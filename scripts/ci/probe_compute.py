#!/usr/bin/env python3
"""Run a standard-library CPU reference and join device observations to a Result.

No driver utility or SDK is executed. PATH tool presence and allowlisted Linux
device-node classes are discovery hints only: they never establish a driver,
runtime, usable device, or accelerator execution. No privileges are changed.
"""

import copy
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import re
import shutil
import stat
import sys


HERE = pathlib.Path(__file__).resolve().parent


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = sibling("collect_environment")
validator = sibling("validate_compatibility")
observed, unknown, not_applicable = collector.observed, collector.unknown, collector.not_applicable
inferred = collector.inferred
TOOL = "tool:probe-compute"
# API catalog, not implemented hardware adapters. Presence tools are not run.
CATALOG = (
    ("cuda", "gpu", "nvidia-smi"), ("rocm", "gpu", "rocminfo"),
    ("oneapi", "gpu", "sycl-ls"), ("level-zero", "gpu", "zeinfo"),
    ("opencl", "gpu", "clinfo"), ("vulkan-compute", "gpu", "vulkaninfo"),
    ("directcompute", "gpu", None), ("directml", "gpu", None),
    ("metal", "gpu", None), ("mps", "gpu", None), ("coreml", "npu", None),
    ("mlx", "gpu", None), ("qnn", "npu", None),
)
BROWSER_APIS = {"wasm": "cpu", "webgl": "gpu", "webgpu": "gpu", "webnn": "npu"}
EXPECTED = [[4, 10], [8, -1]]


def pending_execution(reason):
    return {
        "status": "not-run", "executed": False, "effective": None,
        "correct": unknown(reason), "software_fallback": unknown("no operation ran"),
        "runtime": unknown("no executing runtime"), "process_id": unknown("no executing process"),
        "evidence": [], "reason": reason,
    }


def untested_backend(backend_id, api, kind, reason):
    return {
        "id": backend_id, "kind": kind, "api": api,
        "available": unknown("device usability has not been established"),
        "device": unknown("no device identity is recorded"),
        "version": unknown("runtime version is not observed"),
        "features": unknown("features are not observed"),
        "probe": {
            "tool": "probe-compute",
            "runtime_installed": unknown("no runtime installation probe ran"),
            "api_enumerated": unknown("no API probe ran"),
            "device_enumerated": unknown("no API device enumeration ran"),
            "device_accessible": unknown("no API device access probe ran"),
            "execution": pending_execution(reason),
        },
    }


def device_hints(root=pathlib.Path("/"), system=None):
    """Only coarse class/count information: no device names, serials or addresses."""
    if (system or platform.system()) != "Linux":
        return {"status": "not-run", "reason": "allowlisted device-node hints are Linux-only"}
    out = {}
    for name, patterns in {
        "nvidia": ("dev/nvidia[0-9]*",), "render": ("dev/dri/renderD[0-9]*",),
        "kfd": ("dev/kfd",), "accelerator": ("dev/accel/accel[0-9]*",),
    }.items():
        try:
            nodes = [path for pattern in patterns for path in root.glob(pattern)
                     if stat.S_ISCHR(path.stat().st_mode)]
            out[name] = {"visible_nodes": len(nodes),
                         "read_write_access": sum(os.access(path, os.R_OK | os.W_OK) for path in nodes)}
        except OSError:
            out[name] = {"status": "unknown", "reason": "device-node metadata unavailable"}
    return out


def discover_backends():
    backends = []
    for api, kind, tool in CATALOG:
        backend = untested_backend(api, api, kind, "no hardware kernel adapter is implemented or executed")
        if tool:
            backend["extensions"] = {"x-discovery": {
                "tool": tool, "on_path": shutil.which(tool) is not None,
                "meaning": "tool presence only; driver, runtime, device and execution remain unverified",
            }}
        backends.append(backend)
    for api, kind in BROWSER_APIS.items():
        backends.append(untested_backend(f"browser-{api}", api, kind, "browser probe report was not supplied"))
    return backends


