"""Tests for scripts/ci/validate_compatibility.py and tests/compatibility/schema.json.

The committed profiles and the synthetic fixtures must be valid. Every other
test changes one field of a valid document and checks the exact error code the
change must produce, so a rule that stops firing fails here. No Django and no
third-party package is needed. From the repository root:
    python -I scripts/ci/tests/test_compatibility_schema.py --verbose
"""

import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "validate_compatibility.py"
COMPATIBILITY = ROOT / "tests" / "compatibility"
FIXTURES = COMPATIBILITY / "fixtures"
STRATEGY = ROOT / "docs" / "DEPENDENCY-STRATEGY.md"

spec = importlib.util.spec_from_file_location("validate_compatibility", SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

SCHEMA = validator.load_schema()
LANES = validator.read_strategy_lanes(STRATEGY)
PAIRED = ("linux-x64-vm", "windows-arm64-native", "windows-arm64-wsl2-podman", "kubernetes-arm64-cuda")


def fixture(name):
    return validator.load_json(FIXTURES / f"{name}.json")


def pair(name):
    return fixture(f"{name}.result"), fixture(f"{name}.profile")


def check(document, profile=None, lanes=LANES, policy=True, real=False):
    return validator.validate_document(document, SCHEMA, profile, lanes, policy, real)


def changed(document, change):
    document = copy.deepcopy(document)
    change(document)
    return document


def unknown(reason="not recorded"):
    return {"value": None, "provenance": "unknown", "reason": reason}


def fact(value, *evidence, provenance="observed"):
    out = {"value": value, "provenance": provenance}
    if evidence:
        out["evidence"] = list(evidence)
    return out


def real_result():
    """A measured-looking result built from the Linux fixture: the shape the collector writes on a runner."""
    result, profile = pair("linux-x64-vm")

    def unmark(value):
        if isinstance(value, dict):
            if isinstance(value.get("evidence"), list):
                value["evidence"] = [ref.replace("synthetic:", "file:", 1) for ref in value["evidence"]]
            for item in value.values():
                unmark(item)
        elif isinstance(value, list):
            for item in value:
                unmark(item)

    result, profile = copy.deepcopy(result), copy.deepcopy(profile)
    unmark(result)
    result["synthetic"] = False
    result["generated_at"] = "2026-10-11T00:00:00Z"
    result["profile_id"] = profile["id"] = "linux-x64-vm"
    result.pop("extensions")
    result["stages"]["detect"]["evidence"] = ["tool:collect-environment"]
    sha = "0123456789abcdef0123456789abcdef01234567"
    result["run"] = {
        "ci": {
            "system": fact("github-actions", "env:GITHUB_ACTIONS"),
            "event": fact("pull_request", "env:GITHUB_EVENT_NAME"),
            "repository": fact("P-R-N-D/Claus-AI", "env:GITHUB_REPOSITORY"),
            "ref": fact("refs/pull/1/merge", "env:GITHUB_REF"),
            "run_id": fact("100", "env:GITHUB_RUN_ID"),
            "run_attempt": fact(1, "env:GITHUB_RUN_ATTEMPT"),
            "run_url": fact("https://github.com/P-R-N-D/Claus-AI/actions/runs/100", "env:GITHUB_RUN_ID", provenance="inferred"),
            "workflow_ref": fact("P-R-N-D/Claus-AI/.github/workflows/ci.yml@refs/pull/1/merge", "env:GITHUB_WORKFLOW_REF"),
            "job": fact("compatibility", "env:GITHUB_JOB"),
            "runner_environment": fact("github-hosted", "env:RUNNER_ENVIRONMENT"),
            "runner_os": fact("Linux", "env:RUNNER_OS"),
            "runner_arch": fact("X64", "env:RUNNER_ARCH"),
            "image_os": fact("ubuntu24", "env:ImageOS"),
            "image_version": fact("20261004.327.1", "env:ImageVersion"),
        },
        "revision": {
            "tested_sha": fact(sha, "git:.git/HEAD"),
            "event_sha": fact(sha, "env:GITHUB_SHA"),
            "pr_head_sha": fact("1" * 40, "event:pull_request.head.sha"),
            "pr_base_sha": fact("2" * 40, "event:pull_request.base.sha"),
        },
    }
    profile["synthetic"] = False
    profile["support"]["status"] = "checked"
    profile["evidence_sources"] = [{"workflow": ".github/workflows/ci.yml", "job": "compatibility"}]
    profile.pop("extensions")
    return result, profile


def macos_profile():
    """The real profile of real_result(), moved to an Apple Silicon Mac: one host layer running macOS."""
    _, profile = real_result()
    layers = profile["environment"]["layers"]
    host = layers[0]
    host["os"].update(family=fact("macos", provenance="declared"), name=unknown(), version=unknown(), kernel=unknown())
    host["cpu"].update(arch=fact("aarch64", provenance="declared"), execution=unknown())
    host["service_manager"].update(name=unknown(), scope=unknown())
    profile["environment"]["layers"] = [host]
    profile["workload"]["runtimes"]["python"].update(
        platform=fact("macosx-15.0-arm64", provenance="declared"), arch=fact("aarch64", provenance="declared"),
        soabi=fact("cpython-312-darwin", provenance="declared"), libc=unknown(), execution=unknown())
    profile["workload"]["runtimes"]["node"].update(platform=fact("darwin", provenance="declared"), arch=fact("aarch64", provenance="declared"), execution=unknown())
    return profile


class Case(unittest.TestCase):
    def assertCodes(self, findings, *codes, level="error"):
        self.assertEqual(findings.codes(level), sorted(codes), findings.items)

    def assertValid(self, findings):
        self.assertEqual(findings.errors, [], findings.items)

    def assertFinding(self, findings, code, path, level="error"):
        """The finding with this code is reported at this path, not only somewhere."""
        found = [(code_, path_) for kind, code_, path_, _ in findings.items if kind == level]
        self.assertIn((code, path), found, findings.items)


class CommittedDocumentTests(Case):
    def test_paired_fixtures_are_valid(self):
        for name in PAIRED:
            result, profile = pair(name)
            with self.subTest(name=name):
                self.assertValid(check(profile))
                self.assertValid(check(result, profile))

    def test_unpaired_fixture_is_valid_under_the_default_policy(self):
        self.assertValid(check(fixture("vm-podman-quadlet-systemd.result")))

    def test_the_rootful_cluster_fixture_only_warns_about_the_preferred_rule(self):
        result, profile = pair("kubernetes-arm64-cuda")
        findings = check(result, profile)
        self.assertCodes(findings)
        self.assertCodes(findings, "policy-engine-rootless", "policy-exception-applied", level="warning")
        applied = [message for kind, code, _, message in findings.items if code == "policy-exception-applied"]
        self.assertEqual(len(applied), 1, findings.items)
        self.assertIn("gpu-device (minimal-devices) covers devices nvidia-gpu", applied[0])
        self.assertIn(profile["policy"]["exceptions"][0]["approval"]["reference"], applied[0])

    def test_fixtures_are_marked_synthetic_and_carry_no_measured_data(self):
        for path in sorted(FIXTURES.glob("*.json")):
            document = validator.load_json(path)
            with self.subTest(path=path.name):
                self.assertIs(document["synthetic"], True)
                refs = [ref for _, ref in validator.evidence_refs(document)]
                self.assertTrue(all(ref.startswith("synthetic:") for ref in refs))
                if document["kind"] == "profile":
                    self.assertTrue(document["id"].startswith("synthetic-"))
                    self.assertNotEqual(document["support"]["status"], "checked")
                else:
                    self.assertTrue(refs)
                    self.assertIsNone(document["generated_at"])
                    self.assertTrue(all(value["provenance"] == "not-applicable" for value in document["run"]["ci"].values()))

    def test_committed_profiles_are_real_and_linked_to_a_job(self):
        profiles = sorted((COMPATIBILITY / "profiles").glob("*.json"))
        self.assertTrue(profiles)
        for path in profiles:
            profile = validator.load_json(path)
            with self.subTest(path=path.name):
                self.assertIs(profile["synthetic"], False)
                self.assertTrue(profile["evidence_sources"])
                self.assertValid(check(profile))

    def test_the_real_profile_declares_its_runner(self):
        profile = validator.load_json(COMPATIBILITY / "profiles" / "linux-x64-gha.json")
        self.assertEqual({name: fact["value"] for name, fact in profile["runner"].items()},
                         {"runner_environment": "github-hosted", "runner_os": "Linux", "runner_arch": "X64", "image_os": "ubuntu24"})

    def test_required_scenarios_are_covered(self):
        documents = [validator.load_json(path) for path in FIXTURES.glob("*.result.json")]
        kinds = [[layer["kind"]["value"] for layer in validator.layers_of(document)] for document in documents]
        self.assertIn(["host"], kinds)
        self.assertIn(["host", "wsl2", "container"], kinds)
        self.assertIn(["host", "vm", "container"], kinds)
        self.assertIn(["host", "container"], kinds)
        apis = {backend["api"] for document in documents for backend in document["compute"]["backends"]}
        self.assertTrue({"cpu", "cuda", "qnn"} <= apis)
        wsl = fixture("windows-arm64-wsl2-podman.result")
        self.assertEqual(wsl["environment"]["layers"][2]["container"]["engine_mode"]["value"], "rootless")
        self.assertEqual(wsl["workload"]["identity"]["uid"]["value"], 1000)
        quadlet = fixture("vm-podman-quadlet-systemd.result")["environment"]["layers"]
        self.assertEqual(quadlet[1]["service_manager"]["name"]["value"], "systemd")
        self.assertEqual(quadlet[2]["service_manager"]["name"]["value"], "systemd")
        self.assertEqual(quadlet[2]["container"]["orchestration"]["scope"]["value"], "user")


class SchemaSubsetTests(Case):
    def keywords(self, node, out):
        if isinstance(node, dict):
            out.update(node)
            for key in ("properties", "$defs"):
                for sub in node.get(key, {}).values():
                    self.keywords(sub, out)
            for key in ("items", "additionalProperties", "propertyNames", "if", "then", "else"):
                if key in node:
                    self.keywords(node[key], out)
        return out

    def test_the_schema_uses_only_the_supported_keywords(self):
        used = self.keywords(SCHEMA, set())
        self.assertLessEqual(used, validator.SUPPORTED_KEYWORDS)
        self.assertEqual(used & validator.CHECKING_KEYWORDS, validator.CHECKING_KEYWORDS - {"minLength", "minimum"} | (used & {"minLength", "minimum"}))

    def test_other_keywords_are_rejected_before_any_document_is_read(self):
        for keyword in ("oneOf", "anyOf", "allOf", "not", "format", "uniqueItems", "patternProperties",
                        "exclusiveMinimum", "multipleOf", "dependentRequired", "prefixItems", "contains",
                        "unevaluatedProperties", "$dynamicRef", "default"):
            with self.subTest(keyword=keyword), self.assertRaises(validator.SchemaError):
                validator.check_schema({"type": "object", "properties": {"a": {keyword: []}}})
        for bad in (
            {"$defs": {"a": {"$defs": {}}}, "$ref": "#/$defs/a"},
            {"$ref": "#/$defs/missing"},
            {"$ref": "other.json#/x"},
            {"pattern": "("},
            {"type": "decimal"},
            {"minItems": -1},
            {"maxLength": 1.0},
            {"required": "a"},
            {"$schema": "http://json-schema.org/draft-07/schema#"},
            {"properties": {"a": {"$schema": validator.DIALECT}}},
            {"properties": {"a": {"$id": "https://example.invalid/a"}}},
            {"$id": 1},
            {"title": ["a"]},
            {"description": 1},
            {"type": [1]},
            {"type": [["string"]]},
            {"pattern": "^[0-9]\\d$"},
            {"pattern": "\\w+"},
            {"pattern": "\\bword"},
            {"pattern": "a\\Z"},
            {"pattern": "\\Aa"},
            {"pattern": "(?i)a"},
            {"pattern": "(?P<name>a)"},
            {"pattern": "^a{,3}$"},
            {"pattern": "^a*+b$"},
            {"pattern": "^a{2}+$"},
            {"pattern": "^\\a$"},
            {"pattern": "^\\N{LATIN SMALL LETTER A}$"},
            {"pattern": "^\\U00000061$"},
            {"pattern": "^\\uD83D\\uDE00$"},
            {"pattern": "^[\\udc00-\\uDFFF]$"},
            {"pattern": "^[]a]$"},
            {"pattern": "^[^]a]$"},
            {"$defs": {"a": {"if": {"$ref": "#/$defs/a"}}}, "$ref": "#/$defs/a"},
            {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"else": {"$ref": "#/$defs/a"}}}},
        ):
            with self.subTest(schema=bad), self.assertRaises(validator.SchemaError):
                validator.check_schema(bad)
        for bad in ([], "schema", True):
            with self.subTest(root=bad), self.assertRaises(validator.SchemaError):
                validator.check_schema(bad)
        for good in (
            {"$schema": validator.DIALECT, "$id": "https://example.invalid/s", "title": "t", "$comment": "c"},
            {"pattern": "^(?:a|b)[0-9]\\\\d$"},
            {"pattern": "^\\*+a{0,3}$"},
            {"pattern": "^\\t\\n\\x41\\u0041\\.\\[\\]$"},
            {"$defs": {"a": {"properties": {"x": {"$ref": "#/$defs/a"}}, "items": {"$ref": "#/$defs/a"}}}, "$ref": "#/$defs/a"},
        ):
            with self.subTest(schema=good):
                validator.check_schema(good)

    def test_each_checking_keyword_is_applied(self):
        defs = {"$defs": {"s": {"type": "string"}}}
        cases = [
            ({"type": "integer"}, 1, 1.5),
            ({"type": "integer"}, 1, True),
            ({"type": "integer"}, 1, 1.0),
            ({"type": "number"}, 1, "1"),
            ({"type": ["string", "null"]}, None, 0),
            ({"enum": [1, "a"]}, "a", True),
            ({"const": False}, False, 0),
            ({"properties": {"a": {"type": "string"}}}, {"a": "x"}, {"a": 1}),
            ({"properties": {"a": False, "b": True}}, {"b": 1}, {"a": 1}),
            ({"required": ["a"]}, {"a": 1}, {"b": 1}),
            ({"additionalProperties": False, "properties": {"a": {}}}, {"a": 1}, {"a": 1, "b": 2}),
            ({"additionalProperties": {"type": "integer"}}, {"b": 2}, {"b": "2"}),
            ({"propertyNames": {"pattern": "^x-"}}, {"x-a": 1}, {"a": 1}),
            ({"items": {"type": "integer"}}, [1, 2], [1, "2"]),
            ({"minItems": 1}, [1], []),
            ({"maxItems": 1}, [1], [1, 2]),
            ({"minLength": 2}, "ab", "a"),
            ({"maxLength": 2}, "ab", "abc"),
            ({"pattern": "^a+$"}, "aa", "ab"),
            ({"minimum": 0}, 0, -1),
            ({"maximum": 10}, 10, 11),
            (dict(defs, **{"$ref": "#/$defs/s"}), "a", 1),
            ({"if": {"properties": {"k": {"const": 1}}}, "then": {"required": ["a"]}, "else": {"required": ["b"]}}, {"k": 1, "a": 0}, {"k": 1, "b": 0}),
            ({"if": {"properties": {"k": {"const": 1}}}, "then": {"required": ["a"]}, "else": {"required": ["b"]}}, {"k": 2, "b": 0}, {"k": 2, "a": 0}),
        ]
        exercised = set()
        for schema, good, bad in cases:
            validator.check_schema(schema)
            exercised.update(set(schema) & validator.CHECKING_KEYWORDS)
            with self.subTest(schema=schema, value=good):
                findings = validator.Findings()
                validator.validate_schema(good, schema, schema, "", findings)
                self.assertEqual(findings.items, [])
            with self.subTest(schema=schema, value=bad):
                findings = validator.Findings()
                validator.validate_schema(bad, schema, schema, "", findings)
                self.assertTrue(findings.errors)
        self.assertEqual(exercised, validator.CHECKING_KEYWORDS)

    def test_a_structural_error_stops_the_cross_field_rules(self):
        result = changed(fixture("linux-x64-vm.result"), lambda d: d["stages"].pop("security"))
        findings = check(result)
        self.assertCodes(findings, "schema")
        self.assertCodes(findings, "semantic-skipped", level="warning")

    def test_structure_errors(self):
        result = fixture("linux-x64-vm.result")
        for label, change in (
            ("missing field", lambda d: d["workload"].pop("identity")),
            ("wrong type", lambda d: d["workload"]["identity"]["uid"].update(value="1001")),
            ("unknown status", lambda d: d["stages"]["install"].update(status="skipped")),
            ("unknown provenance", lambda d: d["workload"]["identity"]["uid"].update(provenance="guessed")),
            ("schema version", lambda d: d.update(schema_version=2)),
            ("extra field", lambda d: d.update(notes="x")),
            ("extension without x-", lambda d: d["extensions"].update(note="x")),
            ("layer field without x-", lambda d: d["environment"]["layers"][1].update(cloud="azure")),
            ("too many layers", lambda d: d["environment"].update(layers=d["environment"]["layers"] * 5)),
            ("bad evidence kind", lambda d: d["stages"]["detect"].update(evidence=["https://example.com"])),
            ("bad sha", lambda d: d["run"]["revision"].update(tested_sha=fact("ABC", "synthetic:x"))),
        ):
            with self.subTest(label):
                self.assertCodes(check(changed(result, change)), "schema")

    def test_extension_fields_are_accepted_and_ignored(self):
        result = changed(fixture("linux-x64-vm.result"), lambda d: (
            d["extensions"].update({"x-vendor-data": {"anything": [1, 2]}}),
            d["environment"]["layers"][1].update(extensions={"x-azure-sku": "Standard_D4ads_v6"}),
            d["stages"]["detect"].update(extensions={"x-duration-seconds": 3}),
        ))
        self.assertValid(check(result))
        # Contents that look like facts or evidence are not facts: only strings are checked there.
        for label, data in (
            ("unknown fact without a reason", {"value": None, "provenance": "unknown"}),
            ("measured evidence in a synthetic document", {"value": "x", "provenance": "observed", "evidence": ["file:/x"]}),
            ("evidence that is not a string", {"evidence": [1]}),
        ):
            with self.subTest(label):
                self.assertValid(check(changed(result, lambda d: d["extensions"].update({"x-probe": data}))))


