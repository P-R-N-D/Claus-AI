import assert from "node:assert/strict";
import test from "node:test";
import { applyObservations, emptyReport, observeBrowser } from "./probe-browser-compute.mjs";

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
