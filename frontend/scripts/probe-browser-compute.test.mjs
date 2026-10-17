import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { applyObservations, emptyReport, observeBrowser, probeBrowser, main } from "./probe-browser-compute.mjs";

function observations() {
  return {
    wasm: { api: true, actual: 42, executed: true, correct: true },
    webgl: { api: true, context: true },
    webgpu: { api: true, adapter: true, software: true },
    webnn: { api: true },
  };
}

test("only the fixed WASM operation passes; API, context, and software adapter do not", () => {
  const report = applyObservations(emptyReport("initial", "a".repeat(64)), observations(), "151.0.0.0");
  assert.equal(report.status, "passed");
  assert.equal(report.probes[0].execution.status, "passed");
  assert.equal(report.probes[0].execution.effective, "browser-wasm");
  assert.match(report.probes[0].execution.runtime, /node-controller/);
  for (const probe of report.probes.slice(1)) {
    assert.equal(probe.execution.status, "not-run");
    assert.equal(probe.execution.executed, false);
    assert.equal(probe.execution.effective, null);
    assert.equal(probe.device_enumerated, "unknown");
    assert.equal(probe.device_accessible, "unknown");
  }
  assert.equal(report.probes[2].software_adapter, true);
});

test("a real operation with the wrong answer fails", () => {
  const values = observations();
  values.wasm.actual = 41;
  values.wasm.correct = false;
  const report = applyObservations(emptyReport("initial"), values, "151.0.0.0");
  assert.equal(report.status, "failed");
  assert.equal(report.probes[0].execution.status, "failed");
  assert.equal(report.probes[0].execution.executed, true);
  assert.equal(report.probes[0].execution.correct, false);
});

test("missing browser means not run with unknown hardware", () => {
  const report = emptyReport("chromium-binary-unavailable");
  for (const probe of report.probes) {
    assert.equal(probe.runtime_installed, "unknown");
    assert.equal(probe.device_enumerated, "unknown");
    assert.equal(probe.execution.status, "not-run");
  }
});

test("an unexpected probe failure is not reported as a successful detection", () => {
  const values = observations();
  values.webgpu = { api: true, error: "webgpu-adapter-probe-failed" };
  const report = applyObservations(emptyReport("initial"), values, "151.0.0.0");
  assert.equal(report.status, "failed");
  assert.equal(report.probes[2].execution.status, "not-run");
  assert.equal(report.probes[2].adapter_available, "unknown");
});

test("the WASM bytes execute the expected operation in a CPU runtime", async () => {
  // Exercise the actual bytecode independently from Chromium without replacing
  // WebAssembly. Other globals are synthetic detection fixtures only.
  const documentDescriptor = Object.getOwnPropertyDescriptor(globalThis, "document");
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "document", { configurable: true, value: { createElement: () => ({ getContext: () => null }) } });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  try {
    const result = await observeBrowser();
    assert.equal(result.wasm.actual, 42);
    assert.equal(result.wasm.executed, true);
    assert.equal(result.wasm.correct, true);
    assert.equal(result.webgpu.api, false);
    assert.equal(result.webnn.api, false);
  } finally {
    if (documentDescriptor) Object.defineProperty(globalThis, "document", documentDescriptor);
    else delete globalThis.document;
    if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
    else delete globalThis.navigator;
  }
});

function lifecycle(overrides = {}) {
  const calls = [];
  const operation = (name, value) => async (...args) => {
    calls.push([name, ...args]);
    if (overrides[name]) return overrides[name](...args);
    return value;
  };
  const page = {
    goto: operation("goto"),
    evaluate: operation("evaluate", observations()),
  };
  const context = {
    route: operation("route"),
    newPage: operation("newPage", page),
  };
  const browser = {
    version: () => overrides.version ?? "151.0.0.0",
    newContext: operation("newContext", context),
  };
  const server = {
    wsEndpoint: () => "ws://127.0.0.1/test-only",
    close: operation("close"),
    kill: operation("kill"),
  };
  const chromium = {
    executablePath: () => "/test-only/paired-chromium",
    launchServer: operation("launch", server),
    connect: operation("connect", browser),
  };
  return {
    calls,
    options: {
      loadPlaywright: operation("load", { chromium }),
      accessBinary: operation("access"),
      sourceEnvironment: { PATH: "/test-only/bin", SECRET: "never-forward" },
      observationTimeout: 100,
      cleanupTimeout: 30,
    },
  };
}
const throws = (message) => async () => { throw new Error(message); };
const hangs = () => new Promise(() => {});

