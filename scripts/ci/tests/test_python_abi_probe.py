"""Tests for scripts/ci/probe_python_abi.py.

Most tests feed the probe made-up index metadata and tag lists, so they do not
depend on the interpreter that runs them. InterpreterTests check this
interpreter's own ABI facts, and run their CPython 3.15 and 3.15t cases only on
those interpreters (python-abi.yml runs them there). The probe needs packaging,
which the workflows install from the backend lock. From the repository root:
    python -I scripts/ci/tests/test_python_abi_probe.py --verbose
"""

import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "probe_python_abi.py"
LOCK = ROOT / "backend" / "locks" / "cp312-linux-x86_64.txt"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("probe_python_abi", SCRIPT)
if probe.tags is None:
    raise SystemExit("packaging is not installed; install it from the backend lock first (see the docstring)")

SHA = {name: (name * 64)[:64] for name in "abcdef0123456789"}


def wheel(filename, sha="a", yanked=False):
    return {"filename": filename, "digests": {"sha256": SHA[sha]}, "yanked": yanked}


def release(*entries):
    return {"urls": [*entries, {"filename": "cbor2-6.1.5.tar.gz", "digests": {"sha256": SHA["f"]}}]}


# Tags as packaging lists them for CPython 3.15 on Linux x86-64, abridged and in its order of preference.
GIL_TAGS = ["cp315-cp315-manylinux_2_39_x86_64", "cp315-cp315-manylinux_2_28_x86_64", "cp315-abi3-manylinux_2_39_x86_64",
            "cp315-abi3-manylinux_2_28_x86_64", "cp311-abi3-manylinux_2_28_x86_64", "cp39-abi3-manylinux_2_28_x86_64",
            "cp315-none-manylinux_2_28_x86_64", "py3-none-manylinux_2_28_x86_64", "py3-none-any"]
FT_TAGS = ["cp315-cp315t-manylinux_2_39_x86_64", "cp315-cp315t-manylinux_2_28_x86_64", "cp315-abi3t-manylinux_2_39_x86_64",
           "cp315-abi3t-manylinux_2_28_x86_64", "cp315-none-manylinux_2_28_x86_64", "py3-none-manylinux_2_28_x86_64", "py3-none-any"]


