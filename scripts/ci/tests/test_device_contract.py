"""Device evidence regression inputs; these fabricated records are not run artifacts.

The real CPU arithmetic and process tests live in test_compute_probe.py. These
tests mutate a valid input to prove the common v1 validator rejects false passes.
Run: python -I scripts/ci/tests/test_device_contract.py
"""

import copy
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "compatibility_test_helpers", pathlib.Path(__file__).with_name("test_compatibility_schema.py"))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
validator = helpers.validator
fact = helpers.fact
unknown = helpers.unknown
LOG = "log:cpu-reference.log"


def device_pair():
    """Build a valid-looking unit-test input, never exported as measurement."""
    result, profile = helpers.real_result()
    for document in (result, profile):
        document["compute"]["probe_contract"] = "device-v1"
    profile["required_stages"] = ["detect", "install", "initialize", "execute", "correctness"]
    for name in profile["required_stages"][1:]:
        result["stages"][name] = {
            "status": "passed", "evidence": [LOG],
            "privilege": {"escalation": fact("none", LOG)},
        }
    for name in ("execute", "correctness"):
        result["stages"][name]["compute"] = {"requested": "cpu", "effective": "cpu"}
    result["compute"]["backends"][0]["probe"] = {
        "tool": "python-standard-library",
        "runtime_installed": fact(True, LOG),
        "api_enumerated": fact(True, LOG),
        "device_enumerated": fact(True, LOG),
        "device_accessible": fact(True, LOG),
        "execution": {
            "status": "passed", "executed": True, "effective": "cpu",
            "correct": fact(True, LOG), "software_fallback": fact(False, LOG),
            "runtime": fact("cpython 3.12.15", LOG), "process_id": fact(123, LOG),
            "evidence": [LOG], "run": copy.deepcopy(result["run"]),
        },
    }
    return result, profile


def add_gpu(result):
    gpu = copy.deepcopy(result["compute"]["backends"][0])
    gpu.update(id="cuda", kind="gpu", api="cuda")
    gpu["probe"]["execution"]["effective"] = "cuda"
    result["compute"]["backends"].append(gpu)
    return gpu


def add_browser_wasm(result):
    browser = copy.deepcopy(result["compute"]["backends"][0])
    browser.update(id="browser-wasm", kind="cpu", api="wasm")
    browser["probe"]["tool"] = "playwright-chromium"
    browser["probe"]["execution"].update(
        effective="browser-wasm", runtime=fact("chromium", LOG))
    result["compute"]["backends"].append(browser)
    return browser