for (const [stage, reason] of [["load", "playwright-package-unavailable"], ["access", "chromium-binary-unavailable"]]) {
  for (const required of [false, true]) {
    test(`${stage} unavailable is ${required ? "failed in required installation mode" : "not-run by default"}`, async () => {
      const { calls, options } = lifecycle({ [stage]: throws("private/path") });
      const report = await probeBrowser("a".repeat(64), { ...options, requireBrowser: required });
      assert.equal(report.status, required ? "failed" : "not-run");
      assert.equal(report.reason, reason);
      assert.equal(report.environment_sha256, "a".repeat(64));
      assert.equal(report.binary_source, "paired");
      assert.equal(calls.some(([name]) => name === "launch"), false);
      for (const probe of report.probes) {
        assert.equal(probe.runtime_installed, "unknown");
        assert.equal(probe.execution.executed, false);
        assert.equal(probe.device_enumerated, "unknown");
      }
      assert.doesNotMatch(JSON.stringify(report), /private|test-only/);
    });
  }
}

for (const message of ["No usable sandbox!", "Failed to move to new namespace: Operation not permitted"]) {
  test(`sandbox-specific launch message stays not-run even with required browser: ${message}`, async () => {
    const { calls, options } = lifecycle({ launch: throws(message) });
    const report = await probeBrowser(null, { ...options, requireBrowser: true });
    assert.equal(report.status, "not-run");
    assert.equal(report.reason, "chromium-sandbox-unavailable");
    assert.equal(calls.filter(([name]) => name === "launch").length, 1);
    assert.equal(calls.some(([name]) => name === "connect"), false);
  });
}

for (const message of [
  "unrelated launch error",
  "warning: Operation not permitted\nFATAL: unrelated browser corruption",
  "Running as root without --no-sandbox is not supported",
]) {
  test(`unrelated launch diagnostics fail without retry: ${message}`, async () => {
    const { calls, options } = lifecycle({ launch: throws(message) });
    const report = await probeBrowser(null, options);
    assert.equal(report.status, "failed");
    assert.equal(report.reason, "chromium-launch-failed");
    assert.equal(calls.filter(([name]) => name === "launch").length, 1);
    assert.doesNotMatch(JSON.stringify(report), /corruption|root|unrelated/);
  });
}

test("actual lifecycle keeps sandbox, environment allowlist and fixed-page routing", async () => {
  const { calls, options } = lifecycle();
  const report = await probeBrowser(null, options);
  assert.equal(report.status, "passed");
  assert.equal(report.binary_source, "paired");
  const launchOptions = calls.find(([name]) => name === "launch")[1];
  assert.deepEqual(launchOptions, {
    host: "127.0.0.1", headless: true, executablePath: "/test-only/paired-chromium",
    chromiumSandbox: true, args: ["--disable-setuid-sandbox"],
    env: { PATH: "/test-only/bin", LANG: "C.UTF-8" }, timeout: 15_000,
  });
  assert.deepEqual(calls.find(([name]) => name === "newContext")[1], { serviceWorkers: "block" });
  const routeCall = calls.find(([name]) => name === "route");
  assert.equal(routeCall[1], "**/*");
  for (const [url, expected] of [["https://compute-probe.invalid/", "fulfill"], ["https://elsewhere.invalid/", "abort"], ["https://compute-probe.invalid/path", "abort"]]) {
    let action;
    await routeCall[2]({ request: () => ({ url: () => url }), fulfill: async () => { action = "fulfill"; }, abort: async () => { action = "abort"; } });
    assert.equal(action, expected);
  }
  assert.equal(calls.find(([name]) => name === "goto")[1], "https://compute-probe.invalid/");
  assert.equal(calls.find(([name]) => name === "evaluate")[1], observeBrowser);
  assert.deepEqual(calls.slice(-1).map(([name]) => name), ["close"]);
  assert.equal(calls.some(([name]) => name === "kill"), false);
});