class JsonLoadingTests(Case):
    def write(self, directory, content):
        path = pathlib.Path(directory) / "document.json"
        path.write_bytes(content)
        return path

    def test_lenient_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for label, content in (
                ("duplicate key", b'{"a": 1, "a": 2}'),
                ("NaN", b'{"a": NaN}'),
                ("Infinity", b'{"a": -Infinity}'),
                ("overflow", b'{"a": 1e999}'),
                ("byte order mark", b'\xef\xbb\xbf{}'),
                ("not UTF-8", b'{"a": "\xff"}'),
                ("deep nesting", b"[" * 100000 + b"]" * 100000),
                ("too large", b'{"a": "' + b"x" * validator.MAX_DOCUMENT_BYTES + b'"}'),
                ("integer outside the double range", b'{"a": 1' + b"0" * 400 + b"}"),
            ):
                with self.subTest(label), self.assertRaises(ValueError):
                    validator.load_json(self.write(directory, content))

    def test_a_document_is_read_only_up_to_the_size_limit(self):
        handle = mock.MagicMock()
        handle.__enter__.return_value.read.return_value = b" " * (validator.MAX_DOCUMENT_BYTES + 1)
        with mock.patch.object(validator, "open", create=True, return_value=handle), self.assertRaisesRegex(ValueError, "larger than"):
            validator.load_json("large.json")
        handle.__enter__.return_value.read.assert_called_once_with(validator.MAX_DOCUMENT_BYTES + 1)

    def test_the_command_reports_unparsable_documents_as_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, b'{"a": 1, "a": 2}')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(validator.main([str(path)]), 1)
            self.assertIn("ERROR json /", output.getvalue())


class ProvenanceTests(Case):
    def test_fact_rules(self):
        result = fixture("linux-x64-vm.result")
        uid = ("workload", "identity", "uid")

        def set_uid(value):
            return lambda d: d["workload"]["identity"].update(uid=value)

        for label, change in (
            ("observed without evidence", set_uid({"value": 1001, "provenance": "observed"})),
            ("observed without value", set_uid({"value": None, "provenance": "observed", "evidence": ["synthetic:x"]})),
            ("inferred without value", set_uid({"value": None, "provenance": "inferred", "evidence": ["synthetic:x"]})),
            ("inferred without evidence", set_uid({"value": 1001, "provenance": "inferred", "evidence": []})),
            ("declared without value", set_uid({"value": None, "provenance": "declared"})),
            ("unknown with a value", set_uid({"value": 1001, "provenance": "unknown", "reason": "x"})),
            ("unknown without reason", set_uid({"value": None, "provenance": "unknown"})),
            ("not-applicable with a value", set_uid({"value": 1001, "provenance": "not-applicable", "reason": "x"})),
        ):
            with self.subTest(label):
                findings = check(changed(result, change), policy=False)
                self.assertIn("fact-provenance", findings.codes(), uid)

    def test_a_profile_cannot_hold_observations(self):
        for provenance in ("observed", "inferred"):
            with self.subTest(provenance):
                profile = changed(fixture("linux-x64-vm.profile"), lambda d: d["environment"]["layers"][1]["os"].update(
                    name=fact("ubuntu", "synthetic:x", provenance=provenance)))
                self.assertCodes(check(profile), "profile-provenance")

    def test_synthetic_marking(self):
        result = fixture("linux-x64-vm.result")
        profile = fixture("linux-x64-vm.profile")
        cases = (
            ("measured evidence in a synthetic document", result, lambda d: d["stages"]["detect"].update(evidence=["log:job"])),
            ("synthetic result with a collection time", result, lambda d: d.update(generated_at="2026-10-11T00:00:00Z")),
            ("synthetic result claiming a run", result, lambda d: d["run"]["ci"].update(system=fact("github-actions", "synthetic:x"))),
            ("synthetic result naming a run URL", result, lambda d: d["run"]["ci"].update(system=unknown(), run_url=fact("https://github.com/P-R-N-D/Claus-AI/actions/runs/1", provenance="declared"))),
            ("synthetic result naming a commit", result, lambda d: d["run"]["revision"].update(event_sha=fact("1" * 40, provenance="declared"))),
            ("real result without a collection time", result, lambda d: d.update(synthetic=False)),
            ("synthetic profile without the id prefix", profile, lambda d: d.update(id="linux-x64-vm")),
        )
        for label, document, change in cases:
            with self.subTest(label):
                self.assertIn("synthetic-marking", check(changed(document, change), policy=False).codes())
        measured, _ = real_result()
        self.assertCodes(check(changed(measured, lambda d: d["stages"]["detect"]["evidence"].append("synthetic:x"))), "synthetic-marking")
        self.assertCodes(check(result, real=True), "synthetic-marking")
        undated = check(changed(measured, lambda d: d.update(generated_at=None)))
        self.assertCodes(undated, "synthetic-marking")
        self.assertFinding(undated, "synthetic-marking", "/generated_at")
        # The collection time has the schema's shape and is also a day and time of day that exist.
        for generated_at in ("2026-99-99T99:99:99Z", "2026-02-30T12:00:00Z", "2025-02-29T12:00:00Z", "2026-10-11T24:00:00Z", "2026-10-11T23:60:00Z", "2026-10-11T23:59:60Z"):
            with self.subTest(generated_at=generated_at):
                impossible = check(changed(measured, lambda d: d.update(generated_at=generated_at)))
                self.assertCodes(impossible, "synthetic-marking")
                self.assertFinding(impossible, "synthetic-marking", "/generated_at")
        self.assertValid(check(changed(measured, lambda d: d.update(generated_at="2024-02-29T23:59:59Z"))))

    def test_strings_with_control_characters_or_secrets_are_rejected(self):
        result = fixture("linux-x64-vm.result")
        for label, text, code in (
            ("newline", "a\nb", "control-character"),
            ("C1 control sequence introducer", "a\x9b31mb", "control-character"),
            ("line separator", "a\u2028b", "control-character"),
            ("bidirectional override", "a\u202eb", "control-character"),
            ("lone surrogate", "a\ud800b", "control-character"),
            ("GitHub token", "token ghp_" + "A" * 36, "secret-like-value"),
            ("fine-grained token", "github_pat_" + "a" * 30, "secret-like-value"),
            ("AWS key", "AKIA" + "ABCDEFGHIJKLMNOP", "secret-like-value"),
            # A key ID ends where its alphabet does, so a word character after it does not hide it.
            ("AWS key before an underscore", "AKIA" + "ABCDEFGHIJKLMNOP" + "_suffix", "secret-like-value"),
            ("AWS key before a lowercase letter", "ASIA" + "ABCDEFGHIJKLMNOP" + "abc", "secret-like-value"),
            ("run of 17 key characters", "AKIA" + "ABCDEFGHIJKLMNOPQ", None),
            ("private key", "-----BEGIN OPENSSH PRIVATE KEY-----", "secret-like-value"),
            ("JSON Web Token after a letter", "xeyJ" + "A" * 10 + ".eyJ" + "B" * 10 + ".", "secret-like-value"),
            # The shortest JSON Web Token shape has 8 characters after each "eyJ"; one fewer on either side is not one.
            ("shortest JSON Web Token", "eyJ" + "A" * 8 + ".eyJ" + "B" * 8 + ".", "secret-like-value"),
            ("shortest JSON Web Token after a letter", "xeyJ" + "A" * 8 + ".eyJ" + "B" * 8 + ".", "secret-like-value"),
            ("short first part", "eyJ" + "A" * 7 + ".eyJ" + "B" * 8 + ".", None),
            ("short second part", "eyJ" + "A" * 8 + ".eyJ" + "B" * 7 + ".", None),
        ):
            with self.subTest(label):
                document = changed(result, lambda d: d["stages"]["install"].update(reason=text))
                self.assertCodes(check(document), *([code] if code else []))
        document = changed(result, lambda d: d["extensions"].update({"x-data": {"ghp_" + "A" * 36: 1}}))
        self.assertCodes(check(document), "secret-like-value")
        self.assertNotIn("ABCDEFGHIJKLMNOP", validator.plain("key AKIA" + "ABCDEFGHIJKLMNOP" + "_suffix"))
        # A sample for every shape, found on its own and right after a letter, also one outside ASCII.
        samples = ("ghp_" + "A" * 36, "github_pat_" + "a" * 30, "AKIA" + "ABCDEFGHIJKLMNOP", "-----BEGIN OPENSSH PRIVATE KEY-----", "xoxb-" + "A" * 12, "eyJ" + "A" * 10 + ".eyJ" + "B" * 10 + ".")
        for label, shape in validator.SECRET_SHAPES:
            self.assertTrue(any(shape.search(sample) for sample in samples), f"no sample for {label}")
        for sample in samples:
            for before in ("", "x", "\u00e9"):
                with self.subTest(sample=sample[:6], before=before):
                    document = changed(result, lambda d: d["stages"]["install"].update(reason=before + sample))
                    self.assertCodes(check(document), "secret-like-value")
        # A long run with many "eyJ" in it is searched in linear time; a quadratic search takes seconds here.
        started = time.perf_counter()
        for text in ("eyJ" * 40000, "-eyJ" * 30000, "eyJ" * 20000 + "." + "eyJ" * 20000):
            self.assertFalse([label for label, shape in validator.SECRET_SHAPES if shape.search(text)])
        self.assertLess(time.perf_counter() - started, 2)