def matrix_product(left, right):
    if not left or not right or any(len(row) != len(right) for row in left):
        raise ValueError("incompatible fixed matrix dimensions")
    columns = len(right[0])
    if not columns or any(len(row) != columns for row in right):
        raise ValueError("inconsistent fixed matrix dimensions")
    return [[sum(row[k] * right[k][column] for k in range(len(right)))
             for column in range(columns)] for row in left]


def cpu_reference(run, evidence, case="success"):
    """Failure modes alter fixed inputs, and still perform the same real operation."""
    if case not in ("success", "mismatch", "error"):
        raise ValueError("unknown CPU case")
    left, right = [[1, -2, 3], [4, 0, -1]], [[2, 1], [-1, 3], [0, 5]]
    if case == "mismatch":
        left[0][0] = 2
    elif case == "error":
        left[0].pop()
    execution = {
        "status": "failed", "executed": False, "effective": None,
        "correct": unknown("the operation has not completed"),
        "software_fallback": unknown("the operation has not completed"),
        "runtime": observed(f"{sys.implementation.name} {platform.python_version()}", "python:sys.implementation", evidence),
        "process_id": observed(os.getpid(), "python:os.getpid", evidence),
        "evidence": [evidence], "run": copy.deepcopy(run),
    }
    log = [{"event": "cpu-reference-start", "requested": "cpu", "case": case,
            "runtime": execution["runtime"]["value"], "process_id": os.getpid(), "run": run,
            "left": left, "right": right, "expected": EXPECTED}]
    try:
        actual = matrix_product(left, right)
    except Exception as exc:
        # Fixed exception category only: exception messages can include host paths.
        execution["reason"] = f"CPU reference operation raised {type(exc).__name__}"
        log.append({"event": "cpu-reference-error", "error_type": type(exc).__name__})
    else:
        correct = actual == EXPECTED
        execution.update(status="passed" if correct else "failed", executed=True, effective="cpu",
                         correct=observed(correct, evidence), software_fallback=observed(False, evidence))
        if not correct:
            execution["reason"] = "CPU reference output differs from the fixed expected matrix"
        log.append({"event": "cpu-reference-completed", "effective": "cpu", "actual": actual,
                    "expected": EXPECTED, "correct": correct})
    backend = {
        "id": "cpu", "kind": "cpu", "api": "cpu", "available": inferred(True, TOOL),
        "device": inferred("process CPU", TOOL), "version": not_applicable("native CPU has no runtime version"),
        "features": inferred(["fixed-integer-matrix-reference"], TOOL),
        "probe": {"tool": "python-standard-library", "runtime_installed": inferred(True, TOOL),
                  "api_enumerated": inferred(True, TOOL), "device_enumerated": inferred(True, TOOL),
                  "device_accessible": inferred(True, TOOL), "execution": execution},
    }
    return backend, log


def stage(status, evidence, effective="cpu", reason=None):
    entry = {"status": status, "compute": {"requested": "cpu", "effective": effective}}
    if status in ("passed", "failed"):
        entry.update(evidence=[evidence], privilege={"escalation": collector.declared("none", TOOL)})
    if reason:
        entry["reason"] = reason
    return entry


