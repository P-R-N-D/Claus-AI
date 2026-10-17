#!/usr/bin/env node
// Optional browser observations. Only WASM executes an operation; the other
// probes never qualify GPU/NPU hardware, including software/fallback adapters.
import { createHash } from "node:crypto";
import { access, readFile, stat, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const ORIGIN = "https://compute-probe.invalid/";
const APIS = ["wasm", "webgl", "webgpu", "webnn"];

export function emptyReport(reason, environmentSha256 = null, binarySource = "paired") {
  return {
    schema_version: 1,
    kind: "browser-compute-probe",
    tool: "playwright-chromium",
    binary_source: binarySource,
    environment_sha256: environmentSha256,
    status: "not-run",
    reason,
    probes: APIS.map((api) => ({
      id: `browser-${api}`,
      api,
      kind: api === "wasm" ? "cpu" : api === "webnn" ? "npu" : "gpu",
      runtime_installed: "unknown",
      api_enumerated: "unknown",
      device_enumerated: "unknown",
      device_accessible: "unknown",
      execution: {
        status: "not-run",
        executed: false,
        effective: null,
        correct: "unknown",
        software_fallback: "unknown",
        runtime: null,
        process_id: null,
        evidence: [],
        reason,
      },
      evidence: [],
    })),
  };
}

// Self-contained: Playwright serializes this function into the blank page.
export async function observeBrowser() {
  const observations = {};
  const wasm = (observations.wasm = { api: typeof WebAssembly === "object" });
  if (wasm.api) {
    try {
      // (module (func (export "add") (param i32 i32) (result i32)
      //   local.get 0 local.get 1 i32.add))
      const bytes = new Uint8Array([
        0, 97, 115, 109, 1, 0, 0, 0, 1, 7, 1, 96, 2, 127, 127, 1, 127,
        3, 2, 1, 0, 7, 7, 1, 3, 97, 100, 100, 0, 0,
        10, 9, 1, 7, 0, 32, 0, 32, 1, 106, 11,
      ]);
      const { instance } = await WebAssembly.instantiate(bytes);
      wasm.actual = instance.exports.add(20, 22);
      wasm.executed = true;
      wasm.correct = wasm.actual === 42;
    } catch {
      wasm.error = "wasm-operation-failed";
    }
  }
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl");
    observations.webgl = {
      api: typeof WebGLRenderingContext === "function",
      context: context !== null,
    };
    context?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    observations.webgl = { error: "webgl-context-probe-failed" };
  }
  observations.webgpu = { api: typeof navigator.gpu === "object" };
  if (observations.webgpu.api) {
    let timer;
    try {
      const adapter = await Promise.race([
        navigator.gpu.requestAdapter(),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("timeout")), 5_000);
        }),
      ]);
      observations.webgpu.adapter = adapter !== null;
      // Read only the standard fallback boolean, never vendor, model, IDs,
      // renderer strings, driver descriptions or device identifiers.
      observations.webgpu.software =
        typeof adapter?.info?.isFallbackAdapter === "boolean"
          ? adapter.info.isFallbackAdapter
          : typeof adapter?.isFallbackAdapter === "boolean"
            ? adapter.isFallbackAdapter
            : "unknown";
    } catch {
      observations.webgpu.error = "webgpu-adapter-probe-failed";
    } finally {
      clearTimeout(timer);
    }
  }
  observations.webnn = { api: typeof navigator.ml === "object" };
  return observations;
}

export function applyObservations(report, observations, browserVersion) {
  // The process ID names the Node controller, not a Chromium renderer.
  const runtime = `chromium ${browserVersion}; node-controller ${process.versions.node}`;
  report.status = "passed";
  report.reason = "capability-probes-completed";
  for (const probe of report.probes) {
    const observation = observations[probe.api];
    probe.runtime_installed = true;
    probe.api_enumerated = observation.api ?? "unknown";
    probe.evidence = [`tool:playwright-chromium:${probe.api}`];
    probe.execution.runtime = runtime;
    probe.execution.process_id = process.pid;
    probe.execution.evidence = probe.evidence;
    if (probe.api === "wasm") {
      const ran = observation.executed === true;
      probe.device_enumerated = ran ? true : "unknown";
      probe.device_accessible = ran ? true : "unknown";
      Object.assign(probe.execution, {
        status: observation.error || (ran && !observation.correct)
          ? "failed" : ran ? "passed" : "not-run",
        executed: ran,
        effective: ran ? probe.id : null,
        correct: ran ? observation.correct : "unknown",
        software_fallback: ran ? false : "unknown",
        reason: observation.error ?? (ran ? "fixed-wasm-addition" : "wasm-api-unavailable"),
      });
      probe.expected = 42;
      probe.actual = ran ? observation.actual : null;
    } else {
      probe.execution.reason = observation.error ?? `${probe.api}-detection-only-no-kernel`;
      // A context or an adapter can be software. Neither proves a usable
      // physical device; WebNN API presence cannot identify an NPU either.
      if (probe.api === "webgl") probe.context_available = observation.context ?? "unknown";
      if (probe.api === "webgpu") {
        probe.adapter_available = observation.adapter ?? "unknown";
        probe.software_adapter = observation.software ?? "unknown";
      }
    }
    if (observation.error || probe.execution.status === "failed") {
      report.status = "failed";
      report.reason = "browser-probe-failed";
    }
  }
  return report;
}

