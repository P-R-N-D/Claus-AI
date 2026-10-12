#!/usr/bin/env node
// Optional browser observations. Only WASM executes an operation; the other
// probes never qualify GPU/NPU hardware, including software/fallback adapters.
import { createHash } from "node:crypto";
import { access, readFile, stat, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const ORIGIN = "https://compute-probe.invalid/";
const APIS = ["wasm", "webgl", "webgpu", "webnn"];

export function emptyReport(reason, environmentSha256 = null) {
  return {
    schema_version: 1,
    kind: "browser-compute-probe",
    tool: "playwright-chromium",
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

async function bounded(promise, milliseconds) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("probe-timeout")), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function probeBrowser(environmentSha256) {
  const report = emptyReport("browser-not-started", environmentSha256);
  let chromium;
  try {
    ({ chromium } = await import("@playwright/test"));
  } catch {
    return emptyReport("playwright-package-unavailable", environmentSha256);
  }
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || chromium.executablePath();
  try {
    await access(executablePath);
  } catch {
    return emptyReport("chromium-binary-unavailable", environmentSha256);
  }
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath,
      chromiumSandbox: true,
      args: ["--disable-setuid-sandbox"],
      env: { PATH: process.env.PATH || "", LANG: "C.UTF-8" },
      timeout: 15_000,
    });
  } catch (error) {
    // Do not print Chromium stderr: it can include user paths or identifiers.
    if (/No usable sandbox|Failed to move to new namespace|Operation not permitted/.test(error.message)) {
      return emptyReport("chromium-sandbox-unavailable", environmentSha256);
    }
    report.status = "failed";
    report.reason = "chromium-launch-failed";
    for (const probe of report.probes) probe.execution.reason = report.reason;
    return report;
  }
  try {
    const version = browser.version();
    if (!/^\d+(?:\.\d+){1,3}$/.test(version)) throw new Error("invalid-browser-version");
    const context = await browser.newContext({ serviceWorkers: "block" });
    await context.route("**/*", (route) => route.request().url() === ORIGIN
      ? route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Compute probe</title>" })
      : route.abort());
    const page = await context.newPage();
    await page.goto(ORIGIN, { waitUntil: "domcontentloaded", timeout: 10_000 });
    applyObservations(report, await bounded(page.evaluate(observeBrowser), 15_000), version);
  } catch {
    report.status = "failed";
    report.reason = "browser-observation-failed";
    for (const probe of report.probes) probe.execution.reason = report.reason;
  } finally {
    try {
      await bounded(browser.close(), 5_000);
    } catch {
      report.status = "failed";
      report.reason = "browser-cleanup-failed";
    }
  }
  return report;
}

async function main(args) {
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index];
    if (!["--environment", "--output"].includes(name) || !args[index + 1] || options[name]) {
      throw new Error("invalid-options");
    }
    options[name] = args[index + 1];
  }
  if (!options["--environment"] || !options["--output"]) throw new Error("missing-options");
  const metadata = await stat(options["--environment"]);
  if (!metadata.isFile() || metadata.size > 1024 * 1024) throw new Error("invalid-environment-file");
  const environment = await readFile(options["--environment"]);
  const record = JSON.parse(environment.toString("utf8"));
  if (record.schema_version !== 1 || record.kind !== "result" || record.synthetic !== false) {
    throw new Error("expected-measured-environment");
  }
  const report = await probeBrowser(createHash("sha256").update(environment).digest("hex"));
  await writeFile(options["--output"], `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
  console.log(`browser compute: ${report.status} (${report.reason})`);
  process.exitCode = report.status === "failed" ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(() => {
    // Never echo caller-supplied arguments, paths, JSON, or error messages.
    console.error("browser compute: invalid input or output unavailable");
    process.exitCode = 2;
  });
}