def build_result(environment, case="success", log_ref="log:cpu-reference.log"):
    result = copy.deepcopy(environment)
    # These observations belong to this producer, never to collector extensions.
    result.setdefault("extensions", {}).pop("x-browser-probe", None)
    cpu, log = cpu_reference(result["run"], log_ref, case)
    result["compute"] = {"probe_contract": "device-v1", "backends": [cpu, *discover_backends()],
                         "accelerator_inventory": unknown("tool and device-node hints are not a complete hardware inventory")}
    result.setdefault("extensions", {})["x-device-node-hints"] = device_hints()
    result["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Qualify the already installed standard library; this does not install a package.
    log.insert(0, {"event": "cpu-runtime-qualified", "installed": True, "initialized": True,
                   "package_install_performed": False, "runtime": cpu["probe"]["execution"]["runtime"]["value"]})
    for name in ("install", "initialize"):
        result["stages"][name] = stage("passed", log_ref)
    execution = cpu["probe"]["execution"]
    result["stages"]["execute"] = stage("passed" if execution["executed"] else "failed", log_ref,
                                         execution["effective"], None if execution["executed"] else execution["reason"])
    result["stages"]["correctness"] = stage(execution["status"] if execution["executed"] else "blocked",
                                             log_ref, execution["effective"], execution.get("reason"))
    for name in ("security", "performance"):
        result["stages"][name] = {"status": "not-run", "reason": f"CPU reference does not measure {name}"}
    result["overall"] = collector.overall_of(result["stages"])
    return result, log


def check_environment(environment):
    findings = validator.validate_document(environment, validator.load_schema(), evaluate_policy=False, require_real=True)
    if findings.errors or environment["kind"] != "result" or environment["stages"]["detect"]["status"] != "passed":
        raise ValueError("environment must be a valid measured Result with a passed detection stage")
    current = collector.Probe(probe_node=False)
    if environment["run"] != current.run():
        raise ValueError("environment revision or CI workflow/job/run differs from this process")
    runtime = environment["workload"]["runtimes"]["python"]
    if (validator.measured(runtime["implementation"]), validator.measured(runtime["version"])) != (sys.implementation.name, platform.python_version()):
        raise ValueError("environment Python runtime differs from the CPU probe runtime")
    if environment["workload"]["identity"] != current.identity():
        raise ValueError("environment identity differs from the CPU probe identity")


def browser_fact(value, evidence, reason):
    if type(value) is bool:
        return observed(value, evidence)
    if value == "unknown":
        return unknown(reason, evidence)
    raise ValueError("browser observations must be boolean or unknown")


def import_browser(result, report, environment_sha256, artifact_ref):
    """Import only the four known observations, bound to the collector's exact file.

    The browser report records its Node controller PID, explicitly named in the
    runtime fact. It does not claim to know a Chromium renderer's process ID.
    """
    if not isinstance(report, dict):
        raise ValueError("browser report must be an object")
    if (report.get("schema_version"), report.get("kind"), report.get("tool"), report.get("environment_sha256")) != (
            1, "browser-compute-probe", "playwright-chromium", environment_sha256):
        raise ValueError("browser report does not match this environment")
    if report.get("status") not in ("passed", "failed", "not-run"):
        raise ValueError("invalid browser probe status")
    if "binary_source" in report and report["binary_source"] not in ("paired", "override"):
        raise ValueError("invalid browser binary source")
    probes = report.get("probes")
    if not isinstance(probes, list) or len(probes) != len(BROWSER_APIS):
        raise ValueError("browser report must contain exactly the four API observations")
    if not isinstance(report.get("reason"), str) or not re.fullmatch(r"[a-z0-9-]{1,100}", report["reason"]):
        raise ValueError("invalid browser reason")
    if report["status"] != "failed" and report["reason"].endswith("-failed"):
        raise ValueError("a browser report with a failed probe must fail")
    backends = {backend["id"]: backend for backend in result["compute"]["backends"]}
    seen = set()
    for raw in probes:
        if not isinstance(raw, dict) or not isinstance(raw.get("execution"), dict):
            raise ValueError("browser probe and execution must be objects")
        api = raw["api"]
        if api not in BROWSER_APIS or api in seen or (raw["id"], raw["kind"]) != (f"browser-{api}", BROWSER_APIS[api]):
            raise ValueError("unexpected or duplicate browser backend")
        seen.add(api)
        backend = backends[raw["id"]]
        evidence = f"{artifact_ref}#{api}"
        probe = backend["probe"]
        probe["tool"] = "playwright-chromium"
        execution = raw["execution"]
        reason = execution.get("reason")
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9-]{1,100}", reason):
            raise ValueError("invalid browser execution reason")
        for field in ("runtime_installed", "api_enumerated", "device_enumerated", "device_accessible"):
            probe[field] = browser_fact(raw[field], evidence, reason)
        if execution["status"] not in ("passed", "failed", "not-run") or type(execution["executed"]) is not bool:
            raise ValueError("invalid browser operation status")
        if report["status"] == "not-run" and (execution["status"] != "not-run" or execution["executed"]):
            raise ValueError("an unexecuted browser report cannot contain an attempted operation")
        if report["status"] == "not-run":
            unknown_fields = ("runtime_installed", "api_enumerated", "device_enumerated", "device_accessible",
                              "context_available", "adapter_available", "software_adapter")
            if (any(raw.get(field, "unknown") != "unknown" for field in unknown_fields)
                    or execution.get("correct") != "unknown" or execution.get("software_fallback") != "unknown"
                    or any(execution.get(field) is not None for field in ("effective", "runtime", "process_id"))
                    or execution.get("evidence") or raw.get("evidence") or raw.get("actual") is not None):
                raise ValueError("an unexecuted browser report cannot contain observed browser facts")
        if report["status"] == "passed" and execution["status"] == "failed":
            raise ValueError("a browser report with a failed operation cannot pass")
        # Detection failures leave GPU/NPU operation status not-run. Their
        # producer reason still records a failure of the browser probe.
        if report["status"] != "failed" and reason.endswith("-failed"):
            raise ValueError("a browser report with a failed probe must fail")
        if api != "wasm" and (execution["status"] != "not-run" or execution["executed"]):
            raise ValueError("this browser probe performs no GPU or NPU kernel")
        runtime, pid = execution.get("runtime"), execution.get("process_id")
        if runtime is not None:
            match = re.fullmatch(r"chromium [0-9]+(?:\.[0-9]+){1,3}; node-controller ([0-9]+(?:\.[0-9]+){2})", runtime) if isinstance(runtime, str) else None
            if not match:
                raise ValueError("invalid browser runtime")
            node_version = validator.measured(result["workload"]["runtimes"]["node"]["version"])
            if node_version is None or match.group(1) != node_version:
                raise ValueError("browser controller runtime differs from the measured environment Node runtime")
        elif (report["status"] == "passed" or execution["status"] != "not-run"
              or any(type(raw.get(field)) is bool for field in (
                  "runtime_installed", "api_enumerated", "device_enumerated", "device_accessible",
                  "context_available", "adapter_available", "software_adapter"))):
            raise ValueError("browser observations require a controller runtime")
        if pid is not None and (type(pid) is not int or not 1 <= pid <= 4294967295):
            raise ValueError("invalid browser controller PID")
        converted = {
            "status": execution["status"], "executed": execution["executed"], "effective": execution["effective"],
            "correct": browser_fact(execution["correct"], evidence, reason),
            "software_fallback": browser_fact(execution["software_fallback"], evidence, reason),
            "runtime": observed(runtime, evidence) if runtime else unknown(reason),
            "process_id": observed(pid, evidence) if pid else unknown(reason),
            "evidence": [evidence] if execution["status"] in ("passed", "failed") else [], "reason": reason,
        }
        if execution["status"] in ("passed", "failed"):
            converted["run"] = copy.deepcopy(result["run"])
        probe["execution"] = converted
        if api == "wasm" and execution["executed"]:
            if type(raw.get("expected")) is not int or raw["expected"] != 42 or type(raw.get("actual")) is not int or execution["correct"] is not (raw["actual"] == 42):
                raise ValueError("WASM correctness does not match its fixed arithmetic result")
            if execution["effective"] != raw["id"]:
                raise ValueError("WASM operation must record its actual WASM backend")
            backend["available"] = observed(True, evidence)
            backend["device"] = observed("browser CPU execution", evidence)
        # Context and adapter discovery are useful, but cannot establish physical hardware.
        observations = {}
        for field in ("context_available", "adapter_available", "software_adapter"):
            if field in raw:
                observations[field] = browser_fact(raw[field], evidence, reason)
        if observations:
            backend["extensions"] = {"x-browser-observations": observations}
    result.setdefault("extensions", {})["x-browser-probe"] = {
        "status": report["status"], "reason": report["reason"], "environment_sha256": environment_sha256,
    }
    if "binary_source" in report:
        result["extensions"]["x-browser-probe"]["binary_source"] = report["binary_source"]