test("override binary source is pathless and uses the override only for access/launch", async () => {
  const { calls, options } = lifecycle();
  options.sourceEnvironment.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH = "/private/browser";
  const report = await probeBrowser(null, options);
  assert.equal(report.binary_source, "override");
  assert.equal(calls.find(([name]) => name === "access")[1], "/private/browser");
  assert.equal(calls.find(([name]) => name === "launch")[1].executablePath, "/private/browser");
  assert.doesNotMatch(JSON.stringify(report), /private|browser\"/);
});

for (const stage of ["connect", "newContext", "route", "newPage", "goto", "evaluate"]) {
  test(`${stage} failure records failed observations and closes the owned server`, async () => {
    const { calls, options } = lifecycle({ [stage]: throws("private failure") });
    const report = await probeBrowser(null, options);
    assert.equal(report.status, "failed");
    assert.equal(report.reason, "browser-observation-failed");
    assert.equal(calls.at(-1)[0], "close");
    assert.equal(calls.some(([name]) => name === "kill"), false);
  });
  test(`${stage} timeout kills the owned server and returns`, { timeout: 500 }, async () => {
    const { calls, options } = lifecycle({ [stage]: hangs });
    const report = await probeBrowser(null, { ...options, observationTimeout: 15 });
    assert.equal(report.status, "failed");
    assert.equal(report.reason, "browser-observation-failed");
    assert.equal(calls.at(-1)[0], "kill");
    assert.equal(calls.some(([name]) => name === "close"), false);
  });
}

test("observation deadline is shared across stages", async () => {
  const pause = () => new Promise((resolve) => setTimeout(resolve, 25));
  const { calls, options } = lifecycle({ route: pause, goto: pause });
  const report = await probeBrowser(null, { ...options, observationTimeout: 40 });
  assert.equal(report.reason, "browser-observation-failed");
  assert.equal(calls.some(([name]) => name === "evaluate"), false);
  assert.equal(calls.at(-1)[0], "kill");
});

for (const close of [throws("private close error"), hangs]) {
  test(`cleanup ${close === hangs ? "timeout" : "failure"} fails and force-kills the owned server`, async () => {
    const { calls, options } = lifecycle({ close });
    const report = await probeBrowser(null, options);
    assert.equal(report.status, "failed");
    assert.equal(report.reason, "browser-cleanup-failed");
    assert.deepEqual(calls.slice(-2).map(([name]) => name), ["close", "kill"]);
  });
}

test("kill failure and timeout are bounded and reported as cleanup failure", async () => {
  for (const kill of [throws("kill error"), hangs]) {
    const { options } = lifecycle({ evaluate: hangs, kill });
    const report = await probeBrowser(null, { ...options, observationTimeout: 10 });
    assert.equal(report.reason, "browser-cleanup-failed");
    assert.equal(report.status, "failed");
  }
});

test("invalid browser version fails observations without leaking it", async () => {
  const { options } = lifecycle({ version: "private-browser-version" });
  const report = await probeBrowser(null, options);
  assert.equal(report.reason, "browser-observation-failed");
  assert.doesNotMatch(JSON.stringify(report), /private/);
});

async function inputs(record = { schema_version: 1, kind: "result", synthetic: false }) {
  const directory = await mkdtemp(join(tmpdir(), "browser-probe-test-"));
  const environment = join(directory, "environment.json");
  const output = join(directory, "browser.json");
  await writeFile(environment, JSON.stringify(record));
  return { environment, output, args: ["--environment", environment, "--output", output] };
}

for (const [label, record] of [
  ["synthetic", { schema_version: 1, kind: "result", synthetic: true }],
  ["wrong kind", { schema_version: 1, kind: "profile", synthetic: false }],
  ["wrong schema", { schema_version: 2, kind: "result", synthetic: false }],
]) {
  test(`main rejects ${label} input before probing`, async () => {
    const { args } = await inputs(record);
    let invoked = false;
    const errors = [];
    const code = await main(args, { probe: () => { invoked = true; }, error: (message) => errors.push(message) });
    assert.equal(code, 2);
    assert.equal(invoked, false);
    assert.deepEqual(errors, ["browser compute: invalid input or output unavailable"]);
  });
}

for (const kind of ["oversized", "nonfile", "malformed-json"]) {
  test(`main rejects ${kind} input before probing`, async () => {
    const { args, environment } = await inputs();
    if (kind === "oversized") await writeFile(environment, " ".repeat(1024 * 1024 + 1));
    if (kind === "nonfile") {
      const directory = `${environment}-directory`;
      await mkdir(directory);
      args[1] = directory;
    }
    if (kind === "malformed-json") await writeFile(environment, "private-invalid-json");
    let invoked = false;
    assert.equal(await main(args, { probe: () => { invoked = true; }, error: () => {} }), 2);
    assert.equal(invoked, false);
  });
}

for (const args of [[], ["--unknown", "private"], ["--environment"], ["--require-browser", "--require-browser"]]) {
  test(`main safely rejects invalid options ${JSON.stringify(args)}`, async () => {
    const errors = [];
    assert.equal(await main(args, { error: (message) => errors.push(message) }), 2);
    assert.deepEqual(errors, ["browser compute: invalid input or output unavailable"]);
  });
}

test("main binds exact environment bytes, required option and status without changing process exitCode", async () => {
  for (const status of ["passed", "not-run", "failed"]) {
    const { args, environment, output } = await inputs();
    let received;
    const prior = process.exitCode;
    const code = await main([...args, "--require-browser"], {
      probe: async (...values) => { received = values; return { status, reason: "test-only" }; }, log: () => {},
    });
    assert.equal(code, status === "failed" ? 1 : 0);
    assert.equal(process.exitCode, prior);
    assert.deepEqual(received, [createHash("sha256").update(await readFile(environment)).digest("hex"), { requireBrowser: true }]);
    assert.equal(JSON.parse(await readFile(output, "utf8")).status, status);
  }
});

test("main exclusively creates output and preserves an existing file", async () => {
  const { args, output } = await inputs();
  await writeFile(output, "existing-evidence");
  assert.equal(await main(args, { probe: async () => emptyReport("test-only"), error: () => {} }), 2);
  assert.equal(await readFile(output, "utf8"), "existing-evidence");
});

test("main reports probe exceptions safely", async () => {
  const { args } = await inputs();
  const errors = [];
  assert.equal(await main(args, { probe: throws("private failure"), error: (message) => errors.push(message) }), 2);
  assert.deepEqual(errors, ["browser compute: invalid input or output unavailable"]);
});

test("CLI invalid input exits 2 with a fixed message", () => {
  const result = spawnSync(process.execPath, [new URL("./probe-browser-compute.mjs", import.meta.url).pathname, "--private-unknown-option"], { encoding: "utf8", timeout: 2_000 });
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr, "browser compute: invalid input or output unavailable\n");
});

