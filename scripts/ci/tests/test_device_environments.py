"""Synthetic special environments and denial cases; Python standard library only."""

import copy
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load("device_environment_validator", ROOT / "scripts/ci/validate_compatibility.py")
fixtures = load("device_environment_scenarios", ROOT / "tests/compatibility/fixtures/device-environments/scenarios.py")
SCHEMA = validator.load_schema()


def validate(result, profile=None, **options):
    return validator.validate_document(result, SCHEMA, profile=profile, **options)


def codes(findings):
    return {finding[1] for finding in findings.errors}


class DeviceEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.examples = fixtures.scenarios()

    def test_all_static_profiles_and_environment_structures_are_valid(self):
        self.assertEqual(len(self.examples), 15)
        for name, (profile, result, expected_errors) in self.examples.items():
            with self.subTest(name=name):
                self.assertEqual(validate(profile).errors, [])
                self.assertEqual(validate(result, profile, evaluate_policy=False).errors, [])
                self.assertEqual(codes(validate(result, profile)), expected_errors)

    def test_no_static_case_is_presented_as_an_executed_run(self):
        for name, (profile, result, _) in self.examples.items():
            with self.subTest(name=name):
                self.assertTrue(profile["synthetic"])
                self.assertTrue(result["synthetic"])
                self.assertIsNone(result["generated_at"])
                self.assertEqual(result["overall"], "incomplete")
                self.assertEqual({stage["status"] for stage in result["stages"].values()}, {"not-run"})
                self.assertEqual(profile["policy"]["exceptions"], [])
                self.assertTrue(all(validator.known(fact) is None for _, fact in validator.facts_of(result["run"])))
                self.assertIn("synthetic-marking", codes(validate(result, profile, require_real=True)))

    def test_unmarking_a_fixture_does_not_make_it_measured_evidence(self):
        profile, result, _ = self.examples["bare-metal"]
        result["synthetic"] = False
        result["generated_at"] = "2026-10-12T00:00:00Z"
        self.assertIn("synthetic-marking", codes(validate(result, profile)))
        result["synthetic"] = True
        result["run"]["revision"]["tested_sha"] = fixtures.fact("a" * 40, "invented commit")
        self.assertIn("synthetic-marking", codes(validate(result)))

    def test_host_systemd_and_container_pid1_are_distinct(self):
        direct = self.examples["host-systemd-process"][1]["environment"]["layers"]
        self.assertEqual(len(direct), 1)
        self.assertEqual(direct[0]["service_manager"]["scope"]["value"], "user")
        for name in ("host-systemd-container", "host-quadlet-container"):
            profile, result, _ = self.examples[name]
            layers = result["environment"]["layers"]
            self.assertEqual(layers[0]["service_manager"]["name"]["value"], "systemd")
            self.assertEqual(layers[-1]["service_manager"]["name"]["value"], "none")
            self.assertEqual(layers[-1]["container"]["orchestration"]["scope"]["value"], "user")
            layers[-1]["service_manager"] = copy.deepcopy(layers[0]["service_manager"])
            self.assertIn("profile-mismatch", codes(validate(result, profile)))

        profile, result, _ = self.examples["container-pid1-systemd"]
        layer = result["environment"]["layers"][-1]
        self.assertEqual(layer["service_manager"]["name"]["value"], "systemd")
        self.assertEqual(layer["service_manager"]["scope"]["value"], "system")
        self.assertFalse(layer["container"]["privileged"]["value"])
        layer["service_manager"]["scope"] = fixtures.fact("user", "host scope incorrectly used for PID 1")
        self.assertIn("profile-mismatch", codes(validate(result, profile)))

    def test_systemd_cgroup_delegation_and_rootless_engine_are_independent(self):
        _, inside, _ = self.examples["container-pid1-systemd"]
        _, plain, _ = self.examples["rootless-podman"]
        for result in (inside, plain):
            self.assertEqual(result["environment"]["layers"][-1]["container"]["engine_mode"]["value"], "rootless")
            self.assertFalse(result["workload"]["identity"]["superuser"]["value"])
            self.assertEqual(result["workload"]["isolation"]["cgroup"]["version"]["value"], "v2")
        self.assertTrue(inside["workload"]["isolation"]["cgroup"]["delegated"]["value"])
        self.assertFalse(plain["workload"]["isolation"]["cgroup"]["delegated"]["value"])

    def test_wsl_vm_and_container_order_cannot_be_reversed(self):
        for name in ("windows-arm-wsl2-container", "vm-rootless-podman-nonroot"):
            _, original, _ = self.examples[name]
            for order in ((2, 1, 0), (0, 2, 1)):
                with self.subTest(name=name, order=order):
                    result = copy.deepcopy(original)
                    layers = result["environment"]["layers"]
                    result["environment"]["layers"] = [layers[index] for index in order]
                    # A VM inside a Linux container has no supported nested-lane profile;
                    # profile matching rejects that swap even if OS families agree.
                    errors = codes(validate(result, self.examples[name][0]))
                    self.assertTrue(errors & {"layer-structure", "os-contradiction", "profile-mismatch"}, errors)

    def test_cluster_nonroot_and_no_new_privileges_do_not_mean_rootless_engine(self):
        for name in ("kubernetes", "k3s", "openshift"):
            profile, result, _ = self.examples[name]
            layer = result["environment"]["layers"][-1]
            self.assertEqual(layer["container"]["orchestration"]["name"]["value"], name)
            self.assertFalse(result["workload"]["identity"]["superuser"]["value"])
            self.assertTrue(result["workload"]["isolation"]["no_new_privs"]["value"])
            self.assertFalse(layer["extensions"]["x-device-plugin"]["requested"])
            self.assertEqual(layer["container"]["devices"]["value"], [])
            self.assertIn("policy-engine-rootless", codes(validate(result, profile)))

    def test_unapproved_rootful_and_privileged_changes_are_rejected(self):
        for field, value, expected in (("engine_mode", "rootful", "policy-engine-rootless"),
                                       ("privileged", True, "policy-no-privileged-container")):
            profile, result, _ = copy.deepcopy(self.examples["rootless-podman"])
            result["environment"]["layers"][-1]["container"][field] = fixtures.fact(value, "unauthorized change")
            self.assertIn(expected, codes(validate(result, profile)))

    def test_device_passthrough_is_not_usability_or_execution(self):
        for name in ("gpu-passthrough", "npu-passthrough"):
            profile, result, _ = self.examples[name]
            self.assertIsNone(result["compute"]["backends"][-1]["available"]["value"])
            self.assertEqual(result["stages"]["execute"]["status"], "not-run")
            self.assertIn("policy-minimal-devices", codes(validate(result, profile)))
        profile, cpu, _ = self.examples["cpu-only-container"]
        self.assertEqual(cpu["environment"]["layers"][-1]["container"]["devices"]["value"], [])
        self.assertEqual([backend["id"] for backend in cpu["compute"]["backends"]], ["cpu"])
        self.assertEqual(validate(cpu, profile).errors, [])


if __name__ == "__main__":
    unittest.main()