class LockTests(unittest.TestCase):
    def test_the_backend_lock(self):
        lock = probe.parse_lock(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(len(lock), 50)
        for target in probe.TARGETS:
            self.assertIn(target, lock)
        self.assertEqual(lock["packaging"]["version"], "26.3")
        self.assertIn("cffi", lock["pycparser"]["via"])
        # The "# via" graph, followed transitively: cryptography needs cffi, which needs pycparser.
        self.assertEqual(probe.lock_dependencies(lock, "cryptography"), {"cffi", "pycparser"})
        self.assertIn("psycopg-binary", probe.lock_dependencies(lock, "psycopg"))
        self.assertEqual(probe.lock_dependencies(lock, "packaging"), set())

    def test_requirement_lines_keep_every_hash(self):
        lock = probe.parse_lock(LOCK.read_text(encoding="utf-8"))
        text = probe.requirement_text(lock, {"packaging"})
        again = probe.parse_lock(text)
        self.assertEqual(set(again), {"packaging"})
        self.assertEqual(again["packaging"]["hashes"], lock["packaging"]["hashes"])
        self.assertTrue(text.startswith("packaging==26.3 \\\n    --hash=sha256:"))

    def test_malformed_locks(self):
        for text, message in (
            ("a==1 \\\n    --hash=sha256:" + SHA["a"] + "\na==1 \\\n    --hash=sha256:" + SHA["b"] + "\n", "twice"),
            ("a==1\n    # via b\n", "no hash"),
            ("A_B==1 \\\n    --hash=sha256:" + SHA["a"] + "\n", None),
            ("a==1;rm \\\n    --hash=sha256:" + SHA["a"] + "\n", "unexpected form"),
        ):
            with self.subTest(text=text):
                if message is None:
                    self.assertIn("a-b", probe.parse_lock(text))
                else:
                    with self.assertRaisesRegex(probe.ProbeError, message):
                        probe.parse_lock(text)


class WheelFileTests(unittest.TestCase):
    def test_wheels_of_a_release(self):
        files = probe.wheel_files(release(wheel("cbor2-6.1.5-cp315-cp315t-manylinux_2_28_x86_64.whl"),
                                          wheel("cbor2-6.1.5-py3-none-any.whl", "b", yanked=True)), "cbor2", "6.1.5")
        self.assertEqual([item["filename"] for item in files], ["cbor2-6.1.5-cp315-cp315t-manylinux_2_28_x86_64.whl", "cbor2-6.1.5-py3-none-any.whl"])
        self.assertEqual(files[0]["tags"], ["cp315-cp315t-manylinux_2_28_x86_64"])
        self.assertIs(files[1]["yanked"], True)
        # A compressed tag set expands to every tag it names.
        files = probe.wheel_files(release(wheel("cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl")), "cryptography", "50.0.2")
        self.assertEqual(files[0]["tags"], ["cp315-abi3-manylinux_2_28_x86_64", "cp315-abi3t-manylinux_2_28_x86_64"])

    def test_metadata_that_does_not_match_the_release_is_refused(self):
        for document, message in (
            (release(wheel("other-6.1.5-py3-none-any.whl")), "under cbor2"),
            (release(wheel("cbor2-6.1.4-py3-none-any.whl")), "under cbor2"),
            (release({"filename": "cbor2-6.1.5-py3-none-any.whl", "digests": {"sha256": "x"}}), "unexpected name or digest"),
            (release({"filename": "cbor2-6.1.5-py3-none-any.whl;rm.whl", "digests": {"sha256": SHA["a"]}}), "unexpected name or digest"),
            (release(wheel("cbor2-6.1.5.whl")), "cannot be read"),
        ):
            with self.subTest(document=document), self.assertRaisesRegex(probe.ProbeError, message):
                probe.wheel_files(document, "cbor2", "6.1.5")

    def test_metadata_is_read_from_a_directory_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            (path / "cbor2-6.1.5.json").write_text(json.dumps(release(wheel("cbor2-6.1.5-py3-none-any.whl"))), encoding="utf-8")
            (path / "big-1.json").write_bytes(b" " * (probe.METADATA_LIMIT + 1))
            (path / "list-1.json").write_text("[]", encoding="utf-8")
            self.assertEqual(len(probe.read_metadata("cbor2", "6.1.5", path)["urls"]), 2)
            with self.assertRaisesRegex(probe.ProbeError, "larger than"):
                probe.read_metadata("big", "1", path)
            with self.assertRaisesRegex(probe.ProbeError, "lists no files"):
                probe.read_metadata("list", "1", path)

    def test_the_index_is_asked_for_the_release_only(self):
        requests = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            requests.append((request.full_url, dict(request.header_items()), timeout))
            return Response(json.dumps(release()).encode())

        with mock.patch.object(probe.urllib.request, "urlopen", urlopen):
            probe.read_metadata("cbor2", "6.1.5", None)
        self.assertEqual(requests, [("https://pypi.org/pypi/cbor2/6.1.5/json", {"Accept": "application/json", "User-agent": "claus-ci-probe-python-abi"}, probe.FETCH_TIMEOUT)])
        with mock.patch.object(probe.urllib.request, "urlopen", side_effect=OSError("down")), mock.patch.object(probe.time, "sleep") as sleep:
            with self.assertRaisesRegex(probe.ProbeError, r"cannot read the index metadata of cbor2 6\.1\.5 \(OSError\)"):
                probe.read_metadata("cbor2", "6.1.5", None)
        self.assertEqual(sleep.call_count, probe.FETCH_ATTEMPTS - 1)


class TagTests(unittest.TestCase):
    def files(self, *names):
        return probe.wheel_files(release(*[wheel(name, sha) for name, sha in names]), names[0][0].split("-")[0].lower(), names[0][0].split("-")[1])

    def test_wheels_this_interpreter_accepts_in_its_order(self):
        files = self.files(("cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", "a"),
                           ("cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_39_x86_64.whl", "b"),
                           ("cryptography-50.0.2-cp311-abi3-manylinux_2_28_x86_64.whl", "c"),
                           ("cryptography-50.0.2-cp311-abi3-win_arm64.whl", "d"))
        in_lock, not_in_lock = probe.compatible(files, GIL_TAGS, {SHA["a"], SHA["c"]})
        self.assertEqual([item["filename"] for item in in_lock], ["cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", "cryptography-50.0.2-cp311-abi3-manylinux_2_28_x86_64.whl"])
        self.assertEqual([item["filename"] for item in not_in_lock], ["cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_39_x86_64.whl"])
        # A free-threaded interpreter takes the abi3t half of the dual tag, and no abi3 wheel.
        in_lock, _ = probe.compatible(files, FT_TAGS, {SHA["a"], SHA["c"]})
        self.assertEqual([item["filename"] for item in in_lock], ["cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl"])
        # A yanked wheel is not chosen.
        files[0]["yanked"] = True
        self.assertEqual(probe.compatible(files[:1], GIL_TAGS, {SHA["a"]}), ([], []))

    def test_abi_categories(self):
        files = self.files(("cbor2-6.1.5-cp315-cp315-manylinux_2_28_x86_64.whl", "a"), ("cbor2-6.1.5-cp315-cp315t-manylinux_2_28_x86_64.whl", "b"),
                           ("cbor2-6.1.5-cp315-cp315t-win_arm64.whl", "c"), ("cbor2-6.1.5-cp39-abi3-manylinux_2_28_x86_64.whl", "d"))
        gil = probe.abi_categories(files, GIL_TAGS, "cp315")
        self.assertEqual(gil["cp315-cp315"], {"published": True, "accepted": True})
        self.assertEqual(gil["cp315-cp315t"], {"published": True, "accepted": False})
        self.assertEqual(gil["cp315-abi3"], {"published": False, "accepted": True})
        self.assertEqual(gil["cp315-abi3.abi3t"], {"published": False, "accepted": True})
        self.assertEqual(gil["older"], [{"tag": "cp39-abi3", "published": True, "accepted": True}])
        ft = probe.abi_categories(files, FT_TAGS, "cp315")
        self.assertEqual(ft["cp315-cp315t"], {"published": True, "accepted": True})
        self.assertEqual(ft["cp315-abi3"], {"published": False, "accepted": False})
        self.assertEqual(ft["cp315-abi3t"], {"published": False, "accepted": True})
        # An older abi3 wheel is published but the free-threaded build cannot load it.
        self.assertEqual(ft["older"], [{"tag": "cp39-abi3", "published": True, "accepted": False}])
        # A wheel for another platform does not count as published for this one.
        self.assertEqual(probe.abi_categories(files[2:3], GIL_TAGS, "cp315")["cp315-cp315t"]["published"], False)
        dual = self.files(("cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", "a"))
        self.assertEqual(probe.abi_categories(dual, FT_TAGS, "cp315")["cp315-abi3.abi3t"], {"published": True, "accepted": True})
        self.assertEqual(probe.abi_categories(dual, GIL_TAGS, "cp315")["cp315-abi3.abi3t"], {"published": True, "accepted": True})

    def test_the_selected_wheel_is_found_by_its_tags(self):
        files = self.files(("cryptography-50.0.2-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", "a"),
                           ("cryptography-50.0.2-cp311-abi3-manylinux_2_28_x86_64.whl", "b"),
                           ("cryptography-50.0.2-1-cp311-abi3-manylinux_2_28_x86_64.whl", "c"))
        chosen = probe.selected_wheel(["cp315-abi3t-manylinux_2_28_x86_64", "cp315-abi3-manylinux_2_28_x86_64"], files, {SHA["a"]})
        self.assertEqual(chosen["sha256"], SHA["a"])
        # Two files with the same tags (a build number) are told apart by the lock, or not at all.
        self.assertEqual(probe.selected_wheel(["cp311-abi3-manylinux_2_28_x86_64"], files, {SHA["c"]})["sha256"], SHA["c"])
        self.assertIsNone(probe.selected_wheel(["cp311-abi3-manylinux_2_28_x86_64"], files, {SHA["b"], SHA["c"]}))
        self.assertIsNone(probe.selected_wheel(["cp315-cp315t-manylinux_2_28_x86_64"], files, {SHA["a"]}))


class OutcomeTests(unittest.TestCase):
    def test_a_missing_wheel_is_not_an_install_error(self):
        self.assertEqual(probe.install_status(0, ["w.whl"], []), "installed")
        self.assertEqual(probe.install_status(1, [], []), "no-wheel")
        self.assertEqual(probe.install_status(1, [], ["late.whl"]), "not-in-lock")
        # The lock holds a wheel this interpreter accepts, and the install still failed: unexpected.
        self.assertEqual(probe.install_status(1, ["w.whl"], []), "error")
        self.assertEqual(probe.install_status(None, ["w.whl"], []), "error")
        # The install passed although no wheel was predicted: the report contradicts itself.
        self.assertEqual(probe.install_status(0, [], []), "contradiction")

    def test_the_overall_outcome(self):
        def item(install, imported="not-run", called="not-run"):
            return {"name": install, "install": {"status": install}, "probe": {"import": imported, "call": called}}

        self.assertEqual(probe.overall([item("installed", "passed", "passed")])[0::2], ("passed", "passed"))
        self.assertEqual(probe.overall([item("installed", "passed", "passed"), item("no-wheel")])[0::2], ("blocked", "passed"))
        self.assertEqual(probe.overall([item("installed", "failed"), item("no-wheel")])[0::2], ("blocked", "failed"))
        self.assertEqual(probe.overall([item("error"), item("no-wheel")])[0::2], ("failed", "not-run"))
        self.assertEqual(probe.overall([item("dependency-blocked")])[0], "blocked")
        self.assertEqual(probe.overall([item("dependency-failed")])[0], "failed")
        self.assertEqual(probe.overall([item("contradiction")])[0], "failed")

    def test_the_install_stage(self):
        wheels = {"uncovered": ["cryptography", "psycopg-binary"], "published_not_in_lock": ["psycopg-binary"]}
        named = "Because cryptography==50.0.2 has no wheels with a matching platform tag (e.g., `win_arm64`), we can conclude..."
        status, reason = probe.install_stage(1, None, named, wheels)
        self.assertEqual(status, "blocked")
        self.assertIn("no wheel this interpreter can install for cryptography, psycopg-binary", reason)
        self.assertIn("published after the lock for psycopg-binary", reason)
        self.assertEqual(probe.install_stage(1, None, named.replace("cryptography", "psycopg_binary"), wheels)[0], "blocked")
        # A failure whose output names no such package, such as a network error, is not explained by the report.
        self.assertEqual(probe.install_stage(1, None, "error: Failed to fetch: connection reset", wheels)[0], "failed")
        self.assertEqual(probe.install_stage(1, None, "Because pycryptography has no wheels", {"uncovered": ["cryptography"]})[0], "failed")
        self.assertEqual(probe.install_stage(1, None, named, {"uncovered": []})[0], "failed")
        self.assertEqual(probe.install_stage(0, 0, "", wheels)[0], "failed")
        self.assertEqual(probe.install_stage(0, 0, "", None)[0], "failed")
        self.assertEqual(probe.install_stage(0, 0, "", {"uncovered": []}), ("passed", "the lock installed binary-only with its hashes, and uv pip check passed"))
        # Windows x64: every wheel exists, and tzdata, which Django and psycopg require on win32, is not in the lock.
        missing = {"missing": [{"requirement": "tzdata", "required_by": "django", "in_lock": False},
                               {"requirement": "tzdata", "required_by": "psycopg", "in_lock": False}]}
        self.assertEqual(probe.install_stage(0, 1, "", {"uncovered": []}, missing),
                         ("blocked", "the lock does not pin requirements this platform needs: tzdata (required by django, psycopg)"))
        # A requirement the lock pins but the install lacks, or a check failure nothing explains, is a failure.
        self.assertEqual(probe.install_stage(0, 1, "", {"uncovered": []}, {"missing": [{"requirement": "idna", "required_by": "anyio", "in_lock": True}, *missing["missing"]]})[0], "failed")
        self.assertEqual(probe.install_stage(0, 1, "", {"uncovered": []}, {"missing": []})[0], "failed")
        self.assertEqual(probe.install_stage(0, 1, "", {"uncovered": []}, None)[0], "failed")

    def test_the_install_stage_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            (path / "wheels.json").write_text(json.dumps({"wheels": {"uncovered": ["cryptography"]}}), encoding="utf-8")
            (path / "log.txt").write_text("cryptography==50.0.2 has no wheels with a matching platform tag\n", encoding="utf-8")
            output = path / "output"
            argv = ["install-stage", "--sync", "1", "--check", "skip", "--log", str(path / "log.txt"), "--wheels", str(path / "wheels.json"), "--github-output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(argv), 0)
            self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["status=blocked", "reason=the lock has no wheel this interpreter can install for cryptography"])
            (path / "log.txt").write_text("boom\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(argv), 1)
            # A missing wheels report is a failed stage, not a usage error.
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main([*argv[:-4], "--wheels", str(path / "missing.json")]), 1)
            for bad in (["--sync", "x"], ["--check", "1;x"]):
                with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    probe.main([*argv, *bad])

    def test_output_lines_cannot_be_forged(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(probe.ProbeError, "spans lines"):
            probe.write_output(pathlib.Path(directory) / "o", [("reason", "x\nstatus=passed")])


class ImportProbeTests(unittest.TestCase):
    def run_target(self, code_import, code_call):
        with mock.patch.dict(probe.TARGETS, {"x-test": (code_import, code_call)}):
            return probe.import_probe("x-test")

    def test_an_import_and_a_call_in_a_new_process(self):
        out = self.run_target("import json", "assert json.loads('[1]') == [1]")
        self.assertEqual((out["import"], out["call"]), ("passed", "passed"))
        expected = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None
        if sysconfig.get_config_var("Py_GIL_DISABLED") != 1:
            self.assertEqual((out["gil_before"], out["gil_after_call"]), (expected, expected))
        self.assertIs(out["gil_reenabled"], False)

    def test_failures_are_kept_apart(self):
        out = self.run_target("import claus_no_such_module", "pass")
        self.assertEqual((out["import"], out["import_error"], out["call"]), ("failed", "ModuleNotFoundError", "not-run"))
        out = self.run_target("import json", "json.loads('{')")
        self.assertEqual((out["import"], out["call"], out["call_error"]), ("passed", "failed", "JSONDecodeError"))
        out = self.run_target("import warnings; warnings.warn('careful')", "pass")
        self.assertEqual(out["warnings"], ["UserWarning: careful"])


class InterpreterTests(unittest.TestCase):
    """This interpreter's own ABI facts, as the probe records them."""

    facts = probe.interpreter_facts()
    supported = probe.interpreter_tags()
    version = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    free_threaded = sysconfig.get_config_var("Py_GIL_DISABLED") == 1

    def test_the_facts_agree_with_each_other(self):
        facts = self.facts
        self.assertEqual(facts["version"], ".".join(str(part) for part in sys.version_info[:3]))
        self.assertEqual(facts["extension_suffixes"], list(importlib.machinery.EXTENSION_SUFFIXES))
        if facts["soabi"]:
            # cpython-315t-x86_64-linux-gnu, cpython-315t-darwin, cp315t-win_amd64: the version part ends with t on a free-threaded build.
            part = facts["soabi"].split("-")[1] if facts["soabi"].startswith("cpython-") else facts["soabi"].split("-")[0]
            self.assertEqual(part.endswith("t"), self.free_threaded)
        if hasattr(sys, "_is_gil_enabled") and not self.free_threaded:
            self.assertIs(facts["gil_enabled"], True)
        home = str(pathlib.Path.home())
        if len(home) > 5:
            self.assertNotIn(home, json.dumps(facts))

    def test_the_tags_match_the_build(self):
        own = [tag for tag in self.supported if tag.startswith(f"{self.version}-{self.version}")]
        self.assertTrue(own)
        self.assertEqual({tag.split("-")[1] for tag in own}, {f"{self.version}t" if self.free_threaded else self.version})

    @unittest.skipUnless(sys.version_info[:2] == (3, 15) and not sysconfig.get_config_var("Py_GIL_DISABLED"), "needs CPython 3.15 with the GIL")
    def test_cpython_3_15_with_the_gil(self):
        abis = {tag.split("-")[1] for tag in self.supported if tag.startswith("cp3")}
        self.assertIn("abi3", abis)
        self.assertNotIn("cp315t", abis)
        self.assertIs(sys._is_gil_enabled(), True)
        self.assertEqual(self.facts["py_gil_disabled"], 0)
        if os.name == "posix":
            self.assertIn(".abi3.so", importlib.machinery.EXTENSION_SUFFIXES)

    @unittest.skipUnless(sys.version_info[:2] == (3, 15) and sysconfig.get_config_var("Py_GIL_DISABLED") == 1, "needs CPython 3.15t")
    def test_cpython_3_15_free_threaded(self):
        abis = {tag.split("-")[1] for tag in self.supported if tag.startswith("cp3")}
        # The free-threaded build takes cp315t and abi3t wheels, and no abi3 wheel of any version.
        self.assertIn("cp315t", abis)
        self.assertIn("abi3t", abis)
        self.assertNotIn("abi3", abis)
        self.assertNotIn("cp315", abis)
        self.assertIs(sys._is_gil_enabled(), False)
        self.assertEqual(self.facts["py_gil_disabled"], 1)
        if os.name == "posix":
            self.assertNotIn(".abi3.so", importlib.machinery.EXTENSION_SUFFIXES)
            self.assertIn(".abi3t.so", importlib.machinery.EXTENSION_SUFFIXES)


class SourceTests(unittest.TestCase):
    """The probe's own code: no shell, no other network host, and writes only where it is told to."""

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def calls(self, module):
        return [node for node in ast.walk(self.tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == module]

    def test_subprocesses_without_a_shell_or_input(self):
        runs = self.calls("subprocess")
        self.assertEqual([call.func.attr for call in runs], ["run", "run"])
        for call in runs:
            keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
            self.assertNotIn("shell", keywords)
            self.assertEqual(keywords["stdin"], "subprocess.DEVNULL")
            self.assertIn("timeout", keywords)
        # uv installs binary only, from the lock's hashes, and reads no uv.toml or pyproject.toml.
        uv = next(node for node in ast.walk(self.tree) if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "command" and "pip" in ast.unparse(node.value))
        text = ast.unparse(uv.value)
        for flag in ("'--no-config'", "'--require-hashes'", "'--only-binary'", "':all:'", "'--no-deps'"):
            self.assertIn(flag, text)

    def test_the_only_network_use_is_the_index_json_api(self):
        urls = [node for node in ast.walk(self.tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(("http://", "https://"))]
        self.assertEqual(sorted(node.value for node in urls), ["https://pypi.org/pypi/{name}/{version}/json", "https://pypi.org/simple"])
        self.assertEqual([ast.unparse(call.func) for call in ast.walk(self.tree) if isinstance(call, ast.Call) and "urlopen" in ast.unparse(call.func)],
                         ["urllib.request.urlopen"])
        imported = {alias.name.split(".")[0] for node in ast.walk(self.tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imported & {"socket", "http", "requests", "ssl", "ctypes"}, set())

    def test_no_environment_is_read_or_changed(self):
        names = {ast.unparse(node) for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertEqual({name for name in names if name.startswith("os.environ") or name in ("os.getenv", "os.putenv", "os.system", "os.popen")}, set())


if __name__ == "__main__":
    unittest.main()