// A live child holds Node open. Its SIGSTOP also makes graceful cooperation
// impossible. These exercise the real probe in another Node controller: a
// timeout must force-kill the owned child and allow that controller to exit.
for (const stage of ["evaluate", "close"]) {
  test(`stopped owned child at ${stage} is killed and the Node controller exits`, { skip: process.platform === "win32" }, async () => {
    const directory = await mkdtemp(join(tmpdir(), "browser-probe-child-"));
    const pidFile = join(directory, "child.pid");
    const moduleUrl = new URL("./probe-browser-compute.mjs", import.meta.url).href;
    const script = `
      import { spawn } from "node:child_process";
      import { once } from "node:events";
      import { writeFile } from "node:fs/promises";
      import { probeBrowser } from ${JSON.stringify(moduleUrl)};
      const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
      await once(child, "spawn");
      await writeFile(${JSON.stringify(pidFile)}, String(child.pid));
      process.kill(child.pid, "SIGSTOP");
      const exited = once(child, "exit");
      const hang = () => new Promise(() => {});
      const page = { goto: async () => {}, evaluate: ${stage === "evaluate" ? "hang" : `async () => (${JSON.stringify(observations())})`} };
      const browser = { version: () => "151.0.0.0", newContext: async () => ({ route: async () => {}, newPage: async () => page }) };
      const server = { wsEndpoint: () => "test-only", close: hang, kill: async () => { child.kill("SIGKILL"); await exited; } };
      const chromium = { executablePath: () => "test-only", launchServer: async () => server, connect: async () => browser };
      const report = await probeBrowser(null, { loadPlaywright: async () => ({ chromium }), accessBinary: async () => {}, sourceEnvironment: {}, observationTimeout: 30, cleanupTimeout: 30 });
      console.log(JSON.stringify({ status: report.status, reason: report.reason, signal: child.signalCode }));
    `;
    const controller = spawn(process.execPath, ["--input-type=module", "-e", script], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    controller.stdout.on("data", (data) => { stdout += data; });
    controller.stderr.on("data", (data) => { stderr += data; });
    const timeout = setTimeout(() => controller.kill("SIGKILL"), 3_000);
    let childPid;
    try {
      const [code] = await once(controller, "exit");
      childPid = Number(await readFile(pidFile, "utf8"));
      assert.equal(code, 0, stderr);
      assert.deepEqual(JSON.parse(stdout), {
        status: "failed", reason: stage === "evaluate" ? "browser-observation-failed" : "browser-cleanup-failed", signal: "SIGKILL",
      });
      assert.throws(() => process.kill(childPid, 0), { code: "ESRCH" });
    } finally {
      clearTimeout(timeout);
      // On a failing regression, clean up only the child this fixture created.
      childPid ??= Number(await readFile(pidFile, "utf8").catch(() => "0"));
      if (childPid) { try { process.kill(childPid, "SIGKILL"); } catch {} }
    }
  });
}

for (const [scenario, required, expectedStatus, expectedCode] of [
  ["missing-package", false, "not-run", 0],
  ["missing-package", true, "failed", 1],
  ["missing-binary", false, "not-run", 0],
  ["missing-binary", true, "failed", 1],
  ["sandbox", true, "not-run", 0],
  ["launch-failure", false, "failed", 1],
]) {
  test(`actual CLI ${scenario}${required ? " required" : " optional"} writes evidence and exits ${expectedCode}`, async () => {
    const { environment, output } = await inputs();
    const directory = join(environment, "..");
    const cli = join(directory, "probe-browser-compute.mjs");
    await writeFile(cli, await readFile(new URL("./probe-browser-compute.mjs", import.meta.url)));
    if (scenario !== "missing-package") {
      // A local synthetic package fixture tests actual CLI import resolution;
      // it installs no dependency and never launches a browser.
      const packageDirectory = join(directory, "node_modules/@playwright/test");
      await mkdir(packageDirectory, { recursive: true });
      await writeFile(join(packageDirectory, "package.json"), JSON.stringify({ type: "module", exports: "./index.mjs" }));
      await writeFile(join(packageDirectory, "index.mjs"), `export const chromium = {
        executablePath: () => ${JSON.stringify(scenario === "missing-binary" ? join(directory, "absent-chromium") : process.execPath)},
        launchServer: async () => { throw new Error(${JSON.stringify(scenario === "sandbox" ? "No usable sandbox!" : "Operation not permitted; FATAL unrelated failure")}); }
      };`);
    }
    const result = spawnSync(process.execPath, [cli, "--environment", environment, "--output", output, ...(required ? ["--require-browser"] : [])], {
      encoding: "utf8", timeout: 2_000, env: { PATH: process.env.PATH || "", LANG: "C.UTF-8" },
    });
    assert.equal(result.status, expectedCode, result.stderr);
    const report = JSON.parse(await readFile(output, "utf8"));
    assert.equal(report.status, expectedStatus);
    assert.equal(report.binary_source, "paired");
    assert.equal(result.stdout, `browser compute: ${report.status} (${report.reason})\n`);
    assert.equal(result.stderr, "");
    assert.doesNotMatch(result.stdout, /FATAL|absent-chromium|node_modules/);
  });
}