def main(argv=None):
    parser = validator.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    for name in ("environment", "output", "report", "log"):
        parser.add_argument(f"--{name}", type=pathlib.Path, required=True)
    parser.add_argument("--browser-report", type=pathlib.Path)
    parser.add_argument("--cpu-case", choices=("success", "mismatch", "error"), default="success")
    parser.add_argument("--summary", type=pathlib.Path)
    args = parser.parse_args(argv)
    paths = [path for path in (args.environment, args.output, args.report, args.log, args.browser_report, args.summary) if path]
    if any(validator.same_place(left, right) for index, left in enumerate(paths) for right in paths[index + 1:]):
        parser.error("input, output, report, log and summary must be separate files")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", path.name) for path in (args.log, args.report, args.browser_report) if path):
        parser.error("artifact filenames must use short ASCII letters, digits, dots, underscores or hyphens")
    try:
        with args.environment.open("rb") as stream:
            environment_bytes = stream.read(validator.MAX_DOCUMENT_BYTES + 1)
        environment = validator.load_json_bytes(environment_bytes)
        check_environment(environment)
        result, log = build_result(environment, args.cpu_case, f"log:{args.log.name}")
        if args.browser_report:
            import_browser(result, validator.load_json(args.browser_report),
                           hashlib.sha256(environment_bytes).hexdigest(), f"artifact:{args.browser_report.name}")
        findings = validator.validate_document(result, validator.load_schema(), evaluate_policy=False, require_real=True)
        if findings.errors:
            raise ValueError("generated compute Result violates the shared contract")
        report = {"schema_version": 1, "kind": "compute-probe", "synthetic": False, "run": result["run"],
                  "compute": result["compute"], "device_node_hints": result["extensions"]["x-device-node-hints"],
                  "browser_probe": result["extensions"].get("x-browser-probe")}
        args.log.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in log), encoding="utf-8")
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        cpu = result["compute"]["backends"][0]["probe"]["execution"]
        lines = ["### Device and compute probe", "", f"CPU execution: {result['stages']['execute']['status']}; correctness: {result['stages']['correctness']['status']}.",
                 "", "| API | Execution |", "|---|---|"]
        lines += [f"| {backend['api']} | {backend['probe']['execution']['status']} |" for backend in result["compute"]["backends"]]
        browser = result["extensions"].get("x-browser-probe")
        browser_summary = f"Browser probe: {browser['status']}; reason={browser['reason']}" if browser else "Browser probe: not-run; reason=browser-report-not-supplied"
        lines += ["", browser_summary + "."]
        lines += ["", "Native CPU fixed-matrix verification is pipeline evidence, not LLM inference or accelerator qualification.", ""]
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as stream:
                stream.write("\n".join(lines))
        print(f"CPU reference: {cpu['status']}; requested=cpu; effective={cpu['effective']}; correctness={cpu['correct']['value']}")
        print(browser_summary)
        browser_failed = result["extensions"].get("x-browser-probe", {}).get("status") == "failed"
        return int(browser_failed or any(backend["probe"]["execution"]["status"] == "failed" for backend in result["compute"]["backends"]))
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(f"compute probe could not produce a valid result ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