class EnvironmentRuleTests(Case):
    def test_layer_structure(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        layers = lambda d: d["environment"]["layers"]  # noqa: E731
        at = "/environment/layers/"
        for label, change, code, path in (
            ("host inside", lambda d: layers(d)[1].update(kind=fact("host", "synthetic:x")), "layer-structure", at + "1/kind"),
            ("WSL2 outermost", lambda d: layers(d).pop(0), "layer-structure", at + "0/kind"),
            ("container outermost", lambda d: d["environment"].update(layers=layers(d)[2:]), "layer-structure", at + "0/kind"),
            ("VM outermost", lambda d: (layers(d)[1].update(kind=fact("vm", "synthetic:x"), technology=fact("hyper-v", "synthetic:x")), layers(d).pop(0)), "layer-structure", at + "0/kind"),
            ("container without container facts", lambda d: layers(d)[2].pop("container"), "layer-structure", at + "2"),
            ("container facts on a VM", lambda d: layers(d)[1].update(container=layers(d)[2]["container"]), "layer-structure", at + "1/container"),
            ("container with its own kernel", lambda d: layers(d)[2]["os"].update(kernel=fact("6.6", "synthetic:x")), "layer-structure", at + "2/os/kernel"),
            ("hypervisor on a container layer", lambda d: layers(d)[2].update(technology=fact("kvm", "synthetic:x")), "layer-structure", at + "2/technology"),
            ("engine on a WSL2 layer", lambda d: layers(d)[1].update(technology=fact("docker", "synthetic:x")), "layer-structure", at + "1/technology"),
            ("unlisted technology on a WSL2 layer", lambda d: layers(d)[1].update(technology=fact("wsl3", "synthetic:x")), "layer-structure", at + "1/technology"),
            ("WSL2 on a container layer", lambda d: layers(d)[2].update(technology=fact("wsl2", "synthetic:x")), "layer-structure", at + "2/technology"),
            ("technology on the host", lambda d: layers(d)[0].update(technology=fact("hyper-v", "synthetic:x")), "layer-structure", at + "0/technology"),
            ("WSL2 on Linux", lambda d: layers(d)[0]["os"].update(family=fact("linux", "synthetic:x")), "os-contradiction", at + "1/kind"),
            ("WSL2 running Windows", lambda d: (layers(d)[1]["os"].update(family=fact("windows", "synthetic:x")), layers(d)[2]["os"].update(family=fact("windows", "synthetic:x"))), "os-contradiction", at + "1/os/family"),
            ("Windows container on a Linux kernel", lambda d: layers(d)[2]["os"].update(family=fact("windows", "synthetic:x")), "os-contradiction", at + "2/os/family"),
            ("emulated host", lambda d: layers(d)[0]["cpu"].update(execution=fact("emulated", "synthetic:x")), "cpu-contradiction", at + "0/cpu/execution"),
            ("foreign CPU declared native", lambda d: layers(d)[2]["cpu"].update(arch=fact("x86_64", "synthetic:x")), "cpu-contradiction", at + "2/cpu/execution"),
            ("architecture alias", lambda d: layers(d)[2]["cpu"].update(arch=fact("arm64", "synthetic:x")), "arch-name", at + "2/cpu/arch"),
        ):
            with self.subTest(label):
                self.assertFinding(check(changed(result, change), policy=False), code, path)
        engine_vm = changed(fixture("linux-x64-vm.result"), lambda d: layers(d)[1].update(technology=fact("podman", "synthetic:x")))
        self.assertCodes(check(engine_vm), "layer-structure")
        self.assertFinding(check(engine_vm), "layer-structure", at + "1/technology")

    def test_emulation_reaches_every_inner_layer(self):
        result = fixture("vm-podman-quadlet-systemd.result")
        layers = lambda d: d["environment"]["layers"]  # noqa: E731
        python = lambda d: d["workload"]["runtimes"]["python"]  # noqa: E731

        def arm_python(d, execution):
            python(d).update(platform=fact("linux-aarch64", "synthetic:x"), arch=fact("aarch64", "synthetic:x"),
                             soabi=fact("cpython-312-aarch64-linux-gnu", "synthetic:x"), execution=fact(execution, "synthetic:x"))
            d["workload"]["runtimes"]["node"].update(arch=unknown(), execution=unknown())

        def emulated_container(d):
            layers(d)[2]["cpu"].update(arch=fact("aarch64", "synthetic:x"), execution=fact("emulated", "synthetic:x"))
            arm_python(d, "native")

        def emulated_vm(d):
            layers(d)[1]["cpu"].update(arch=fact("aarch64", "synthetic:x"), execution=fact("emulated", "synthetic:x"))
            layers(d)[2]["cpu"].update(arch=fact("aarch64", "synthetic:x"), execution=fact("native", "synthetic:x"))
            arm_python(d, "emulated")

        def foreign_host(d):
            layers(d)[0]["cpu"].update(arch=fact("aarch64", "synthetic:x"))
            layers(d)[1]["cpu"].update(arch=unknown())

        for label, change, path in (
            ("native runtime in an emulated container", emulated_container, "/workload/runtimes/python/execution"),
            ("native container in an emulated VM", emulated_vm, "/environment/layers/2/cpu/execution"),
            ("x86-64 container declared native on an aarch64 host", foreign_host, "/environment/layers/2/cpu/execution"),
        ):
            with self.subTest(label):
                self.assertFinding(check(changed(result, change)), "cpu-contradiction", path)
        self.assertValid(check(changed(result, lambda d: (emulated_container(d), python(d).update(execution=fact("emulated", "synthetic:x"))))))

    def test_emulation_is_recorded_as_emulated(self):
        def emulate(d):
            layers = d["environment"]["layers"]
            layers[2]["cpu"].update(arch=fact("x86_64", "synthetic:x"), execution=fact("emulated", "synthetic:x"))
            python = d["workload"]["runtimes"]["python"]
            python.update(platform=fact("linux-x86_64", "synthetic:x"), arch=fact("x86_64", "synthetic:x"),
                          soabi=fact("cpython-312-x86_64-linux-gnu", "synthetic:x"), execution=fact("emulated", "synthetic:x"))
            d["workload"]["runtimes"]["node"].update(arch=fact("x86_64", "synthetic:x"), execution=fact("emulated", "synthetic:x"))
        self.assertValid(check(changed(fixture("windows-arm64-wsl2-podman.result"), emulate)))

    def test_runtime_and_abi_contradictions(self):
        result = fixture("linux-x64-vm.result")
        python = lambda d: d["workload"]["runtimes"]["python"]  # noqa: E731
        node = lambda d: d["workload"]["runtimes"]["node"]  # noqa: E731
        for label, change, code in (
            ("Windows Python on Linux", lambda d: python(d).update(platform=fact("win-amd64", "synthetic:x")), "os-contradiction"),
            ("macOS Node on Linux", lambda d: node(d).update(platform=fact("darwin", "synthetic:x")), "os-contradiction"),
            ("platform tag and arch differ", lambda d: python(d).update(arch=fact("aarch64", "synthetic:x"), soabi=unknown(), execution=fact("emulated", "synthetic:x")), "abi-contradiction"),
            ("32-bit pointers on x86_64", lambda d: python(d).update(pointer_bits=fact(32, "synthetic:x")), "abi-contradiction"),
            ("cache tag of another version", lambda d: python(d).update(cache_tag=fact("cpython-313", "synthetic:x")), "abi-contradiction"),
            ("ABI tag of another version", lambda d: python(d).update(soabi=fact("cpython-313-x86_64-linux-gnu", "synthetic:x")), "abi-contradiction"),
            ("free-threaded ABI tag on a GIL build", lambda d: python(d).update(soabi=fact("cpython-312t-x86_64-linux-gnu", "synthetic:x")), "abi-contradiction"),
            ("ABI tag of another architecture", lambda d: python(d).update(soabi=fact("cpython-312-aarch64-linux-gnu", "synthetic:x")), "abi-contradiction"),
            ("Windows ABI tag on Linux", lambda d: python(d).update(soabi=fact("cp312-win_amd64", "synthetic:x")), "os-contradiction"),
            ("no GIL on a GIL build", lambda d: python(d).update(gil_enabled=fact(False, "synthetic:x")), "abi-contradiction"),
            ("glibc with a musl ABI tag", lambda d: python(d).update(soabi=fact("cpython-312-x86_64-linux-musl", "synthetic:x")), "abi-contradiction"),
            ("aarch64 Node declared native on x86_64", lambda d: node(d).update(arch=fact("aarch64", "synthetic:x")), "cpu-contradiction"),
        ):
            with self.subTest(label):
                self.assertIn(code, check(changed(result, change)).codes())

    def test_the_abi_tag_names_the_build_architecture(self):
        result = fixture("linux-x64-vm.result")
        other = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(soabi=fact("cpython-312-aarch64-linux-gnu", "synthetic:x")))
        self.assertCodes(check(other), "abi-contradiction")
        self.assertFinding(check(other), "abi-contradiction", "/workload/runtimes/python/soabi")
        # The C library in the tag must agree with the libc fact when both are known.
        for libc, soabi in ((None, "cpython-312-x86_64-linux-musl"), ("glibc 2.39", "cpython-312-x86_64-linux-gnu"), ("musl 1.2.5", "cpython-312-x86_64-linux-musl")):
            with self.subTest(libc=libc, soabi=soabi):
                libc_fact = unknown() if libc is None else fact(libc, "synthetic:x")
                self.assertValid(check(changed(result, lambda d: d["workload"]["runtimes"]["python"].update(libc=libc_fact, soabi=fact(soabi, "synthetic:x")))))
        for libc, soabi in (("glibc 2.39", "cpython-312-x86_64-linux-musl"), ("musl 1.2.5", "cpython-312-x86_64-linux-gnu"),
                            ("musl 1.2.5", "cpython-312-x86_64-linux-gnux32")):
            with self.subTest(libc=libc, soabi=soabi):
                mixed = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(libc=fact(libc, "synthetic:x"), soabi=fact(soabi, "synthetic:x")))
                self.assertCodes(check(mixed), "abi-contradiction")
                self.assertFinding(check(mixed), "abi-contradiction", "/workload/runtimes/python/libc")
        # The platform part of the tag is checked even when the version is not known.
        no_version = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(version=unknown()))
        for soabi, code in (("cpython-312-aarch64-linux-gnu", "abi-contradiction"), ("cp312-win_amd64", "os-contradiction")):
            with self.subTest(soabi=soabi, version="unknown"):
                self.assertFinding(check(changed(no_version, lambda d: d["workload"]["runtimes"]["python"].update(soabi=fact(soabi, "synthetic:x")))), code, "/workload/runtimes/python/soabi")
        self.assertEqual(validator.soabi_target("cpython-312-arm-linux-gnueabihf"), ("linux", "armv7"))
        self.assertEqual(validator.soabi_target("cpython-313t-powerpc64le-linux-gnu"), ("linux", "ppc64le"))
        self.assertEqual(validator.soabi_target("cp313t-win_arm64"), ("windows", "aarch64"))
        self.assertEqual(validator.soabi_target("cpython-312-darwin"), ("macos", None))
        self.assertEqual(validator.soabi_target("pypy310-pp73-x86_64-linux-gnu"), (None, None))
        self.assertEqual(validator.soabi_target("cpython-313td-x86_64-linux-gnu"), ("linux", "x86_64"))
        self.assertEqual(validator.soabi_target("cpython-312d-darwin"), ("macos", None))
        self.assertEqual(validator.soabi_target("cp313d-win_amd64"), ("windows", "x86_64"))
        tag = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(arch=unknown(), soabi=fact("cpython-312-x86_64-linux-gnu", "synthetic:x")))
        platform_only = changed(tag, lambda d: d["workload"]["runtimes"]["python"].update(arch=fact("aarch64", "synthetic:x"), soabi=unknown(), execution=fact("emulated", "synthetic:x")))
        self.assertFinding(check(platform_only), "abi-contradiction", "/workload/runtimes/python/arch")
        self.assertCodes(check(platform_only), "abi-contradiction")

    def test_an_abi_tag_the_validator_cannot_read_is_reported(self):
        result = fixture("linux-x64-vm.result")
        odd = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(soabi=fact("cpython-312-x86_64-cygwin", "synthetic:x")))
        findings = check(odd)
        self.assertValid(findings)
        self.assertFinding(findings, "unrecognized-value", "/workload/runtimes/python/soabi", level="warning")
        debug = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(soabi=fact("cpython-312d-x86_64-linux-gnu", "synthetic:x")))
        self.assertNotIn("unrecognized-value", check(debug).codes("warning"))

    def test_windows_and_macos_platform_tags_name_the_build(self):
        result = fixture("windows-arm64-native.result")
        python = lambda d: d["workload"]["runtimes"]["python"]  # noqa: E731
        x86_on_arm = changed(result, lambda d: python(d).update(platform=fact("win32", "synthetic:x"), arch=fact("x86_64", "synthetic:x"),
                                                                 soabi=unknown(), execution=fact("emulated", "synthetic:x")))
        self.assertFinding(check(x86_on_arm), "abi-contradiction", "/workload/runtimes/python/arch")
        amd64_tag = changed(result, lambda d: python(d).update(soabi=fact("cp312-win_amd64", "synthetic:x")))
        self.assertFinding(check(amd64_tag), "abi-contradiction", "/workload/runtimes/python/soabi")
        self.assertEqual(validator.python_platform_target("macosx-14.0-universal2"), ("macos", None))

    def test_32_bit_code_on_a_64_bit_kernel_of_its_family_is_native(self):
        def thirty_two_bit(d):
            d["workload"]["runtimes"]["python"].update(arch=fact("x86", "synthetic:x"), pointer_bits=fact(32, "synthetic:x"),
                                                        soabi=fact("cpython-312-i386-linux-gnu", "synthetic:x"))
        self.assertValid(check(changed(fixture("linux-x64-vm.result"), thirty_two_bit)))

        def x86_on_arm(d):
            d["workload"]["runtimes"]["python"].update(arch=fact("x86", "synthetic:x"), pointer_bits=fact(32, "synthetic:x"))
        self.assertIn("cpu-contradiction", check(changed(fixture("windows-arm64-wsl2-podman.result"), x86_on_arm)).codes())

    def test_a_free_threaded_build_is_consistent(self):
        def free_threaded(d):
            python = d["workload"]["runtimes"]["python"]
            python.update(version=fact("3.15.0", "synthetic:x"), cache_tag=fact("cpython-315", "synthetic:x"),
                          soabi=fact("cpython-315t-x86_64-linux-gnu", "synthetic:x"),
                          free_threaded_build=fact(True, "synthetic:x"), gil_enabled=fact(False, "synthetic:x"))
        self.assertValid(check(changed(fixture("linux-x64-vm.result"), free_threaded)))

    def test_unrecognized_values_warn_and_change_nothing_else(self):
        def unusual(d):
            d["profile_id"] = None
            layer = d["environment"]["layers"][1]
            layer["cpu"]["arch"] = fact("e2k", "synthetic:x")
            layer["technology"] = fact("bhyve", "synthetic:x")
            layer["os"]["family"] = fact("haiku", "synthetic:x")
            d["workload"]["runtimes"]["python"].update(platform=fact("haiku-e2k", "synthetic:x"), arch=fact("e2k", "synthetic:x"),
                                                        soabi=unknown(), execution=unknown())
            d["workload"]["runtimes"]["node"].update(platform=fact("haiku", "synthetic:x"), arch=unknown(), execution=unknown())
            d["compute"]["backends"].append({"id": "npu0", "kind": "npu", "api": "vendor-sdk", "available": unknown(),
                                             "device": unknown(), "version": unknown(), "features": unknown()})
        findings = check(changed(fixture("linux-x64-vm.result"), unusual))
        self.assertValid(findings)
        self.assertCodes(findings, "unrecognized-value", level="warning")
        for path in ("/environment/layers/1/cpu/arch", "/environment/layers/1/os/family", "/environment/layers/1/technology", "/compute/backends/1/api"):
            self.assertFinding(findings, "unrecognized-value", path, level="warning")

    def test_identity(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        identity = lambda d: d["workload"]["identity"]  # noqa: E731
        superuser = changed(result, lambda d: identity(d).update(superuser=fact(True, "synthetic:x")))
        self.assertIn("identity-contradiction", check(superuser, policy=False).codes())
        initial = changed(result, lambda d: identity(d)["user_namespace"].update(initial=fact(True, "synthetic:x")))
        self.assertCodes(check(initial), "identity-contradiction")
        for family in ("macos", "freebsd"):
            with self.subTest(family):
                root = changed(fixture("linux-x64-vm.result"), lambda d, family=family: (
                    d["environment"]["layers"][-1]["os"].update(family=fact(family, "synthetic:x")), identity(d).update(uid=fact(0, "synthetic:x"))))
                self.assertIn("identity-contradiction", check(root, policy=False).codes())
        nested_full_map = changed(fixture("linux-x64-vm.result"), lambda d: identity(d)["user_namespace"].update(initial=fact(False, "synthetic:x")))
        self.assertValid(check(nested_full_map))


class StageRuleTests(Case):
    def stage(self, document, name, **values):
        def change(d):
            d["stages"][name] = values
            d["overall"] = validator.overall_of(d["stages"])
        return changed(document, change)

    def test_stage_fields(self):
        result = fixture("linux-x64-vm.result")
        escalation = {"escalation": {"value": "none", "provenance": "declared"}}
        for label, values in (
            ("not-run with evidence", dict(status="not-run", reason="x", evidence=["synthetic:log"])),
            ("not-run without reason", dict(status="not-run")),
            ("failed without evidence", dict(status="failed", reason="x", privilege=escalation)),
            ("failed without reason", dict(status="failed", evidence=["synthetic:log"], privilege=escalation)),
            ("passed without evidence", dict(status="passed", privilege=escalation)),
            ("passed without privilege", dict(status="passed", evidence=["synthetic:log"])),
            ("blocked without reason", dict(status="blocked")),
        ):
            with self.subTest(label):
                self.assertIn("stage-fields", check(self.stage(result, "install", **values)).codes())

    def test_only_security_and_performance_can_be_not_applicable(self):
        result = fixture("linux-x64-vm.result")
        for name in ("detect", "install", "initialize", "execute", "correctness"):
            with self.subTest(name):
                self.assertFinding(check(self.stage(result, name, status="not-applicable", reason="x")), "stage-fields", f"/stages/{name}/status")
        for name in ("security", "performance"):
            with self.subTest(name):
                self.assertValid(check(self.stage(result, name, status="not-applicable", reason="x")))

        # Every stage after detect marked not-applicable does not make a passed run.
        def detect_only(d):
            for name in validator.STAGES[1:]:
                d["stages"][name] = {"status": "not-applicable", "reason": "x"}
            d["overall"] = validator.overall_of(d["stages"])
        findings = check(changed(result, detect_only))
        self.assertCodes(findings, "stage-fields")
        self.assertFinding(findings, "stage-fields", "/stages/install/status")

    def test_a_real_stage_cites_a_log_or_an_artifact(self):
        result, profile = real_result()
        passed = dict(status="passed", privilege={"escalation": {"value": "none", "provenance": "declared"}})
        for name, evidence, valid in (
            ("install", ["doc:README.md"], False),
            ("install", ["tool:collect-environment"], False),
            ("install", ["file:/proc/self/status"], False),
            ("install", ["log:uv pip sync"], True),
            ("install", ["artifact:install-log"], True),
            ("detect", ["doc:README.md"], False),
            ("detect", ["tool:collect-environment"], True),
        ):
            with self.subTest(name=name, evidence=evidence):
                findings = check(self.stage(result, name, evidence=evidence, **passed), profile)
                if valid:
                    self.assertValid(findings)
                else:
                    self.assertFinding(findings, "stage-fields", f"/stages/{name}/evidence")

    def test_detect_cannot_pass_with_probe_errors(self):
        result = fixture("linux-x64-vm.result")
        self.assertEqual(result["stages"]["detect"]["status"], "passed")
        broken = changed(result, lambda d: d.update(probe_errors=[{"probe": "isolation", "error": "ValueError raised by the isolation probe"}]))
        self.assertCodes(check(broken), "stage-fields")
        self.assertFinding(check(broken), "stage-fields", "/stages/detect/status")

    def test_a_stage_cannot_pass_before_its_prerequisites(self):
        result = fixture("linux-x64-vm.result")
        passed = dict(status="passed", evidence=["synthetic:log"], privilege={"escalation": {"value": "none", "provenance": "declared"}})
        for name in ("initialize", "correctness", "performance"):
            with self.subTest(name):
                self.assertCodes(check(self.stage(result, name, **passed)), "stage-order")
        ran = result
        for name in ("detect", "install", "initialize", "execute"):
            ran = self.stage(ran, name, **passed)
        ran = self.stage(ran, "correctness", status="failed", reason="wrong output", evidence=["synthetic:log"], privilege=passed["privilege"])
        self.assertFinding(check(self.stage(ran, "performance", **passed)), "stage-order", "/stages/performance/status")
        failed_detect = self.stage(result, "detect", status="failed", reason="probe error", evidence=["synthetic:log"], privilege=passed["privilege"])
        self.assertCodes(check(self.stage(failed_detect, "security", **passed)), "stage-order")

    def test_not_run_stages_cannot_make_the_run_pass(self):
        result = fixture("linux-x64-vm.result")
        self.assertEqual(result["overall"], "incomplete")
        self.assertCodes(check(changed(result, lambda d: d.update(overall="passed"))), "overall-mismatch")
        failed = fixture("windows-arm64-native.result")
        self.assertEqual(failed["overall"], "failed")
        self.assertCodes(check(changed(failed, lambda d: d.update(overall="incomplete"))), "overall-mismatch")

    def test_a_blocked_stage_leaves_the_run_incomplete(self):
        result = fixture("linux-x64-vm.result")
        passed = dict(status="passed", evidence=["synthetic:log"], privilege={"escalation": {"value": "none", "provenance": "declared"}})
        ran = result
        for name in ("install", "initialize", "execute", "correctness"):
            ran = self.stage(ran, name, **passed)
        ran = self.stage(ran, "security", status="not-applicable", reason="x")
        ran = self.stage(ran, "performance", status="blocked", reason="no benchmark runner")
        self.assertEqual(validator.overall_of(ran["stages"]), "incomplete")
        self.assertValid(check(ran))
        for overall in ("passed", "failed"):
            with self.subTest(overall=overall):
                self.assertFinding(check(changed(ran, lambda d: d.update(overall=overall))), "overall-mismatch", "/overall")
        failed = self.stage(ran, "correctness", status="failed", reason="wrong output", evidence=["synthetic:log"], privilege=passed["privilege"])
        self.assertEqual(validator.overall_of(failed["stages"]), "failed")

    def test_a_failure_is_not_recorded_as_not_run(self):
        result = fixture("windows-arm64-native.result")
        hidden = self.stage(result, "install", status="not-run", reason="x", evidence=["synthetic:uv pip sync log"])
        self.assertCodes(check(hidden), "stage-fields")

    def test_required_stages_must_pass(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        skipped = self.stage(result, "correctness", status="not-run", reason="x")
        self.assertCodes(check(skipped, profile), "required-stage")

    def test_cpu_fallback_is_not_an_accelerator_pass(self):
        result, profile = pair("kubernetes-arm64-cuda")
        execute = lambda d: d["stages"]["execute"]  # noqa: E731
        for label, change, code, path in (
            ("fell back to the CPU", lambda d: execute(d)["compute"].update(effective="cpu"), "accelerator-fallback", "/stages/execute/compute"),
            ("nothing recorded as running", lambda d: execute(d)["compute"].update(effective=None), "accelerator-fallback", "/stages/execute/compute"),
            ("unknown backend requested", lambda d: execute(d)["compute"].update(requested="rocm"), "backend-reference", "/stages/execute/compute/requested"),
            ("unknown backend ran", lambda d: execute(d)["compute"].update(effective="rocm"), "backend-reference", "/stages/execute/compute/effective"),
            ("unavailable backend ran", lambda d: d["compute"]["backends"][1].update(available=unknown()), "backend-reference", "/stages/execute/compute/effective"),
            ("duplicate backend id", lambda d: d["compute"]["backends"].append(copy.deepcopy(d["compute"]["backends"][1])), "backend-reference", "/compute/backends/1/id"),
            ("CPU API on a GPU", lambda d: d["compute"]["backends"][1].update(api="cpu"), "backend-reference", "/compute/backends/1"),
        ):
            with self.subTest(label):
                self.assertFinding(check(changed(result, change), profile), code, path)
        other_gpu = {"id": "cuda1", "kind": "gpu", "api": "cuda", "available": fact(True, "synthetic:x"), "device": unknown(), "version": unknown(), "features": unknown()}
        to_other_gpu = changed(result, lambda d: (d["compute"]["backends"].append(other_gpu), execute(d)["compute"].update(effective="cuda1")))
        self.assertFinding(check(to_other_gpu), "accelerator-fallback", "/stages/execute/compute")
        npu = fixture("windows-arm64-native.result")
        npu_id = next(backend["id"] for backend in npu["compute"]["backends"] if backend["kind"] == "npu")

        def npu_on_cpu(d):
            for name in ("detect", "install", "initialize", "execute"):
                d["stages"][name] = {"status": "passed", "evidence": ["synthetic:log"], "privilege": {"escalation": {"value": "none", "provenance": "declared"}}}
            d["stages"]["execute"]["compute"] = {"requested": npu_id, "effective": "cpu"}
            d["overall"] = validator.overall_of(d["stages"])
        self.assertFinding(check(changed(npu, npu_on_cpu)), "accelerator-fallback", "/stages/execute/compute")

    def test_an_accelerator_profile_needs_its_stages_on_the_accelerator(self):
        result, profile = pair("kubernetes-arm64-cuda")
        self.assertEqual(profile["accelerator_backend"], "cuda")

        def on_cpu(d):
            d["compute"]["backends"][1]["available"] = fact(False, "synthetic:x")
            for name in ("execute", "correctness"):
                d["stages"][name]["compute"] = {"requested": "cpu", "effective": "cpu"}

        def unrecorded(d):
            for name in ("execute", "correctness"):
                d["stages"][name].pop("compute")

        for label, change in (("ran on the CPU", on_cpu), ("no backend recorded", unrecorded)):
            with self.subTest(label):
                findings = check(changed(result, change), profile)
                self.assertFinding(findings, "required-stage", "/stages/execute/compute")
                self.assertFinding(findings, "required-stage", "/stages/correctness/compute")
        # The CPU was asked for, so this is no fallback, but the stage still did not request the accelerator.
        cpu_requested = changed(result, lambda d: d["stages"]["execute"]["compute"].update(requested="cpu"))
        self.assertCodes(check(cpu_requested, profile), "required-stage")
        self.assertFinding(check(cpu_requested, profile), "required-stage", "/stages/execute/compute")

        def performance_on_cpu(d):
            d["stages"]["performance"] = {"status": "passed", "evidence": ["synthetic:benchmark log"], "privilege": {"escalation": {"value": "none", "provenance": "declared"}},
                                          "compute": {"requested": "cpu", "effective": "cpu"}}
        performance_profile = changed(profile, lambda d: d["required_stages"].append("performance"))
        self.assertFinding(check(changed(result, performance_on_cpu), performance_profile), "required-stage", "/stages/performance/compute")
        for label, name in (("missing backend", "rocm"), ("CPU backend", "cpu")):
            with self.subTest(label):
                self.assertFinding(check(changed(profile, lambda d: d.update(accelerator_backend=name))), "backend-reference", "/accelerator_backend")
        # A CPU lane names no accelerator backend.
        self.assertValid(check(changed(profile, lambda d: d.update(accelerator_backend=None))))

    def test_a_cpu_backend_may_use_another_api(self):
        result = fixture("linux-x64-vm.result")
        wasm = {"id": "wasm", "kind": "cpu", "api": "wasm", "available": unknown(), "device": unknown(), "version": unknown(), "features": unknown()}
        self.assertValid(check(changed(result, lambda d: d["compute"]["backends"].append(wasm))))

    def test_a_failed_stage_may_record_the_fallback_it_hit(self):
        result, profile = pair("kubernetes-arm64-cuda")

        def fail(d):
            d["stages"]["correctness"].update(status="failed", reason="the CUDA kernel did not load; the CPU ran instead")
            d["stages"]["correctness"]["compute"]["effective"] = "cpu"
            d["overall"] = "failed"
        findings = check(changed(result, fail), profile)
        self.assertCodes(findings, "required-stage")

    def test_a_stage_that_did_not_run_used_no_backend(self):
        result = fixture("linux-x64-vm.result")
        document = changed(result, lambda d: d["stages"]["install"].update(compute={"requested": "cpu", "effective": "cpu"}))
        self.assertCodes(check(document), "backend-reference")


class PolicyTests(Case):
    def test_the_default_policy_rejects_root_privileged_devices_capabilities_and_escalation(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        container = lambda d: d["environment"]["layers"][2]["container"]  # noqa: E731
        for label, change, code in (
            ("root workload", lambda d: d["workload"]["identity"].update(superuser=fact(True, "synthetic:x"), uid=fact(0, "synthetic:x")), "policy-app-non-root"),
            ("unknown user", lambda d: d["workload"]["identity"].update(superuser=unknown()), "policy-app-non-root"),
            ("privileged container", lambda d: container(d).update(privileged=fact(True, "synthetic:x")), "policy-no-privileged-container"),
            ("unknown privileged state", lambda d: container(d).update(privileged=unknown()), "policy-no-privileged-container"),
            ("device passed in", lambda d: container(d).update(devices=fact(["fuse"], "synthetic:x")), "policy-minimal-devices"),
            ("capability held", lambda d: d["workload"]["isolation"]["capabilities"].update(effective=fact(["cap_net_admin"], "synthetic:x")), "policy-minimal-capabilities"),
            ("sudo in a stage", lambda d: d["stages"]["install"]["privilege"].update(escalation={"value": "sudo", "provenance": "declared"}), "policy-no-privilege-escalation"),
        ):
            with self.subTest(label):
                self.assertCodes(check(changed(result, change)), code)

    def test_capabilities_apply_on_linux(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        not_applicable = {"value": None, "provenance": "not-applicable", "reason": "x"}
        findings = check(changed(result, lambda d: d["workload"]["isolation"]["capabilities"].update(effective=not_applicable)))
        self.assertFinding(findings, "policy-minimal-capabilities", "/workload/isolation/capabilities/effective")
        # Windows has no Linux capabilities.
        self.assertValid(check(fixture("windows-arm64-native.result")))
        # An unknown OS family is not shown to be one without them.
        unknown_family = changed(result, lambda d: (d["environment"]["layers"][-1]["os"].update(family=unknown()),
                                                    d["workload"]["isolation"]["capabilities"].update(effective=not_applicable)))
        self.assertFinding(check(unknown_family), "policy-minimal-capabilities", "/workload/isolation/capabilities/effective")

    def test_a_workload_not_shown_outside_a_container_does_not_pass_the_container_rules(self):
        result = fixture("linux-x64-vm.result")
        path = "/workload/isolation/pid_namespace/initial"
        for label, initial in (("another PID namespace", fact(False, "synthetic:x")), ("unknown", unknown())):
            with self.subTest(label):
                findings = check(changed(result, lambda d: d["workload"]["isolation"]["pid_namespace"].update(initial=initial)))
                self.assertCodes(findings, "policy-minimal-devices", "policy-no-privileged-container")
                self.assertFinding(findings, "policy-no-privileged-container", path)
                self.assertFinding(findings, "policy-engine-rootless", path, level="warning")
        self.assertValid(check(result))
        # An unknown OS family fails closed, like Linux.
        unknown_family = changed(result, lambda d: (d["environment"]["layers"][-1]["os"].update(family=unknown()),
                                                    d["workload"]["isolation"]["pid_namespace"].update(initial=fact(False, "synthetic:x"))))
        self.assertFinding(check(unknown_family), "policy-no-privileged-container", path)
        # WSL 2 gives every distribution its own PID namespace, so there the namespace does not suggest a container.
        wsl2 = fixture("windows-arm64-wsl2-podman.result")
        distribution = changed(wsl2, lambda d: (d["environment"]["layers"].pop(2), d.update(profile_id=None),
                                                d["workload"]["isolation"]["pid_namespace"].update(initial=fact(False, "synthetic:x"))))
        self.assertValid(check(distribution))
        self.assertCodes(check(distribution), level="warning")

    def test_rootless_engine_and_non_root_workload_are_independent(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        root_in_rootless = changed(result, lambda d: d["workload"]["identity"].update(
            superuser=fact(True, "synthetic:x"), uid=fact(0, "synthetic:x"),
            user_namespace={"initial": fact(False, "synthetic:x"), "uid_map": fact([[0, 1000, 1], [1, 100000, 65536]], "synthetic:x")}))
        self.assertCodes(check(root_in_rootless, profile), "policy-app-non-root")
        rootful = changed(result, lambda d: (d["environment"]["layers"][2]["container"].update(engine_mode=fact("rootful", "synthetic:x")), d.update(profile_id=None)))
        findings = check(rootful)
        self.assertCodes(findings)
        self.assertCodes(findings, "policy-engine-rootless", level="warning")
        rootful_profile = changed(profile, lambda d: d["environment"]["layers"][2]["container"].update(engine_mode=unknown()))
        self.assertCodes(check(changed(rootful, lambda d: d.update(profile_id=profile["id"])), rootful_profile), "policy-engine-rootless")

    def test_exceptions_need_approval_and_a_matching_scope(self):
        result, profile = pair("kubernetes-arm64-cuda")
        exception = lambda d: d["policy"]["exceptions"][0]  # noqa: E731
        pending = changed(profile, lambda d: exception(d)["approval"].update(status="pending"))
        self.assertCodes(check(result, pending), "policy-exception-not-approved")
        other_device = changed(profile, lambda d: exception(d)["scope"].update(devices=["fuse"]))
        self.assertCodes(check(result, other_device), "policy-minimal-devices")
        self.assertCodes(check(result), "policy-minimal-devices")
        for label, change in (
            ("approved without a reference", lambda d: exception(d)["approval"].pop("reference")),
            ("scope of another rule", lambda d: exception(d)["scope"].update(stages=["execute"])),
            ("no scope", lambda d: exception(d).update(scope={})),
            ("duplicate id", lambda d: d["policy"]["exceptions"].append(copy.deepcopy(exception(d)))),
        ):
            with self.subTest(label):
                self.assertIn("policy-exception", check(changed(profile, change)).codes())
        # The schema checks only the date's shape; the day must exist.
        for date in ("2026-99-99", "2026-02-30", "2026-00-10", "2025-02-29"):
            with self.subTest(date=date):
                self.assertFinding(check(changed(profile, lambda d: exception(d)["approval"].update(date=date))), "policy-exception", "/policy/exceptions/0/approval/date")
        self.assertValid(check(changed(profile, lambda d: exception(d)["approval"].update(date="2024-02-29"))))
        for label, change in (
            ("no reason", lambda d: exception(d).pop("reason")),
            ("no end condition", lambda d: exception(d).pop("end_condition")),
            ("no verification", lambda d: exception(d).update(verification="")),
            ("unknown rule", lambda d: exception(d).update(rule="no-network")),
        ):
            with self.subTest(label):
                self.assertCodes(check(changed(profile, change)), "schema")

    def test_unused_exceptions_warn(self):
        result, profile = pair("kubernetes-arm64-cuda")
        no_gpu = changed(result, lambda d: d["environment"]["layers"][1]["container"].update(devices=fact([], "synthetic:x")))
        self.assertIn("policy-exception-unused", check(no_gpu, changed(profile, lambda d: d["environment"]["layers"][1]["container"].update(devices=unknown()))).codes("warning"))

    def test_a_profile_can_tighten_but_not_weaken_a_rule(self):
        profile = fixture("linux-x64-vm.profile")
        self.assertCodes(check(changed(profile, lambda d: d["policy"]["rules"].update({"app-non-root": "preferred"}))), "policy-weakened")
        self.assertValid(check(changed(profile, lambda d: d["policy"]["rules"].update({"engine-rootless": "required"}))))

    def test_the_policy_can_be_skipped_for_machines_it_is_not_meant_for(self):
        result = changed(fixture("linux-x64-vm.result"), lambda d: d["workload"]["identity"].update(superuser=fact(True, "synthetic:x"), uid=fact(0, "synthetic:x")))
        self.assertCodes(check(result), "policy-app-non-root")
        self.assertValid(check(result, policy=False))


class MeasuredPolicyTests(Case):
    """The policy passes only on measured facts, fails closed off Linux, and lets an exception cover only what it names."""

    def test_declared_values_in_a_result_do_not_show_compliance(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        container = lambda d: d["environment"]["layers"][2]["container"]  # noqa: E731
        identity = lambda d: d["workload"]["identity"]  # noqa: E731
        stated = lambda value: fact(value, provenance="declared")  # noqa: E731
        for label, change, code, path in (
            ("privileged", lambda d: container(d).update(privileged=stated(False)), "policy-no-privileged-container", "/environment/layers/2/container/privileged"),
            ("devices", lambda d: container(d).update(devices=stated([])), "policy-minimal-devices", "/environment/layers/2/container/devices"),
            ("superuser", lambda d: identity(d).update(superuser=stated(False)), "policy-app-non-root", "/workload/identity/superuser"),
            ("real user ID", lambda d: identity(d).update(real_uid=stated(1000)), "policy-app-non-root", "/workload/identity/real_uid"),
            ("effective capabilities", lambda d: d["workload"]["isolation"]["capabilities"].update(effective=stated([])), "policy-minimal-capabilities", "/workload/isolation/capabilities/effective"),
        ):
            with self.subTest(label):
                findings = check(changed(result, change))
                self.assertCodes(findings, code)
                self.assertFinding(findings, code, path)
        vm = fixture("linux-x64-vm.result")
        outside = changed(vm, lambda d: d["workload"]["isolation"]["pid_namespace"].update(initial=stated(True)))
        self.assertFinding(check(outside), "policy-no-privileged-container", "/workload/isolation/pid_namespace/initial")
        # A layer that a declared fact calls a container is still held to the container rules.
        declared_kind = changed(result, lambda d: (d["environment"]["layers"][2].update(kind=stated("container")), container(d).update(privileged=unknown())))
        self.assertFinding(check(declared_kind), "policy-no-privileged-container", "/environment/layers/2/container/privileged")

    def test_unknown_or_unshown_privilege_state_fails(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        unknown_mode = changed(result, lambda d: (d["environment"]["layers"][2]["container"].update(engine_mode=unknown()), d.update(profile_id=None)))
        self.assertCodes(check(unknown_mode), level="error")
        self.assertCodes(check(unknown_mode), "policy-engine-rootless", level="warning")
        required = changed(profile, lambda d: (d["policy"]["rules"].update({"engine-rootless": "required"}), d["environment"]["layers"][2]["container"].update(engine_mode=unknown())))
        self.assertCodes(check(changed(unknown_mode, lambda d: d.update(profile_id=profile["id"])), required), "policy-engine-rootless")
        self.assertCodes(check(changed(result, lambda d: d["stages"]["install"]["privilege"].update(escalation=unknown()))), "policy-no-privilege-escalation")
        failed_with_sudo = changed(result, lambda d: (d["stages"]["correctness"].update(status="failed", reason="x"),
                                                      d["stages"]["correctness"]["privilege"].update(escalation=fact("sudo", provenance="declared")),
                                                      d.update(overall="failed")))
        self.assertCodes(check(failed_with_sudo), "policy-no-privilege-escalation")

    def test_off_linux_an_unmeasured_layer_kind_fails_closed(self):
        windows = fixture("windows-arm64-native.result")
        self.assertValid(check(windows))
        layer = lambda d: d["environment"]["layers"][0]  # noqa: E731
        path = "/environment/layers/0/kind"
        for label, change in (
            ("Windows, kind unknown", lambda d: layer(d).update(kind=unknown())),
            ("Windows, kind declared", lambda d: layer(d).update(kind=fact("host", provenance="declared"))),
            ("FreeBSD, kind unknown", lambda d: (layer(d).update(kind=unknown()), layer(d)["os"].update(family=fact("freebsd", "synthetic:x")))),
        ):
            with self.subTest(label):
                findings = check(changed(windows, change), policy=True)
                self.assertFinding(findings, "policy-no-privileged-container", path)
                self.assertFinding(findings, "policy-minimal-devices", path)
                self.assertFinding(findings, "policy-engine-rootless", path, level="warning")
        # Linux capabilities pass as not-applicable only on a measured non-Linux family.
        stated_family = changed(windows, lambda d: layer(d)["os"].update(family=fact("windows", provenance="declared")))
        self.assertFinding(check(stated_family), "policy-minimal-capabilities", "/workload/isolation/capabilities/effective")
        self.assertFinding(check(changed(windows, lambda d: d["workload"]["isolation"]["capabilities"].update(effective=unknown()))),
                           "policy-minimal-capabilities", "/workload/isolation/capabilities/effective")

    def test_a_process_that_can_regain_root_fails(self):
        result = fixture("windows-arm64-wsl2-podman.result")
        identity = lambda d: d["workload"]["identity"]  # noqa: E731
        for label, change, path in (
            ("real user ID 0", lambda d: identity(d).update(real_uid=fact(0, "synthetic:x")), "/workload/identity/real_uid"),
            ("saved user ID 0", lambda d: identity(d).update(saved_uid=fact(0, "synthetic:x")), "/workload/identity/saved_uid"),
            ("saved user ID unknown", lambda d: identity(d).update(saved_uid=unknown()), "/workload/identity/saved_uid"),
            ("real user ID not applicable on Linux", lambda d: identity(d).update(real_uid={"value": None, "provenance": "not-applicable", "reason": "x"}), "/workload/identity/real_uid"),
            ("permitted capability", lambda d: d["workload"]["isolation"]["capabilities"].update(permitted=fact(["cap_setuid"], "synthetic:x")), "/workload/isolation/capabilities/permitted"),
            ("permitted capabilities unknown", lambda d: d["workload"]["isolation"]["capabilities"].update(permitted=unknown()), "/workload/isolation/capabilities/permitted"),
        ):
            with self.subTest(label):
                findings = check(changed(result, change))
                self.assertEqual(len(findings.errors), 1, findings.items)
                self.assertEqual(findings.errors[0][2], path)

    def test_an_exception_covers_only_what_it_names(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        sudo_install = changed(result, lambda d: d["stages"]["install"]["privilege"].update(escalation=fact("sudo", provenance="declared")))
        self.assertCodes(check(sudo_install, profile), "policy-no-privilege-escalation")
        approval = {"status": "approved", "reference": "https://github.com/P-R-N-D/Claus-AI (synthetic example)", "date": "2026-10-11"}
        # The profile leaves these open, so that only the policy decides.
        open_profile = changed(profile, lambda d: d["environment"]["layers"][2]["container"].update(privileged=unknown(), devices=unknown()))

        def allow(rule, **scope):
            exception = {"id": f"allow-{rule}", "rule": rule, "reason": "x", "scope": scope, "verification": "x", "end_condition": "x", "approval": dict(approval)}
            return changed(open_profile, lambda d: d["policy"].setdefault("exceptions", []).append(exception))

        sudo_allowed = allow("no-privilege-escalation", stages=["install"], escalations=["sudo"])
        self.assertValid(check(sudo_allowed))
        findings = check(sudo_install, sudo_allowed)
        self.assertValid(findings)
        self.assertFinding(findings, "policy-exception-applied", "/policy/exceptions/0", level="warning")
        for label, change in (
            ("another method", lambda d: d["stages"]["install"]["privilege"].update(escalation=fact("setuid", provenance="declared"))),
            ("an unknown method", lambda d: d["stages"]["install"]["privilege"].update(escalation=unknown())),
            ("another stage", lambda d: d["stages"]["execute"]["privilege"].update(escalation=fact("sudo", provenance="declared"))),
        ):
            with self.subTest(label):
                self.assertIn("policy-no-privilege-escalation", check(changed(result, change), sudo_allowed).codes())
        unknown_method = check(changed(result, lambda d: d["stages"]["install"]["privilege"].update(escalation=unknown())), sudo_allowed)
        self.assertIn("cannot cover a state that is not measured", unknown_method.errors[0][3])
        self.assertFinding(check(allow("no-privilege-escalation", stages=["install"])), "policy-exception", "/policy/exceptions/0/scope")
        self.assertFinding(check(allow("no-privilege-escalation", escalations=["sudo"])), "policy-exception", "/policy/exceptions/0/scope")
        # A layer exception covers a privileged container, not one whose state is unknown.
        privileged_allowed = allow("no-privileged-container", layers=[2])
        privileged = changed(result, lambda d: d["environment"]["layers"][2]["container"].update(privileged=fact(True, "synthetic:x")))
        self.assertValid(check(privileged, privileged_allowed))
        unknown_state = changed(result, lambda d: d["environment"]["layers"][2]["container"].update(privileged=unknown()))
        self.assertCodes(check(unknown_state, privileged_allowed), "policy-no-privileged-container")
        # A device exception does not cover devices that are not known.
        devices_allowed = allow("minimal-devices", devices=["fuse"])
        unknown_devices = changed(result, lambda d: d["environment"]["layers"][2]["container"].update(devices=unknown()))
        self.assertCodes(check(unknown_devices, devices_allowed), "policy-minimal-devices")
        # app-non-root needs no scope, but still cannot cover an unknown user.
        root_allowed = allow("app-non-root")
        root = changed(result, lambda d: d["workload"]["identity"].update(superuser=fact(True, "synthetic:x"), uid=fact(0, "synthetic:x"), real_uid=fact(0, "synthetic:x"), saved_uid=fact(0, "synthetic:x")))
        self.assertValid(check(root, root_allowed))
        self.assertCodes(check(changed(result, lambda d: d["workload"]["identity"].update(superuser=unknown())), root_allowed), "policy-app-non-root")

    def test_a_skipped_policy_is_reported(self):
        findings = check(fixture("linux-x64-vm.result"), policy=False)
        self.assertValid(findings)
        self.assertIn("policy-skipped", findings.codes("warning"))


class ProfileTests(Case):
    def test_support_claims_follow_the_dependency_strategy(self):
        profile = fixture("windows-arm64-native.profile")
        support = lambda d: d["support"]  # noqa: E731
        for label, change in (
            ("synthetic checked claim", lambda d: support(d).update(status="checked")),
            ("status that differs from the lane table", lambda d: support(d).update(status="statically-resolvable")),
            ("lane that is not in the table", lambda d: support(d).update(lane="Windows on Arm, native")),
            ("claim without a lane", lambda d: support(d).update(lane=None)),
            ("not assessed with a lane that is not in the table", lambda d: support(d).update(status="not-assessed", lane="Windows on Arm")),
        ):
            with self.subTest(label):
                self.assertIn("support-claim", check(changed(profile, change)).codes())
        unlisted = changed(profile, lambda d: support(d).update(status="not-assessed", lane=None))
        self.assertValid(check(unlisted))
        # A synthetic profile assesses nothing, so it may name its lane without a status from it.
        self.assertValid(check(changed(profile, lambda d: support(d).update(status="not-assessed"))))
        _, real = real_result()
        self.assertFinding(check(changed(real, lambda d: d["support"].update(status="not-assessed"))), "support-claim", "/support/status")
        # A lane with one status per condition accepts each of them.
        mac = macos_profile()
        for status, valid in (("statically-resolvable", True), ("not-supported", True), ("checked", False)):
            with self.subTest(status=status):
                findings = check(changed(mac, lambda d: d["support"].update(status=status, lane="macOS Apple Silicon")))
                self.assertEqual(not findings.errors, valid, findings.items)
        # The lane table and the platform agree with this claim, so only the synthetic marking can reject it.
        on_checked_lane = changed(fixture("linux-x64-vm.profile"), lambda d: support(d).update(status="checked"))
        self.assertCodes(check(on_checked_lane), "support-claim")
        self.assertEqual([path for _, code, path, _ in check(on_checked_lane).errors], ["/support/status"])

    def test_support_claims_are_bound_to_the_lane_platform_and_interpreter(self):
        _, real = real_result()
        self.assertValid(check(real))
        python = lambda d: d["workload"]["runtimes"]["python"]  # noqa: E731
        layers = lambda d: d["environment"]["layers"]  # noqa: E731

        def free_threaded_315(d):
            python(d).update(version=fact("3.15.0", provenance="declared"), cache_tag=fact("cpython-315", provenance="declared"),
                             soabi=fact("cpython-315t-x86_64-linux-gnu", provenance="declared"), free_threaded_build=fact(True, provenance="declared"),
                             gil_enabled=fact(False, provenance="declared"))

        def aarch64(d):
            layers(d)[1]["cpu"].update(arch=fact("aarch64", provenance="declared"))
            python(d).update(arch=fact("aarch64", provenance="declared"), platform=fact("linux-aarch64", provenance="declared"),
                             soabi=fact("cpython-312-aarch64-linux-gnu", provenance="declared"))
            d["workload"]["runtimes"]["node"].update(arch=fact("aarch64", provenance="declared"))

        for label, change, path in (
            ("Python 3.15 free-threaded", free_threaded_315, "/workload/runtimes/python"),
            ("Python 3.13", lambda d: python(d).update(version=fact("3.13.9", provenance="declared"), cache_tag=fact("cpython-313", provenance="declared"),
                                                        soabi=fact("cpython-313-x86_64-linux-gnu", provenance="declared")), "/workload/runtimes/python"),
            ("PyPy", lambda d: python(d).update(implementation=fact("pypy", provenance="declared"), cache_tag=unknown(), soabi=unknown()), "/workload/runtimes/python"),
            ("interpreter not declared", lambda d: python(d).update(free_threaded_build=unknown()), "/workload/runtimes/python"),
            ("another architecture", aarch64, "/workload/runtimes/python/arch"),
            ("architecture not declared", lambda d: python(d).update(arch=unknown(), execution=unknown()), "/workload/runtimes/python/arch"),
            ("musl", lambda d: python(d).update(soabi=fact("cpython-312-x86_64-linux-musl", provenance="declared")), "/workload/runtimes/python/soabi"),
            ("C library not declared", lambda d: python(d).update(soabi=unknown()), "/workload/runtimes/python/soabi"),
            ("another OS", lambda d: layers(d)[1]["os"].update(family=fact("freebsd", provenance="declared")), "/environment/layers/1/os/family"),
        ):
            with self.subTest(label):
                claimed = changed(real, change)
                self.assertFinding(check(claimed), "support-claim", path)
                if label in ("Python 3.15 free-threaded", "Python 3.13", "PyPy"):
                    # The lane table does not assess this interpreter, so the profile records not-assessed.
                    self.assertValid(check(changed(claimed, lambda d: d["support"].update(status="not-assessed"))))
        # The C library comes from the libc fact when the ABI tag is not declared.
        for libc, errors in (("musl 1.2.5", []), ("glibc 2.39", ["/workload/runtimes/python/soabi"])):
            with self.subTest(libc=libc):
                alpine = changed(real, lambda d: (d["support"].update(status="not-supported", lane="Linux musl (Alpine)"),
                                                  python(d).update(libc=fact(libc, provenance="declared"), soabi=unknown())))
                self.assertEqual([path for _, code, path, _ in check(alpine).errors if code == "support-claim"], errors)
        # A WSL2 lane needs a WSL2 layer, and a native Windows lane none.
        wsl = fixture("windows-arm64-wsl2-podman.profile")
        self.assertValid(check(wsl))
        no_wsl = changed(wsl, lambda d: layers(d)[1].update(kind=fact("vm", provenance="declared"), technology=fact("hyper-v", provenance="declared")))
        self.assertFinding(check(no_wsl), "support-claim", "/environment/layers")
        native = fixture("windows-arm64-native.profile")
        self.assertFinding(check(changed(native, lambda d: python(d).update(arch=fact("x86_64", provenance="declared"), platform=fact("win-amd64", provenance="declared"),
                                                                             soabi=fact("cp312-win_amd64", provenance="declared"), execution=unknown()))),
                           "support-claim", "/workload/runtimes/python/arch")
        # A contradiction is an error even in a profile that claims no status.
        self.assertFinding(check(changed(fixture("linux-x64-vm.profile"), aarch64)), "support-claim", "/workload/runtimes/python/arch")

    def test_the_validator_knows_every_lane_in_the_table(self):
        self.assertEqual(set(validator.LANE_TARGETS), set(LANES))

    def test_a_checked_claim_needs_the_lane_table(self):
        _, profile = real_result()
        claimed = changed(profile, lambda d: d.update(support={"status": "checked", "lane": "Linux x86-64 glibc", "reference": "docs/DEPENDENCY-STRATEGY.md"}))
        self.assertValid(check(claimed))
        self.assertIn("support-claim-unverified", check(claimed, lanes=None).codes("warning"))
        other = changed(claimed, lambda d: d["support"].update(lane="Linux aarch64 glibc"))
        self.assertCodes(check(other), "support-claim")

    def test_evidence_sources_name_a_real_workflow_job(self):
        _, profile = real_result()
        self.assertValid(check(profile))
        for label, source in (
            ("missing job", {"workflow": ".github/workflows/ci.yml", "job": "gpu"}),
            ("missing workflow", {"workflow": ".github/workflows/devices.yml", "job": "compatibility"}),
            ("outside the workflow directory", {"workflow": "docs/CONTEXT.md", "job": "compatibility"}),
            ("path traversal", {"workflow": ".github/workflows/../../docs/CONTEXT.md", "job": "compatibility"}),
            ("path traversal to a real workflow", {"workflow": ".github/workflows/../workflows/ci.yml", "job": "compatibility"}),
            ("trigger key, not a job", {"workflow": ".github/workflows/ci.yml", "job": "pull_request"}),
            ("step key, not a job", {"workflow": ".github/workflows/ci.yml", "job": "run"}),
        ):
            with self.subTest(label):
                self.assertCodes(check(changed(profile, lambda d: d.update(evidence_sources=[source]))), "evidence-source")
        traversal = changed(profile, lambda d: d.update(evidence_sources=[{"workflow": ".github/workflows/../workflows/ci.yml", "job": "compatibility"}]))
        self.assertFinding(check(traversal), "evidence-source", "/evidence_sources/0/workflow")
        unsourced = changed(profile, lambda d: d.update(evidence_sources=[]))
        self.assertCodes(check(unsourced, real=True), "evidence-source")
        self.assertFinding(check(unsourced), "evidence-source", "/evidence_sources")
        # A synthetic profile is recorded by no job.
        self.assertValid(check(changed(fixture("linux-x64-vm.profile"), lambda d: d.update(evidence_sources=[]))))
        # A comment at the start of a line does not end the jobs block.
        with tempfile.TemporaryDirectory() as directory:
            workflow = pathlib.Path(directory) / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("on: push\njobs:\n  backend:\n    runs-on: x\n# Compatibility\n  compatibility:\n    runs-on: x\n", encoding="utf-8")
            with mock.patch.object(validator, "ROOT", pathlib.Path(directory)):
                self.assertValid(check(profile))
                workflow.write_text("on: push\njobs:\n  backend:\n    runs-on: x\nenv:\n  compatibility: x\n", encoding="utf-8")
                self.assertCodes(check(profile), "evidence-source")

    def test_a_result_is_checked_against_what_its_profile_declares(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        layers = lambda d: d["environment"]["layers"]  # noqa: E731
        for label, change, code in (
            ("other profile id", lambda d: d.update(profile_id="synthetic-other"), "profile-link"),
            ("other Python", lambda d: d["workload"]["runtimes"]["python"].update(version=fact("3.13.9", "synthetic:x"), cache_tag=fact("cpython-313", "synthetic:x"), soabi=fact("cpython-313-aarch64-linux-gnu", "synthetic:x")), "profile-mismatch"),
            ("missing layer", lambda d: (layers(d).pop(1), layers(d)[0].update(kind=fact("host", "synthetic:x"))), "profile-mismatch"),
            ("rootful engine", lambda d: layers(d)[2]["container"].update(engine_mode=fact("rootful", "synthetic:x")), "profile-mismatch"),
            ("not established", lambda d: layers(d)[2]["os"].update(version=unknown()), "profile-unverified"),
            ("backend missing", lambda d: d["compute"]["backends"][0].update(id="cpu0"), "profile-mismatch"),
            ("backend of another kind", lambda d: d["compute"]["backends"][0].update(kind="other", api="wasm"), "profile-mismatch"),
        ):
            with self.subTest(label):
                self.assertIn(code, check(changed(result, change), profile).codes())

    def test_every_declared_fact_is_compared(self):
        result, profile = pair("kubernetes-arm64-cuda")
        self.assertValid(check(result, profile))
        layers = lambda d: d["environment"]["layers"]  # noqa: E731
        cuda = lambda d: d["compute"]["backends"][1]  # noqa: E731
        for label, change, path in (
            ("service manager scope", lambda d: layers(d)[0]["service_manager"].update(scope=fact("user", "synthetic:x")), "/environment/layers/0/service_manager/scope"),
            ("orchestration version", lambda d: layers(d)[1]["container"]["orchestration"].update(version=fact("9.9", "synthetic:x")), "/environment/layers/1/container/orchestration/version"),
            ("backend device", lambda d: cuda(d).update(device=fact("Other GPU", "synthetic:x")), "/compute/backends/1/device"),
            ("backend version", lambda d: cuda(d).update(version=fact("12.0", "synthetic:x")), "/compute/backends/1/version"),
            ("backend features", lambda d: cuda(d).update(features=fact(["fp16"], "synthetic:x")), "/compute/backends/1/features"),
            ("accelerator inventory", lambda d: d["compute"].update(accelerator_inventory=fact(False, "synthetic:x")), "/compute/accelerator_inventory"),
        ):
            with self.subTest(label):
                self.assertFinding(check(changed(result, change), profile), "profile-mismatch", path)
        self.assertFinding(check(changed(result, lambda d: cuda(d).update(available=unknown())), profile), "profile-unverified", "/compute/backends/1/available")
        # Extension objects are not facts, so vendor data the profile records is not compared.
        self.assertValid(check(changed(result, lambda d: cuda(d).pop("extensions", None)), profile))

    def test_layer_count_runtimes_and_backend_api_are_compared_on_their_own(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        fewer = changed(result, lambda d: d["environment"]["layers"].pop())
        self.assertCodes(check(fewer, profile, policy=False), "profile-mismatch")
        self.assertFinding(check(fewer, profile, policy=False), "profile-mismatch", "/environment/layers")
        result, profile = pair("linux-x64-vm")
        node = lambda d: d["workload"]["runtimes"]["node"]  # noqa: E731
        for label, change, code, path in (
            ("node version", lambda d: node(d).update(version=fact("20.0.0", "synthetic:x")), "profile-mismatch", "/workload/runtimes/node/version"),
            ("node modules ABI", lambda d: node(d).update(modules_abi=fact("115", "synthetic:x")), "profile-mismatch", "/workload/runtimes/node/modules_abi"),
            ("node platform unknown", lambda d: node(d).update(platform=unknown()), "profile-unverified", "/workload/runtimes/node/platform"),
        ):
            with self.subTest(label):
                self.assertFinding(check(changed(result, change), profile), code, path)
        result, profile = pair("kubernetes-arm64-cuda")
        other_api = changed(result, lambda d: d["compute"]["backends"][1].update(api="vulkan-compute"))
        self.assertFinding(check(other_api, profile), "profile-mismatch", "/compute/backends/1")

    def test_only_measured_values_establish_what_a_profile_declares(self):
        result, profile = pair("windows-arm64-wsl2-podman")
        stated = changed(result, lambda d: d["environment"]["layers"][2]["container"].update(engine_mode=fact("rootless", provenance="declared")))
        self.assertFinding(check(stated, profile), "profile-unverified", "/environment/layers/2/container/engine_mode")
        # This profile makes engine-rootless required.
        self.assertFinding(check(stated, profile), "policy-engine-rootless", "/environment/layers/2/container/engine_mode")

    def test_a_fact_the_profile_calls_not_applicable_is_compared(self):
        result, profile = pair("windows-arm64-native")
        self.assertEqual(profile["workload"]["runtimes"]["python"]["libc"]["provenance"], "not-applicable")
        glibc = changed(result, lambda d: d["workload"]["runtimes"]["python"].update(libc=fact("glibc 2.39", "synthetic:x")))
        self.assertFinding(check(glibc, profile), "profile-mismatch", "/workload/runtimes/python/libc")

    def test_the_runner_a_profile_declares_is_compared(self):
        result, profile = real_result()
        declared = lambda value: fact(value, provenance="declared")  # noqa: E731
        profile = changed(profile, lambda d: d.update(runner={"runner_environment": declared("github-hosted"), "runner_os": declared("Linux"), "runner_arch": declared("X64")}))
        self.assertValid(check(result, profile))
        for label, change, code in (
            ("self-hosted runner", lambda d: d["run"]["ci"].update(runner_environment=fact("self-hosted", "env:RUNNER_ENVIRONMENT")), "profile-mismatch"),
            ("Arm runner", lambda d: d["run"]["ci"].update(runner_arch=fact("ARM64", "env:RUNNER_ARCH")), "profile-mismatch"),
            ("runner not recorded", lambda d: d["run"]["ci"].update(runner_os=unknown()), "profile-unverified"),
        ):
            with self.subTest(label):
                findings = check(changed(result, change), profile)
                self.assertCodes(findings, code)

    def test_a_real_profile_accepts_only_github_actions_records(self):
        result, profile = real_result()
        for label, system in (("a local run", fact("local", "env:GITHUB_ACTIONS", provenance="inferred")),
                              ("a declared system", fact("github-actions", provenance="declared")),
                              ("an unknown system", unknown())):
            with self.subTest(label):
                findings = check(changed(result, lambda d: d["run"]["ci"].update(system=system)), profile)
                self.assertFinding(findings, "profile-link", "/run/ci/system")

    def test_a_real_result_is_linked_to_a_real_profile_and_its_job(self):
        result, profile = real_result()
        self.assertValid(check(result, profile))
        self.assertCodes(check(result, fixture("linux-x64-vm.profile")), "profile-link")
        # Same id and job, so only the synthetic flag differs.
        synthetic = changed(fixture("linux-x64-vm.profile"), lambda d: d.update(id=result["profile_id"], evidence_sources=profile["evidence_sources"]))
        self.assertCodes(check(result, synthetic), "profile-link")
        self.assertFinding(check(result, synthetic), "profile-link", "/synthetic")
        other_job = changed(result, lambda d: d["run"]["ci"].update(job=fact("backend", "env:GITHUB_JOB")))
        self.assertCodes(check(other_job, profile), "profile-link")
        # The job must also be in the workflow the profile names.
        other_workflow = changed(result, lambda d: d["run"]["ci"].update(workflow_ref=fact("P-R-N-D/Claus-AI/.github/workflows/other.yml@refs/pull/1/merge", "env:GITHUB_WORKFLOW_REF")))
        self.assertCodes(check(other_workflow, profile), "profile-link")
        self.assertFinding(check(other_workflow, profile), "profile-link", "/run/ci/workflow_ref")
        self.assertValid(check(changed(result, lambda d: d["run"]["ci"].update(workflow_ref=unknown())), profile))
        # Without a workflow_ref, only the job is compared.
        no_ref = changed(other_job, lambda d: d["run"]["ci"].update(workflow_ref=unknown()))
        self.assertFinding(check(no_ref, profile), "profile-link", "/run/ci/job")

    def test_a_github_actions_record_names_its_run_and_commits(self):
        result, profile = real_result()
        ci = lambda d: d["run"]["ci"]  # noqa: E731
        revision = lambda d: d["run"]["revision"]  # noqa: E731
        for label, change in (
            ("no run id", lambda d: ci(d).update(run_id=unknown())),
            ("run URL of another repository", lambda d: ci(d).update(run_url=fact("https://github.com/other/repo/actions/runs/100", "env:x", provenance="inferred"))),
            ("no GITHUB_SHA", lambda d: revision(d).update(event_sha=unknown())),
            ("no checked-out commit", lambda d: revision(d).update(tested_sha=unknown())),
            ("pull request without its head", lambda d: revision(d).update(pr_head_sha=unknown())),
            ("pull request without its base", lambda d: revision(d).update(pr_base_sha=unknown())),
            ("no run attempt", lambda d: ci(d).update(run_attempt=unknown())),
            ("run URL of another run", lambda d: ci(d).update(run_url=fact("https://github.com/P-R-N-D/Claus-AI/actions/runs/101", "env:x", provenance="inferred"))),
            ("pull_request_target without the pull request", lambda d: (ci(d).update(event=fact("pull_request_target", "env:GITHUB_EVENT_NAME")),
                                                                         revision(d).update(pr_head_sha=unknown(), pr_base_sha=unknown()))),
        ):
            with self.subTest(label):
                self.assertCodes(check(changed(result, change), profile), "ci-evidence")
        self.assertFinding(check(changed(result, lambda d: ci(d).update(job=unknown())), profile), "ci-evidence", "/run/ci/job")
        push = changed(result, lambda d: (ci(d).update(event=fact("push", "env:GITHUB_EVENT_NAME")),
                                          revision(d).update(pr_head_sha={"value": None, "provenance": "not-applicable", "reason": "push"},
                                                             pr_base_sha={"value": None, "provenance": "not-applicable", "reason": "push"})))
        self.assertValid(check(push, profile))

    def test_strategy_lane_table_parsing(self):
        self.assertEqual(LANES["Linux x86-64 glibc"], {"checked"})
        self.assertEqual(LANES["Windows on Arm, native (`win_arm64`)"], {"not-supported"})
        self.assertEqual(LANES["Windows on Arm with WSL2 (Ubuntu arm64)"], {"statically-resolvable"})
        self.assertEqual(LANES["macOS Apple Silicon"], {"statically-resolvable", "not-supported"})
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "strategy.md"
            path.write_text("# T\n\n## Platform and accelerator lanes\n\n| Lane | Wheels | Backend lock | Notes |\n|---|---|---|---|\n"
                            "| A | x | Checked: CI | n |\n| B | x | Same as A | n |\n| C | x | Partly | n |\n"
                            "| D | x | v2: statically resolvable. v1: not supported (no wheel) | n |\n", encoding="utf-8")
            self.assertEqual(validator.read_strategy_lanes(path), {"A": {"checked"}, "B": {"checked"}, "C": set(), "D": {"statically-resolvable", "not-supported"}})
            path.write_text("# T\n\n## Other\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validator.read_strategy_lanes(path)


class CommandTests(Case):
    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = validator.main([str(arg) for arg in argv])
        return code, out.getvalue() + err.getvalue()

    def test_exit_status(self):
        result, profile = FIXTURES / "linux-x64-vm.result.json", FIXTURES / "linux-x64-vm.profile.json"
        self.assertEqual(self.run_main("--dependency-strategy", STRATEGY, "--profile", profile, result, profile)[0], 0)
        self.assertEqual(self.run_main("--require-real", result)[0], 1)
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            invalid = directory / "invalid.json"
            invalid.write_text(json.dumps(changed(fixture("linux-x64-vm.result"), lambda d: d.update(overall="passed"))), encoding="utf-8")
            code, output = self.run_main(invalid)
            self.assertEqual(code, 1)
            self.assertIn("ERROR overall-mismatch /overall", output)
            self.assertEqual(self.run_main(directory / "missing.json")[0], 2)
            self.assertEqual(self.run_main("--profile", invalid, result)[0], 2)
            self.assertEqual(self.run_main("--profile", result, result)[0], 2)
            bad_schema = directory / "schema.json"
            bad_schema.write_text(json.dumps({"oneOf": []}), encoding="utf-8")
            self.assertEqual(self.run_main("--schema", bad_schema, result)[0], 2)
            no_lanes = directory / "strategy.md"
            no_lanes.write_text("# Nothing\n", encoding="utf-8")
            self.assertEqual(self.run_main("--dependency-strategy", no_lanes, result)[0], 2)

    def test_an_unwritable_summary_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output = self.run_main("--summary", pathlib.Path(directory) / "missing" / "summary.md", FIXTURES / "linux-x64-vm.profile.json")
        self.assertEqual(code, 2)
        self.assertIn("cannot write the summary", output)

    def test_document_text_cannot_forge_output_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            for label, tag in (("newline", "cpython-31\n::error title=x::forged"), ("lone surrogate", "cpython-31\ud800")):
                with self.subTest(label):
                    path = directory / "result.json"
                    path.write_text(json.dumps(changed(fixture("linux-x64-vm.result"), lambda d: d["workload"]["runtimes"]["python"].update(cache_tag=fact(tag, "synthetic:x")))), encoding="utf-8")
                    summary = directory / "summary.md"
                    code, output = self.run_main("--summary", summary, path)
                    self.assertEqual(code, 1)
                    self.assertIn("control-character", output)
                    self.assertFalse([line for line in output.splitlines() if line.lstrip().startswith("::")], output)
                    self.assertNotRegex(output + summary.read_text(encoding="utf-8"), "[\ud800-\udfff]")

    def test_document_text_cannot_start_a_legacy_workflow_command(self):
        # The runner reads "##[name]" anywhere in a line, so the value is echoed with "##[" broken up.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "result.json"
            version = "24.04 ##[stop-commands]token"
            path.write_text(json.dumps(changed(fixture("linux-x64-vm.result"), lambda d: d["environment"]["layers"][1]["os"].update(version=fact(version, "synthetic:x")))), encoding="utf-8")
            code, output = self.run_main("--profile", FIXTURES / "linux-x64-vm.profile.json", path)
            self.assertEqual(code, 1)
            self.assertIn("profile-mismatch", output)
            self.assertIn("24.04 ## [stop-commands]token", output)
            self.assertNotIn("##[", output)

    def test_secret_shaped_values_are_not_shown(self):
        # A value the schema rejects is echoed in its finding, and a file name is printed; neither may show a token.
        # The echo may escape the character before the value (repr's \n, a JSON Pointer's ~1), which a leading \b
        # in a shape would miss. The summary escapes "_", so it is checked for the token's body.
        token = "gh" + "p_" + "A" * 36
        documents = {
            "kind": changed(fixture("linux-x64-vm.result"), lambda d: d.update(kind=token)),
            "escaped": changed(fixture("linux-x64-vm.result"), lambda d: d.update(kind="\n" + token)),
            "pointer": changed(fixture("linux-x64-vm.result"), lambda d: d.update({"/" + token: 1})),
        }
        with tempfile.TemporaryDirectory() as directory:
            for case, document, name in [(case, document, "record.json") for case, document in documents.items()] + [("name", documents["kind"], token + ".json")]:
                with self.subTest(case=case):
                    path = pathlib.Path(directory) / name
                    path.write_text(json.dumps(document), encoding="utf-8")
                    summary = pathlib.Path(directory) / "summary.md"
                    summary.write_text("", encoding="utf-8")
                    code, output = self.run_main("--summary", summary, path)
                    summary = summary.read_text(encoding="utf-8")
                    self.assertEqual(code, 1)
                    self.assertIn("not shown: looks like a GitHub token", output)
                    self.assertIn("not shown: looks like a GitHub token", summary)
                    self.assertNotIn("A" * 36, output + summary)
        self.assertEqual(validator.plain("a " + token + " b ::c"), "a [not shown: looks like a GitHub token]")
        self.assertEqual(validator.plain(repr("\x07" + token)), "'\\x07[not shown: looks like a GitHub token]")

    def test_file_names_cannot_forge_output_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / "x\n::error title=forged::y.json"
            for argv in ((missing,), ("--profile", missing, FIXTURES / "linux-x64-vm.result.json"), ("--schema", missing, FIXTURES / "linux-x64-vm.result.json")):
                with self.subTest(argv=argv[0]):
                    code, output = self.run_main(*argv)
                    self.assertEqual(code, 2)
                    self.assertFalse([line for line in output.splitlines() if line.lstrip().startswith("::")], output)
            # A relative file name is printed as given, so one that starts with "::" would start the line.
            name = "::warning title=forged::x.json"
            (pathlib.Path(directory) / name).write_bytes((FIXTURES / "linux-x64-vm.result.json").read_bytes())
            with contextlib.chdir(directory):
                code, output = self.run_main(name)
            self.assertEqual(code, 0)
            self.assertIn("valid result", output)
            self.assertFalse([line for line in output.splitlines() if line.lstrip().startswith("::")], output)

    def test_an_unusable_schema_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = pathlib.Path(directory) / "schema.json"
            for bad in ([], {"type": [["object"]]}, {"$defs": {"a": {"then": {"$ref": "#/$defs/a"}}}}):
                with self.subTest(schema=bad):
                    schema.write_text(json.dumps(bad), encoding="utf-8")
                    code, output = self.run_main("--schema", schema, FIXTURES / "linux-x64-vm.result.json")
                    self.assertEqual(code, 2)
                    self.assertIn("cannot use schema", output)

    def test_summary_lists_at_most_200_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            path = directory / "many.json"
            path.write_text(json.dumps(changed(fixture("linux-x64-vm.result"), lambda d: d["extensions"].update({"x-many": {f"k{i}": "a\nb" for i in range(250)}}))), encoding="utf-8")
            summary = directory / "summary.md"
            code, _ = self.run_main("--summary", summary, path)
            self.assertEqual(code, 1)
            lines = summary.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(line.startswith("- error") for line in lines), 200)
            self.assertIn("- 51 more findings are in the job log", lines)

    def test_a_skipped_policy_is_visible(self):
        code, output = self.run_main("--no-policy", FIXTURES / "linux-x64-vm.result.json")
        self.assertEqual(code, 0)
        self.assertIn("WARNING policy-skipped /", output)

    def test_a_profile_whose_approval_is_incomplete_is_not_usable(self):
        _, profile = pair("kubernetes-arm64-cuda")
        result = FIXTURES / "kubernetes-arm64-cuda.result.json"
        with tempfile.TemporaryDirectory() as directory:
            for field in ("date", "reference"):
                with self.subTest(missing=field):
                    incomplete = changed(profile, lambda d: d["policy"]["exceptions"][0]["approval"].pop(field))
                    self.assertFinding(check(incomplete), "policy-exception", "/policy/exceptions/0/approval")
                    path = pathlib.Path(directory) / "profile.json"
                    path.write_text(json.dumps(incomplete), encoding="utf-8")
                    code, output = self.run_main("--profile", path, result)
                    self.assertEqual(code, 2)
                    self.assertIn("is not a valid profile", output)

    def test_summary_cells_escape_markdown(self):
        self.assertEqual(validator.markdown_cell("a|b`c\nd"), "a\\|b\\`c d")
        self.assertEqual(validator.markdown_cell("[x](https://example.invalid) <b>\\"), "\\[x\\]\\(https://example.invalid\\) \\<b\\>\\\\")

    def test_summary_states_what_a_valid_record_means(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            odd = directory / "a|b.json"
            odd.write_text(json.dumps(changed(fixture("linux-x64-vm.result"), lambda d: d.update(overall="passed"))), encoding="utf-8")
            summary = directory / "summary.md"
            code, _ = self.run_main("--summary", summary, FIXTURES / "linux-x64-vm.result.json", odd)
            self.assertEqual(code, 1)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("| linux-x64-vm.result.json | result | valid | 0 | 1 |", text)
            self.assertIn("| a\\|b.json | result | invalid | 1 | 1 |", text)
            self.assertIn("It is not a support claim", text)


if __name__ == "__main__":
    unittest.main()