class ProbeTimeout extends Error {}

async function bounded(operation, milliseconds) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new ProbeTimeout()), Math.max(0, milliseconds));
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function failed(report, reason) {
  report.status = "failed";
  report.reason = reason;
  for (const probe of report.probes) probe.execution.reason = reason;
  return report;
}

// Dependencies and short deadlines are injectable for lifecycle tests. Production
// uses only the existing locked package, filesystem access and bounded defaults.
export async function probeBrowser(environmentSha256, {
  loadPlaywright = () => import("@playwright/test"),
  accessBinary = access,
  sourceEnvironment = process.env,
  requireBrowser = false,
  observationTimeout = 15_000,
  cleanupTimeout = 5_000,
} = {}) {
  const binarySource = sourceEnvironment.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? "override" : "paired";
  const report = emptyReport("browser-not-started", environmentSha256, binarySource);
  const unavailable = (reason) => requireBrowser ? failed(report, reason)
    : emptyReport(reason, environmentSha256, binarySource);
  let chromium;
  try {
    ({ chromium } = await loadPlaywright());
  } catch {
    return unavailable("playwright-package-unavailable");
  }
  const executablePath = sourceEnvironment.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || chromium.executablePath();
  try {
    await accessBinary(executablePath);
  } catch {
    return unavailable("chromium-binary-unavailable");
  }
  let server;
  try {
    server = await chromium.launchServer({
      host: "127.0.0.1",
      headless: true,
      executablePath,
      chromiumSandbox: true,
      args: ["--disable-setuid-sandbox"],
      env: { PATH: sourceEnvironment.PATH || "", LANG: "C.UTF-8" },
      timeout: 15_000,
    });
  } catch (error) {
    // Only sandbox-specific diagnostics establish this optional limitation.
    // Generic EPERM noise or root-without-sandbox errors remain launch failures.
    // Never expose Chromium stderr, user paths, or identifiers.
    if (/No usable sandbox|Failed to move to new namespace/.test(error?.message ?? "")) {
      return emptyReport("chromium-sandbox-unavailable", environmentSha256, binarySource);
    }
    return failed(report, "chromium-launch-failed");
  }
  let timedOut = false;
  try {
    // One wall-clock budget covers connection and every observation operation,
    // including newContext/route/newPage, rather than just evaluate().
    const deadline = performance.now() + observationTimeout;
    const withinDeadline = (operation) => bounded(operation, deadline - performance.now());
    const browser = await withinDeadline(() => chromium.connect(server.wsEndpoint(), { timeout: observationTimeout }));
    const version = browser.version();
    if (!/^\d+(?:\.\d+){1,3}$/.test(version)) throw new Error("invalid-browser-version");
    const context = await withinDeadline(() => browser.newContext({ serviceWorkers: "block" }));
    await withinDeadline(() => context.route("**/*", (route) => route.request().url() === ORIGIN
      ? route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Compute probe</title>" })
      : route.abort()));
    const page = await withinDeadline(() => context.newPage());
    await withinDeadline(() => page.goto(ORIGIN, { waitUntil: "domcontentloaded", timeout: 10_000 }));
    applyObservations(report, await withinDeadline(() => page.evaluate(observeBrowser)), version);
  } catch (error) {
    timedOut = error instanceof ProbeTimeout;
    failed(report, "browser-observation-failed");
  } finally {
    // A timed-out Chromium may be stopped and cannot answer a graceful close.
    // BrowserServer.kill() force-kills only Playwright's own process tree and
    // waits for exit, also closing its loopback transport and Node handles.
    if (timedOut) {
      await bounded(() => server.kill(), cleanupTimeout).catch(() => {
        failed(report, "browser-cleanup-failed");
      });
    } else {
      try {
        await bounded(() => server.close(), cleanupTimeout);
      } catch {
        failed(report, "browser-cleanup-failed");
        await bounded(() => server.kill(), cleanupTimeout).catch(() => {});
      }
    }
  }
  return report;
}

export async function main(args, {
  probe = probeBrowser,
  log = (message) => console.log(message),
  error = (message) => console.error(message),
} = {}) {
  try {
    const options = {};
    for (let index = 0; index < args.length;) {
      const name = args[index++];
      if (name === "--require-browser" && !options[name]) {
        options[name] = true;
      } else if (["--environment", "--output"].includes(name) && args[index] && !options[name]) {
        options[name] = args[index++];
      } else {
        throw new Error("invalid-options");
      }
    }
    if (!options["--environment"] || !options["--output"]) throw new Error("missing-options");
    const metadata = await stat(options["--environment"]);
    if (!metadata.isFile() || metadata.size > 1024 * 1024) throw new Error("invalid-environment-file");
    const environment = await readFile(options["--environment"]);
    const record = JSON.parse(environment.toString("utf8"));
    if (record.schema_version !== 1 || record.kind !== "result" || record.synthetic !== false) {
      throw new Error("expected-measured-environment");
    }
    const report = await probe(createHash("sha256").update(environment).digest("hex"), {
      requireBrowser: options["--require-browser"] === true,
    });
    await writeFile(options["--output"], `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
    log(`browser compute: ${report.status} (${report.reason})`);
    return report.status === "failed" ? 1 : 0;
  } catch {
    // Never echo caller-supplied arguments, paths, JSON, or error messages.
    error("browser compute: invalid input or output unavailable");
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2));
}