class DeviceContractTests(unittest.TestCase):
    def setUp(self):
        self.result, self.profile = device_pair()
        self.cpu = self.result["compute"]["backends"][0]
        self.execution = self.cpu["probe"]["execution"]

    def check(self):
        return helpers.check(self.result, self.profile)

    def assertRejected(self, code, path):
        findings = self.check()
        self.assertIn((code, path), [(code, path) for _, code, path, _ in findings.errors], findings.items)
        self.assertNotIn("schema", findings.codes(), findings.items)

    def extra_backends(self):
        """Keep stages on the valid CPU so only the extra backend's guard can reject it."""
        for add in (add_browser_wasm, add_gpu):
            self.setUp()
            backend = add(self.result)
            self.assertEqual(self.check().errors, [])
            yield backend, backend["probe"]["execution"]

    def test_extra_operation_requires_log_or_artifact(self):
        for backend, execution in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                execution["evidence"] = ["tool:discovery"]
                self.assertRejected("compute-evidence", "/compute/backends/1/probe/execution/evidence")
                execution["evidence"] = ["artifact:operation.json"]
                self.assertEqual(self.check().errors, [])

    def test_extra_completed_failed_operation_requires_effective_backend(self):
        for backend, execution in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                execution.update(status="failed", correct=fact(False, LOG), effective=None, reason="operation failed")
                self.assertRejected("compute-execution", "/compute/backends/1/probe/execution")

    def test_extra_pass_requires_measured_correctness(self):
        for backend, execution in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                execution["correct"] = fact(True, provenance="declared")
                self.assertRejected("compute-execution", "/compute/backends/1/probe/execution")

    def test_extra_pass_must_execute_its_own_backend(self):
        for backend, execution in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                execution["effective"] = "cpu"
                code = "accelerator-fallback" if backend["kind"] == "gpu" else "compute-execution"
                self.assertRejected(code, "/compute/backends/1/probe/execution")

    def test_extra_available_backend_requires_each_measured_device_fact(self):
        for field in ("api_enumerated", "device_enumerated", "device_accessible"):
            for value in (fact(False, LOG), unknown(), fact(True, provenance="declared")):
                for backend, _ in self.extra_backends():
                    with self.subTest(backend=backend["id"], field=field, value=value):
                        backend["probe"][field] = value
                        self.assertRejected("compute-availability", f"/compute/backends/1/probe/{field}")

    def test_extra_effective_backend_requires_measured_availability(self):
        for backend, _ in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                backend["available"] = unknown("not inspected")
                self.assertRejected("compute-availability", "/compute/backends/1/probe/execution/effective")

    def test_extra_backend_requires_probe(self):
        for backend, _ in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                backend.pop("probe")
                self.assertRejected("compute-probe", "/compute/backends/1/probe")

    def test_extra_probe_requires_contract_marker(self):
        for backend, _ in self.extra_backends():
            with self.subTest(backend=backend["id"]):
                for document in (self.result, self.profile):
                    document["compute"].pop("probe_contract")
                self.cpu.pop("probe")
                self.assertRejected("compute-probe", "/compute/backends/1/probe")

    def test_extra_unexecuted_status_rejects_completed_operation(self):
        for status in ("not-run", "blocked"):
            for backend, execution in self.extra_backends():
                with self.subTest(backend=backend["id"], status=status):
                    execution.update(status=status, effective=None, correct=unknown(), evidence=[], reason="not attempted")
                    self.assertRejected("compute-execution", "/compute/backends/1/probe/execution")

    def test_extra_completed_operation_requires_runtime_and_positive_pid(self):
        changes = (("runtime", unknown()), ("runtime", fact("chromium", provenance="declared")),
                   ("process_id", unknown()), ("process_id", fact(0, LOG)),
                   ("process_id", fact(123, provenance="declared")))
        for field, value in changes:
            for backend, execution in self.extra_backends():
                with self.subTest(backend=backend["id"], field=field, value=value):
                    execution[field] = value
                    self.assertRejected("compute-evidence", "/compute/backends/1/probe/execution")

    def test_extra_uncompleted_operation_carries_no_effective_or_correct_result(self):
        for field, value in (("effective", "cpu"), ("correct", fact(False, LOG))):
            for backend, execution in self.extra_backends():
                with self.subTest(backend=backend["id"], field=field):
                    execution.update(status="failed", executed=False, effective=None, correct=unknown(), reason="execution error")
                    execution[field] = value
                    self.assertRejected("compute-execution", "/compute/backends/1/probe/execution")

    def test_software_fallback_cannot_pass_cpu_probe(self):
        self.execution["software_fallback"] = fact(True, LOG)
        self.assertRejected("compute-execution", "/compute/backends/0/probe/execution")

    def test_software_fallback_cannot_pass_cpu_or_wasm_stage_when_probe_failed_correctness(self):
        for browser in (False, True):
            with self.subTest(browser=browser):
                self.setUp()
                backend = add_browser_wasm(self.result) if browser else self.cpu
                execution = backend["probe"]["execution"]
                execution.update(status="failed", correct=fact(False, LOG), reason="wrong result")
                self.profile["required_stages"] = ["detect", "install", "initialize"]
                for name in ("execute", "correctness"):
                    self.result["stages"][name]["compute"] = {
                        "requested": backend["id"], "effective": backend["id"]}
                self.result["stages"]["correctness"].update(status="failed", reason="wrong result")
                self.result["overall"] = "failed"
                # An operation can execute successfully and still compute an incorrect result.
                for fallback in (fact(False, LOG), unknown("fallback not inspected")):
                    execution["software_fallback"] = fallback
                    self.assertEqual(self.check().errors, [])
                execution["software_fallback"] = fact(True, LOG)
                self.assertRejected("compute-execution", "/stages/execute/compute")

    def test_wasm_pass_cannot_report_unexecuted_cuda_as_effective(self):
        browser = add_browser_wasm(self.result)
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"].update(status="not-run", executed=False, effective=None,
            correct=unknown(), evidence=[], reason="discovery only")
        browser["probe"]["execution"]["effective"] = "cuda"
        self.assertRejected("accelerator-fallback", "/compute/backends/1/probe/execution")
        self.result["stages"]["performance"] = copy.deepcopy(self.result["stages"]["correctness"])
        self.result["stages"]["performance"]["compute"] = {"requested": "browser-wasm", "effective": "cuda"}
        self.assertRejected("accelerator-fallback", "/stages/performance/compute")

    def test_legacy_cpu_to_cuda_discovery_requires_execution_evidence(self):
        gpu = add_gpu(self.result)
        for document in (self.result, self.profile):
            document["compute"].pop("probe_contract")
        self.cpu.pop("probe")
        gpu.pop("probe")
        self.result["stages"]["execute"]["compute"] = {"requested": "cpu", "effective": "cuda"}
        self.assertRejected("compute-execution", "/stages/execute/compute")
        self.assertRejected("accelerator-fallback", "/stages/execute/compute")

    def test_known_accelerator_apis_cannot_hide_in_cpu_or_other_kind(self):
        for api in validator.KNOWN_BACKEND_APIS - {"cpu", "wasm"}:
            for kind in ("cpu", "other"):
                with self.subTest(api=api, kind=kind):
                    self.setUp()
                    gpu = add_gpu(self.result)
                    gpu.update(api=api, kind=kind)
                    self.assertRejected("backend-reference", "/compute/backends/1")

    def test_flexible_accelerator_apis_allow_gpu_and_npu(self):
        for api in ("qnn", "webnn", "coreml", "opencl", "oneapi"):
            for kind in ("gpu", "npu"):
                with self.subTest(api=api, kind=kind):
                    self.setUp()
                    add_gpu(self.result).update(api=api, kind=kind)
                    self.assertEqual(self.check().errors, [])

    def test_real_stage_and_probe_must_share_an_execution_log_or_artifact(self):
        for common in ("tool:compute", "env:CI"):
            with self.subTest(common=common):
                self.result["stages"]["execute"]["evidence"] = ["log:stage.log", common]
                self.execution["evidence"] = ["artifact:operation.json", common]
                self.assertRejected("compute-evidence", "/stages/execute/evidence")
        for common in (LOG, "artifact:operation.json"):
            with self.subTest(common=common):
                self.result["stages"]["execute"]["evidence"] = ["log:stage.log", common]
                self.result["stages"]["correctness"]["evidence"] = [common]
                self.execution["evidence"] = [common]
                self.assertEqual(self.check().errors, [])

    def test_legacy_cpu_and_synthetic_accelerator_records_remain_valid(self):
        for document in (self.result, self.profile):
            document["compute"].pop("probe_contract")
        self.cpu.pop("probe")
        self.assertEqual(self.check().errors, [])
        result, profile = helpers.pair("kubernetes-arm64-cuda")
        self.assertEqual(helpers.check(result, profile).errors, [])

    def test_device_profile_required_workload_without_binding_fails_closed(self):
        add_gpu(self.result)
        declared = copy.deepcopy(self.profile["compute"]["backends"][0])
        declared.update(id="cuda", kind="gpu", api="cuda")
        self.profile["compute"]["backends"] = [declared]
        self.assertRejected("required-stage", "/stages/execute/compute")
        findings = helpers.check(self.profile)
        self.assertIn(("backend-reference", "/compute/backends"),
                      [(code, path) for _, code, path, _ in findings.errors], findings.items)
        self.profile["required_stages"] = ["detect"]
        # Capability-only profiles need no workload binding.
        self.assertEqual(helpers.check(self.profile).errors, [])

    def test_completed_cpu_reference_contract_is_valid(self):
        self.assertEqual(self.check().errors, [])

    def test_available_backend_requires_measured_installed_runtime(self):
        for runtime in (fact(False, LOG), unknown("runtime not inspected"),
                        fact(True, provenance="declared")):
            with self.subTest(runtime=runtime):
                self.cpu["probe"]["runtime_installed"] = runtime
                self.assertRejected("compute-availability", "/compute/backends/0/probe/runtime_installed")

    def test_completed_failed_operation_requires_installed_runtime(self):
        self.execution.update(status="failed", correct=fact(False, LOG), reason="reference mismatch")
        self.result["stages"]["correctness"].update(status="failed", reason="reference mismatch")
        self.result["overall"] = "failed"
        self.cpu["probe"]["runtime_installed"] = unknown("runtime not inspected")
        findings = self.check()
        self.assertIn(("compute-availability", "/compute/backends/0/probe/runtime_installed"),
                      [(code, path) for _, code, path, _ in findings.errors], findings.items)

    def test_available_unexecuted_browser_requires_installed_runtime(self):
        browser = add_browser_wasm(self.result)
        browser["probe"]["runtime_installed"] = unknown("runtime not inspected")
        browser["probe"]["execution"].update(
            status="not-run", executed=False, effective=None, correct=unknown(),
            runtime=unknown(), process_id=unknown(), evidence=[], reason="operation not attempted")
        self.assertRejected("compute-availability", "/compute/backends/1/probe/runtime_installed")

    def test_completed_operation_requires_runtime_even_when_request_unavailable(self):
        browser = add_browser_wasm(self.result)
        browser["available"] = unknown("requested runtime unavailable")
        browser["probe"]["runtime_installed"] = fact(False, LOG)
        browser["probe"]["execution"].update(effective="cpu", software_fallback=fact(True, LOG))
        self.assertRejected("compute-availability", "/compute/backends/1/probe/runtime_installed")

    def test_inferred_runtime_installation_can_support_completed_operation(self):
        self.cpu["probe"]["runtime_installed"] = fact(True, LOG, provenance="inferred")
        self.assertEqual(self.check().errors, [])

    def test_required_cpu_stages_cannot_be_replaced_by_extra_browser_operation(self):
        add_browser_wasm(self.result)
        self.execution.update(status="not-run", executed=False, effective=None,
                              correct=unknown(), evidence=[], reason="CPU reference not attempted")
        for name in ("execute", "correctness"):
            self.result["stages"][name]["compute"] = {
                "requested": "browser-wasm", "effective": "browser-wasm"}
        findings = self.check()
        for name in ("execute", "correctness"):
            self.assertIn(("required-stage", f"/stages/{name}/compute"),
                          [(code, path) for _, code, path, _ in findings.errors], findings.items)
        self.assertNotIn("schema", findings.codes(), findings.items)

    def test_required_performance_also_uses_profile_cpu(self):
        add_browser_wasm(self.result)
        self.profile["required_stages"].append("performance")
        self.result["stages"]["performance"] = copy.deepcopy(self.result["stages"]["correctness"])
        self.result["stages"]["performance"]["compute"] = {
            "requested": "browser-wasm", "effective": "browser-wasm"}
        self.assertRejected("required-stage", "/stages/performance/compute")

    def test_extra_completed_browser_operation_does_not_replace_cpu(self):
        add_browser_wasm(self.result)
        self.assertEqual(self.check().errors, [])

    def test_optional_stage_can_use_extra_browser_backend(self):
        add_browser_wasm(self.result)
        self.result["stages"]["performance"] = copy.deepcopy(self.result["stages"]["correctness"])
        self.result["stages"]["performance"]["compute"] = {
            "requested": "browser-wasm", "effective": "browser-wasm"}
        self.assertEqual(self.check().errors, [])

    def test_cpu_binding_uses_profile_id_instead_of_literal_cpu(self):
        self.cpu["id"] = self.profile["compute"]["backends"][0]["id"] = "reference-cpu"
        self.execution["effective"] = "reference-cpu"
        for name in ("execute", "correctness"):
            self.result["stages"][name]["compute"] = {
                "requested": "reference-cpu", "effective": "reference-cpu"}
        self.assertEqual(self.check().errors, [])

    def test_unexecuted_optional_browser_allows_unknown_or_absent_runtime(self):
        browser = add_browser_wasm(self.result)
        browser["available"] = unknown("browser capability not established")
        for field in ("api_enumerated", "device_enumerated", "device_accessible"):
            browser["probe"][field] = unknown("browser not inspected")
        browser["probe"]["execution"] = {
            "status": "not-run", "executed": False, "effective": None,
            "correct": unknown(), "software_fallback": unknown(),
            "runtime": unknown(), "process_id": unknown(), "evidence": [],
            "reason": "browser not configured",
        }
        for status in ("not-run", "blocked"):
            for runtime in (unknown("runtime not inspected"), fact(False, LOG)):
                with self.subTest(status=status, runtime=runtime):
                    browser["probe"]["execution"]["status"] = status
                    browser["probe"]["runtime_installed"] = runtime
                    self.assertEqual(self.check().errors, [])

    def test_device_contract_cannot_be_stripped_from_profile_result(self):
        self.result["compute"].pop("probe_contract")
        self.cpu.pop("probe")
        self.assertRejected("profile-mismatch", "/compute/probe_contract")

    def test_device_contract_requires_every_backend_probe(self):
        self.cpu.pop("probe")
        self.assertRejected("compute-probe", "/compute/backends/0/probe")

    def test_driver_presence_does_not_establish_device_availability(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["api_enumerated"] = unknown("driver installed, API never called")
        gpu["probe"]["device_accessible"] = unknown("no device opened")
        self.assertRejected("compute-availability", "/compute/backends/1/probe/api_enumerated")

    def test_absent_device_cannot_be_reported_available_or_passed(self):
        gpu = add_gpu(self.result)
        gpu["available"] = fact(False, LOG)
        gpu["probe"]["device_enumerated"] = fact(False, LOG)
        self.assertRejected("compute-availability", "/compute/backends/1/probe/execution/effective")

    def test_gpu_detection_is_not_operation_execution(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"].update(executed=False, effective=None, correct=unknown())
        self.assertRejected("compute-execution", "/compute/backends/1/probe/execution")

    def test_legacy_real_gpu_pass_still_requires_probe(self):
        gpu = add_gpu(self.result)
        for document in (self.result, self.profile):
            document["compute"].pop("probe_contract")
        self.cpu.pop("probe")
        gpu.pop("probe")
        self.result["stages"]["execute"]["compute"] = {"requested": "cuda", "effective": "cuda"}
        self.assertRejected("compute-execution", "/stages/execute/compute")

    def test_gpu_request_cpu_execution_uses_existing_fallback_rule(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"].update(effective="cpu", software_fallback=fact(True, LOG))
        self.result["stages"]["execute"]["compute"] = {"requested": "cuda", "effective": "cpu"}
        self.assertRejected("accelerator-fallback", "/stages/execute/compute")

    def test_software_gpu_with_same_backend_id_is_not_hardware_pass(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"]["software_fallback"] = fact(True, LOG)
        self.assertRejected("accelerator-fallback", "/compute/backends/1/probe/execution")

    def test_unknown_fallback_cannot_establish_hardware_execution(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"]["software_fallback"] = unknown("adapter type not observed")
        self.assertRejected("accelerator-fallback", "/compute/backends/1/probe/execution")

    def test_failed_software_probe_cannot_support_hardware_execute_pass(self):
        gpu = add_gpu(self.result)
        gpu["probe"]["execution"].update(
            status="failed", software_fallback=fact(True, LOG), reason="software adapter used")
        self.result["stages"]["execute"]["compute"] = {"requested": "cuda", "effective": "cuda"}
        self.assertRejected("accelerator-fallback", "/stages/execute/compute")

    def test_backend_id_must_match_profile(self):
        self.cpu["id"] = "other-cpu"
        self.execution["effective"] = "other-cpu"
        for name in ("execute", "correctness"):
            self.result["stages"][name]["compute"] = {"requested": "other-cpu", "effective": "other-cpu"}
        self.assertRejected("profile-mismatch", "/compute/backends")

    def test_actual_backend_cannot_be_omitted(self):
        self.execution["effective"] = None
        self.assertRejected("compute-execution", "/compute/backends/0/probe/execution")

    def test_stage_cannot_omit_compute_record(self):
        self.result["stages"]["execute"].pop("compute")
        self.assertRejected("compute-execution", "/stages/execute/compute")

    def test_actual_stage_backend_must_match_probe(self):
        add_gpu(self.result)
        self.result["stages"]["execute"]["compute"]["effective"] = "cuda"
        self.assertRejected("compute-execution", "/stages/execute/compute")

    def test_not_run_probe_cannot_be_promoted_to_passed_stage(self):
        self.execution.update(status="not-run", executed=False, effective=None,
                              correct=unknown(), evidence=[], reason="no operation attempted")
        self.assertRejected("compute-execution", "/stages/execute/compute")

    def test_completed_operation_requires_observed_runtime_and_process(self):
        for field in ("runtime", "process_id"):
            with self.subTest(field=field):
                original = self.execution[field]
                self.execution[field] = unknown("process not observed")
                self.assertRejected("compute-evidence", "/compute/backends/0/probe/execution")
                self.execution[field] = original

    def test_declared_correctness_is_not_a_measured_pass(self):
        self.execution["correct"] = fact(True, provenance="declared")
        self.assertRejected("compute-execution", "/compute/backends/0/probe/execution")

    def test_mismatched_cpu_result_cannot_pass_correctness(self):
        self.execution.update(status="failed", correct=fact(False, LOG), reason="reference mismatch")
        self.assertRejected("compute-execution", "/stages/correctness/status")
        self.result["stages"]["correctness"].update(status="failed", reason="reference mismatch")
        self.result["overall"] = "failed"
        findings = self.check()
        self.assertEqual(findings.codes(), ["required-stage"], findings.items)

    def test_stage_evidence_cannot_cite_a_different_operation(self):
        self.result["stages"]["execute"]["evidence"] = ["log:unrelated.log"]
        self.assertRejected("compute-evidence", "/stages/execute/evidence")

    def test_discovery_tool_is_not_execution_evidence(self):
        self.execution["evidence"] = ["tool:nvidia-smi"]
        self.assertRejected("compute-evidence", "/compute/backends/0/probe/execution/evidence")

    def test_execution_must_link_same_github_sha_workflow_job_and_attempt(self):
        changes = (
            ("revision", "tested_sha", "a" * 40),
            ("revision", "event_sha", "b" * 40),
            ("revision", "pr_head_sha", "c" * 40),
            ("revision", "pr_base_sha", "d" * 40),
            ("ci", "workflow_ref", "P-R-N-D/Claus-AI/.github/workflows/other.yml@refs/pull/1/merge"),
            ("ci", "job", "another-job"),
            ("ci", "run_id", "101"),
            ("ci", "run_attempt", 2),
        )
        for group, field, value in changes:
            with self.subTest(field=field):
                self.execution["run"] = copy.deepcopy(self.result["run"])
                self.execution["run"][group][field]["value"] = value
                self.assertRejected("compute-evidence", "/compute/backends/0/probe/execution/run")

    def test_missing_execution_run_is_rejected(self):
        self.execution.pop("run")
        self.assertRejected("compute-evidence", "/compute/backends/0/probe/execution/run")

    def test_synthetic_fixture_is_not_measured_evidence(self):
        result, profile = helpers.pair("kubernetes-arm64-cuda")
        findings = helpers.check(result, profile, real=True)
        self.assertIn("synthetic-marking", findings.codes())

    def test_privileged_container_does_not_gain_a_device_exception(self):
        result, profile = helpers.pair("kubernetes-arm64-cuda")
        result["environment"]["layers"][-1]["container"]["privileged"]["value"] = True
        findings = helpers.check(result, profile)
        self.assertIn("policy-no-privileged-container", findings.codes(), findings.items)

    def test_rootful_engine_is_rejected_when_lane_requires_rootless(self):
        result, profile = helpers.pair("windows-arm64-wsl2-podman")
        profile["policy"]["rules"]["engine-rootless"] = "required"
        result["environment"]["layers"][-1]["container"]["engine_mode"]["value"] = "rootful"
        findings = helpers.check(result, profile)
        self.assertIn("policy-engine-rootless", findings.codes(), findings.items)

    def test_not_run_accelerator_and_unknown_driver_do_not_fail_cpu_lane(self):
        gpu = add_gpu(self.result)
        gpu["available"] = unknown("device usability was not tested")
        for field in ("runtime_installed", "api_enumerated", "device_enumerated", "device_accessible"):
            gpu["probe"][field] = unknown("runtime and hardware were not inspected")
        gpu["probe"]["execution"] = {
            "status": "not-run", "executed": False, "effective": None,
            "correct": unknown(), "software_fallback": unknown(),
            "runtime": unknown(), "process_id": unknown(), "evidence": [],
            "reason": "no trusted hardware executor configured",
        }
        self.assertEqual(self.check().errors, [])


if __name__ == "__main__":
    unittest.main()
