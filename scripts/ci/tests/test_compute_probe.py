"""CPU reference, real CLI and discovery regression tests; standard library only.

    python -I scripts/ci/tests/test_compute_probe.py --verbose
"""

import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "probe_compute.py"
spec = importlib.util.spec_from_file_location("probe_compute", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CPUReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = probe.collector.build_result(probe.collector.Probe(probe_node=False))
        cls.schema = probe.validator.load_schema()

    def validate(self, result):
        # This environment's container engine may be invisible; these tests exercise
        # the compute contract, while the real workflow applies the strict policy.
        findings = probe.validator.validate_document(result, self.schema, evaluate_policy=False, require_real=True)
        self.assertEqual(findings.errors, [], findings.errors)

    def test_real_cpu_operation_matches_fixed_expected_values(self):
        result, log = probe.build_result(self.environment)
        self.validate(result)
        self.assertEqual(log[-1]["actual"], [[4, 10], [8, -1]])
        self.assertTrue(log[-1]["correct"])
        execution = result["compute"]["backends"][0]["probe"]["execution"]
        self.assertEqual(execution["status"], "passed")
        self.assertTrue(execution["executed"])
        self.assertEqual(execution["effective"], "cpu")
        self.assertGreater(execution["process_id"]["value"], 0)
        self.assertEqual(execution["run"], result["run"])
        self.assertEqual(result["stages"]["correctness"]["compute"], {"requested": "cpu", "effective": "cpu"})
        self.assertFalse(log[0]["package_install_performed"])

    def test_changed_input_runs_but_fails_correctness(self):
        result, log = probe.build_result(self.environment, "mismatch")
        self.validate(result)
        self.assertNotEqual(log[-1]["actual"], probe.EXPECTED)
        self.assertFalse(log[-1]["correct"])
        self.assertEqual(result["stages"]["execute"]["status"], "passed")
        self.assertEqual(result["stages"]["correctness"]["status"], "failed")
        self.assertEqual(result["overall"], "failed")

    def test_bad_dimensions_fail_execution_and_block_correctness(self):
        result, log = probe.build_result(self.environment, "error")
        self.validate(result)
        self.assertEqual(result["stages"]["execute"]["status"], "failed")
        self.assertEqual(result["stages"]["correctness"]["status"], "blocked")
        execution = result["compute"]["backends"][0]["probe"]["execution"]
        self.assertFalse(execution["executed"])
        self.assertIsNone(execution["effective"])
        self.assertIsNone(execution["correct"]["value"])
        self.assertEqual(log[-1], {"event": "cpu-reference-error", "error_type": "ValueError"})

    def test_unexpected_error_is_failed_without_exception_message(self):
        with mock.patch.object(probe, "matrix_product", side_effect=RuntimeError("private-host-value")):
            result, log = probe.build_result(self.environment)
        self.validate(result)
        self.assertEqual(result["stages"]["execute"]["status"], "failed")
        self.assertNotIn("private-host-value", json.dumps([result, log]))

    def test_discovery_tool_presence_is_not_runtime_or_device_availability(self):
        for present in (None, "/private/path/nvidia-smi"):
            with self.subTest(present=bool(present)), mock.patch.object(probe.shutil, "which", return_value=present):
                backends = probe.discover_backends()
            cuda = next(backend for backend in backends if backend["id"] == "cuda")
            self.assertEqual(cuda["extensions"]["x-discovery"]["on_path"], present is not None)
            for backend in backends:
                self.assertIsNone(backend["available"]["value"])
                self.assertIsNone(backend["probe"]["runtime_installed"]["value"])
                self.assertEqual(backend["probe"]["execution"]["status"], "not-run")
            self.assertNotIn("/private", json.dumps(backends))

    def test_catalog_covers_requested_apis_without_claiming_execution(self):
        apis = {backend["api"] for backend in probe.discover_backends()}
        self.assertEqual(apis, {"cuda", "rocm", "oneapi", "level-zero", "opencl", "vulkan-compute",
                               "directcompute", "directml", "metal", "mps", "coreml", "mlx", "qnn",
                               "wasm", "webgl", "webgpu", "webnn"})

    def test_missing_nodes_leave_hardware_inventory_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            hints = probe.device_hints(pathlib.Path(directory), "Linux")
        self.assertTrue(all(value["visible_nodes"] == 0 for value in hints.values()))
        result, _ = probe.build_result(self.environment)
        self.assertIsNone(result["compute"]["accelerator_inventory"]["value"])

    def test_stale_revision_is_rejected(self):
        environment = copy.deepcopy(self.environment)
        environment["run"]["revision"]["tested_sha"] = probe.observed("1" * 40, "git:.git/HEAD")
        with self.assertRaisesRegex(ValueError, "revision or CI"):
            probe.check_environment(environment)

    def test_synthetic_environment_cannot_be_submitted_as_measurement(self):
        environment = copy.deepcopy(self.environment)
        environment["synthetic"] = True
        with self.assertRaisesRegex(ValueError, "measured Result"):
            probe.check_environment(environment)

    def test_runtime_changed_after_collection_is_rejected(self):
        environment = copy.deepcopy(self.environment)
        environment["workload"]["runtimes"]["python"]["version"]["value"] = "3.12.1"
        with self.assertRaises(ValueError):
            probe.check_environment(environment)


class CLITests(unittest.TestCase):
    def cli(self, directory, case="success", extra=()):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--environment", str(directory / "environment.json"),
                               "--output", str(directory / "result.json"), "--report", str(directory / "compute-probe.json"),
                               "--log", str(directory / "cpu-reference.log"), "--cpu-case", case, *extra],
                              cwd=ROOT, capture_output=True, text=True, timeout=30)

    def write_environment(self, directory):
        environment = probe.collector.build_result(probe.collector.Probe(probe_node=False))
        (directory / "environment.json").write_text(json.dumps(environment), encoding="utf-8")

    def test_cli_success_and_negative_inputs_write_real_artifacts(self):
        for case, expected_status in (("success", 0), ("mismatch", 1), ("error", 1)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = pathlib.Path(temporary)
                self.write_environment(directory)
                run = self.cli(directory, case)
                self.assertEqual(run.returncode, expected_status, run.stderr + run.stdout)
                result = json.loads((directory / "result.json").read_text())
                report = json.loads((directory / "compute-probe.json").read_text())
                log = [json.loads(line) for line in (directory / "cpu-reference.log").read_text().splitlines()]
                self.assertEqual(report["compute"], result["compute"])
                self.assertEqual(log[1]["run"], result["run"])
                self.assertEqual(result["stages"]["correctness"]["status"], {"success": "passed", "mismatch": "failed", "error": "blocked"}[case])

    def test_cli_does_not_overwrite_its_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            self.write_environment(directory)
            before = (directory / "environment.json").read_bytes()
            run = self.cli(directory, extra=("--output", str(directory / "environment.json")))
            self.assertEqual(run.returncode, 2)
            self.assertEqual((directory / "environment.json").read_bytes(), before)

    def test_cli_does_not_rewrite_result_with_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            self.write_environment(directory)
            run = self.cli(directory, extra=("--summary", str(directory / "result.json")))
            self.assertEqual(run.returncode, 2)
            self.assertFalse((directory / "result.json").exists())

    def test_cli_malformed_browser_objects_fail_without_tracebacks(self):
        sample = BrowserImportTests()
        sample.setUp()
        malformed_execution = copy.deepcopy(sample.report)
        malformed_execution["probes"][0]["execution"] = None
        malformed_probe = copy.deepcopy(sample.report)
        malformed_probe["probes"][0] = None
        for report in ([], malformed_execution, malformed_probe):
            with self.subTest(report_type=type(report).__name__), tempfile.TemporaryDirectory() as temporary:
                directory = pathlib.Path(temporary)
                self.write_environment(directory)
                if isinstance(report, dict):
                    report["environment_sha256"] = probe.hashlib.sha256((directory / "environment.json").read_bytes()).hexdigest()
                browser = directory / "browser.json"
                browser.write_text(json.dumps(report), encoding="utf-8")
                run = self.cli(directory, extra=("--browser-report", str(browser)))
                self.assertEqual(run.returncode, 2)
                self.assertNotIn("Traceback", run.stderr)
                self.assertNotIn(str(directory), run.stderr)
                self.assertFalse((directory / "result.json").exists())


class BrowserImportTests(unittest.TestCase):
    def setUp(self):
        environment = probe.collector.build_result(probe.collector.Probe(probe_node=False))
        self.result, _ = probe.build_result(environment)
        self.report = {
            "schema_version": 1, "kind": "browser-compute-probe", "tool": "playwright-chromium",
            "environment_sha256": "a" * 64, "status": "passed", "reason": "capability-probes-completed",
            "probes": [{
                "id": f"browser-{api}", "api": api, "kind": kind,
                "runtime_installed": True, "api_enumerated": True,
                "device_enumerated": True if api == "wasm" else "unknown",
                "device_accessible": True if api == "wasm" else "unknown",
                "execution": {
                    "status": "passed" if api == "wasm" else "not-run", "executed": api == "wasm",
                    "effective": "browser-wasm" if api == "wasm" else None,
                    "correct": True if api == "wasm" else "unknown",
                    "software_fallback": False if api == "wasm" else "unknown",
                    "runtime": "chromium 150.0.1.2; node-controller 24.19.0", "process_id": 123,
                    "evidence": [f"tool:playwright-chromium:{api}"], "reason": "fixed-operation-or-discovery",
                }, "expected": 42, "actual": 42,
            } for api, kind in probe.BROWSER_APIS.items()],
        }

    def import_report(self):
        probe.import_browser(self.result, self.report, "a" * 64, "artifact:browser-compute.json")

    def test_wasm_import_preserves_binding_and_does_not_promote_gpu(self):
        self.report["probes"][2].update(adapter_available=True, software_adapter=True)
        self.import_report()
        findings = probe.validator.validate_document(self.result, probe.validator.load_schema(), evaluate_policy=False, require_real=True)
        self.assertEqual(findings.errors, [])
        backends = {backend["id"]: backend for backend in self.result["compute"]["backends"]}
        wasm = backends["browser-wasm"]["probe"]["execution"]
        self.assertEqual(wasm["status"], "passed")
        self.assertEqual(wasm["run"], self.result["run"])
        self.assertEqual(wasm["evidence"], ["artifact:browser-compute.json#wasm"])
        webgpu = backends["browser-webgpu"]
        self.assertIsNone(webgpu["available"]["value"])
        self.assertEqual(webgpu["probe"]["execution"]["status"], "not-run")
        self.assertEqual(webgpu["probe"]["execution"]["evidence"], [])
        self.assertTrue(webgpu["extensions"]["x-browser-observations"]["software_adapter"]["value"])

    def test_report_from_another_environment_is_rejected(self):
        self.report["environment_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "match this environment"):
            self.import_report()

    def test_claimed_wasm_pass_requires_actual_correct_answer(self):
        self.report["probes"][0]["actual"] = 41
        with self.assertRaisesRegex(ValueError, "fixed arithmetic"):
            self.import_report()

    def test_gpu_detection_cannot_be_imported_as_execution(self):
        self.report["probes"][2]["execution"].update(status="passed", executed=True, effective="browser-webgpu", correct=True)
        with self.assertRaisesRegex(ValueError, "no GPU or NPU kernel"):
            self.import_report()

    def test_not_run_browser_report_cannot_promote_wasm_execution(self):
        self.report["status"] = "not-run"
        with self.assertRaisesRegex(ValueError, "unexecuted browser report"):
            self.import_report()

    def test_passed_browser_report_cannot_contain_failed_operation(self):
        self.report["probes"][0]["execution"].update(status="failed", correct=False)
        self.report["probes"][0]["actual"] = 41
        with self.assertRaisesRegex(ValueError, "failed operation cannot pass"):
            self.import_report()

    def test_malicious_runtime_string_is_not_written(self):
        self.report["probes"][0]["execution"]["runtime"] = "private host and secret"
        with self.assertRaisesRegex(ValueError, "browser runtime"):
            self.import_report()

    def test_duplicate_browser_backend_rejected(self):
        self.report["probes"][3] = copy.deepcopy(self.report["probes"][2])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.import_report()

    def test_browser_not_run_reason_remains_unexecuted(self):
        self.report.update(status="not-run", reason="chromium-sandbox-unavailable")
        for raw in self.report["probes"]:
            for field in ("runtime_installed", "api_enumerated", "device_enumerated", "device_accessible"):
                raw[field] = "unknown"
            raw["execution"].update(status="not-run", executed=False, effective=None, correct="unknown", software_fallback="unknown",
                                    runtime=None, process_id=None, evidence=[], reason="chromium-sandbox-unavailable")
            raw["actual"] = None
        self.import_report()
        self.assertEqual(self.result["extensions"]["x-browser-probe"]["status"], "not-run")
        for backend in self.result["compute"]["backends"]:
            if backend["id"].startswith("browser-"):
                self.assertEqual(backend["probe"]["execution"]["status"], "not-run")

    def test_not_run_browser_report_cannot_contain_observed_api_facts(self):
        self.report.update(status="not-run", reason="chromium-sandbox-unavailable")
        for raw in self.report["probes"]:
            raw["execution"].update(status="not-run", executed=False, effective=None, correct="unknown", software_fallback="unknown",
                                    runtime=None, process_id=None, evidence=[])
            raw["actual"] = None
        with self.assertRaisesRegex(ValueError, "observed browser facts"):
            self.import_report()


if __name__ == "__main__":
    unittest.main()
