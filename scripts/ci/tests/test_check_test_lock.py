"""Regression tests for scripts/ci/check_test_lock.py.

CheckTestLockTests copy the two backend locks and their requirement inputs into
a temporary directory, change them (or leave them as committed), and run the
check there with `python -I` the way CI does, so they read the metadata of the
environment that runs them. InProcessTests call the check's functions on
made-up inputs. Run them in the environment installed from the test lock, from
the repository root:
    python -I -m unittest discover --start-directory scripts/ci/tests --verbose
"""

import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from packaging import markers
from packaging.requirements import Requirement

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "check_test_lock.py"
BASE_LOCK = "backend/locks/cp312-linux-x86_64.txt"
TEST_LOCK = "backend/locks/cp312-linux-x86_64-test.txt"
BASE_INPUT = "backend/requirements.txt"
TEST_INPUT = "backend/requirements-test.txt"
FILES = (BASE_LOCK, TEST_LOCK, BASE_INPUT, TEST_INPUT)
PASSED = "through the installed metadata."

spec = importlib.util.spec_from_file_location("check_test_lock", SCRIPT)
check_test_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_test_lock)


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def pin_block(name, package):
    """Return the pin line, hash lines and "# via" lines for `package` in the lock `name`."""
    return re.search(rf"^{re.escape(package)}==.*?(?=^\S|\Z)", read(name), re.MULTILINE | re.DOTALL).group(0)


def pinned(name, package):
    """Return `package==version` as the lock `name` pins it."""
    return pin_block(name, package).split(None, 1)[0]


def line(name, package):
    """Return the line of the input `name` that requires `package`, with its newline."""
    pattern = rf"^{re.escape(package)}(?![A-Za-z0-9._-])[^\n]*\n"
    return re.search(pattern, read(name), re.MULTILINE | re.IGNORECASE).group(0)


class CheckTestLockTests(unittest.TestCase):
    def run_check(self, *edits):
        """Copy the four files, apply each (file, old, new) edit, and run the check on the copies.

        An edit replaces the only occurrence of `old`, or appends `new` when `old` is None, and must
        change the file, so a test whose edit no longer applies fails instead of checking the files as
        committed.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name in FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
            for name, old, new in edits:
                path = root / name
                text = path.read_text(encoding="utf-8")
                if old is None:
                    text += new
                else:
                    self.assertEqual(text.count(old), 1, f"the edit must match exactly once in {name}: {old!r}")
                    self.assertNotEqual(old, new, f"the edit must change {name}: {old!r}")
                    text = text.replace(old, new)
                path.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), *FILES], cwd=root, capture_output=True, text=True, check=False
            )
        return result.returncode, result.stdout + result.stderr

    def assertFails(self, message, *edits):
        code, output = self.run_check(*edits)
        self.assertEqual(code, 1, output)
        self.assertIn(message, output)
        self.assertNotIn(PASSED, output)

    def assertPasses(self, *edits):
        code, output = self.run_check(*edits)
        self.assertEqual(code, 0, output)
        self.assertIn(PASSED, output)

    def with_marker(self, name, package, marker):
        """Return an edit that adds `marker` to the requirement on `package` in the input `name`."""
        old = line(name, package)
        return (name, old, f"{old.rstrip()}; {marker}\n")

    # The files as committed, and changes that keep the locks' meaning.

    def test_unchanged_files_pass(self):
        self.assertPasses()

    def test_comment_after_requirement_passes(self):
        old = line(BASE_INPUT, "Django")
        self.assertPasses((BASE_INPUT, old, f"{old.rstrip()}  # web framework\n"))

    def test_marker_that_holds_on_the_target_passes(self):
        for marker in (
            'sys_platform == "linux"',
            'python_full_version >= "3.12.15"',
            'platform_machine == "x86_64"',
        ):
            with self.subTest(marker=marker):
                self.assertPasses(self.with_marker(BASE_INPUT, "boto3", marker))

    def test_supported_marker_forms_pass(self):
        for marker in (
            "python_version >= '3.12' and (os_name == \"posix\" or sys_platform == \"win32\")",
            'python_full_version < "3.13.0" and platform_python_implementation != "PyPy"',
            'os.name == "posix"',
        ):
            with self.subTest(marker=marker):
                self.assertPasses(self.with_marker(BASE_INPUT, "boto3", marker))

    def test_marker_false_on_the_target_for_an_unpinned_package_passes(self):
        for name in (BASE_INPUT, TEST_INPUT):
            with self.subTest(input=name):
                self.assertPasses((name, None, 'pywin32>=311 ; sys_platform == "win32"\n'))

    def test_extra_names_are_normalized(self):
        self.assertPasses((BASE_INPUT, "psycopg[binary]", "psycopg[BINARY]"))

    def test_extra_the_lock_already_satisfies_passes(self):
        # pytest-django's "django" extra needs only Django, which the test lock pins at an allowed version.
        self.assertPasses((TEST_INPUT, "pytest-django>", "pytest-django[django]>"))

    # Lock format and the pins shared by the two locks.

    def test_pin_missing_from_test_lock_fails(self):
        self.assertFails(
            f"{pinned(TEST_LOCK, 'six')} is missing from {TEST_LOCK}", (TEST_LOCK, pin_block(TEST_LOCK, "six"), "")
        )

    def test_version_mismatch_between_locks_fails(self):
        version = pinned(TEST_LOCK, "six").split("==")[1]
        self.assertFails(
            f"six: {BASE_LOCK} pins {version}, {TEST_LOCK} pins 0.0.1",
            (TEST_LOCK, pinned(TEST_LOCK, "six") + " ", "six==0.0.1 "),
        )

    def test_hash_difference_between_locks_fails(self):
        digest = re.search(r"sha256:([0-9a-f]{64})", pin_block(TEST_LOCK, "six")).group(1)
        self.assertFails(
            f"{pinned(TEST_LOCK, 'six')}: hash sets differ between the two locks", (TEST_LOCK, digest, digest[::-1])
        )

    def test_duplicate_pin_fails(self):
        block = pin_block(TEST_LOCK, "six")
        self.assertFails("duplicate pin for six", (TEST_LOCK, block, block + block))

    def test_pin_without_hashes_fails(self):
        block = pin_block(TEST_LOCK, "six")
        bare = "".join(text for text in block.splitlines(keepends=True) if "--hash=" not in text)
        self.assertFails(f"{pinned(TEST_LOCK, 'six')} has no hashes", (TEST_LOCK, block, bare))

    def test_unexpected_lock_lines_fail(self):
        six = pinned(TEST_LOCK, "six")
        django = pinned(TEST_LOCK, "django")
        for old, new, message in (
            (six + " ", f"--index-url https://example.invalid/simple\n{six} ", "unexpected line: --index-url"),
            (django + " ", "django @ https://example.invalid/Django.whl ", "unexpected line: django @"),
            (six + " ", f'{six} ; sys_platform == "linux" ', f"unexpected line: {six} ;"),
        ):
            with self.subTest(line=new.splitlines()[0]):
                self.assertFails(message, (TEST_LOCK, old, new))

    def test_lock_compiled_for_another_target_fails(self):
        self.assertFails(
            "does not record --python-platform x86_64-manylinux_2_31",
            (TEST_LOCK, "--python-platform x86_64-manylinux_2_31", "--python-platform aarch64-manylinux_2_31"),
        )
        self.assertFails(
            "does not record --python-version 3.12.15",
            (BASE_LOCK, "--python-version 3.12.15", "--python-version 3.13.9"),
        )
        self.assertFails(
            "does not record --python-platform x86_64-manylinux_2_31",
            (
                TEST_LOCK,
                "--python-platform x86_64-manylinux_2_31",
                "--python-platform x86_64-manylinux_2_31 --python-platform=aarch64-manylinux_2_31",
            ),
        )

    def test_lock_header_with_equals_form_passes(self):
        # uv records `--flag=value` as given.
        self.assertPasses(
            (TEST_LOCK, "--python-version 3.12.15", "--python-version=3.12.15"),
            (BASE_LOCK, "--python-platform x86_64-manylinux_2_31", "--python-platform=x86_64-manylinux_2_31"),
        )

    # Packages only in the test lock.

    def test_package_dropped_from_backend_lock_only_fails(self):
        self.assertFails(
            f"{pinned(TEST_LOCK, 'six')} is only in {TEST_LOCK} but is pulled in by -c {BASE_LOCK}, python-dateutil",
            (BASE_LOCK, pin_block(BASE_LOCK, "six"), ""),
        )

    def test_test_only_package_without_via_fails(self):
        block = pin_block(TEST_LOCK, "pytest-django")
        bare = "".join(text for text in block.splitlines(keepends=True) if not text.startswith("    #"))
        self.assertFails(
            f"{pinned(TEST_LOCK, 'pytest-django')} has no '# via' annotation", (TEST_LOCK, block, bare)
        )

    def test_test_only_package_pulled_in_by_a_backend_package_fails(self):
        block = pin_block(TEST_LOCK, "iniconfig")
        self.assertFails(
            f"{pinned(TEST_LOCK, 'iniconfig')} is only in {TEST_LOCK} but is pulled in by django",
            (TEST_LOCK, block, block.replace("# via pytest", "# via django")),
        )

    def test_runtime_requirement_only_in_test_lock_fails(self):
        block = pin_block(TEST_LOCK, "pluggy")
        self.assertFails(
            f"{pinned(TEST_LOCK, 'pluggy')} is only in {TEST_LOCK} but is pulled in by -r backend/requirements.txt",
            (TEST_LOCK, block, block.replace("# via pytest", "# via -r backend/requirements.txt")),
        )

    # Requirement inputs: names, versions and direct requirements.

    def test_new_requirement_not_in_lock_fails(self):
        self.assertFails(f"pytest-cov>=7 is not pinned in {TEST_LOCK}", (TEST_INPUT, None, "pytest-cov>=7\n"))

    def test_specifier_excluding_the_pin_fails(self):
        for name, package in ((TEST_INPUT, "pytest"), (BASE_INPUT, "Django")):
            lock = TEST_LOCK if name == TEST_INPUT else BASE_LOCK
            with self.subTest(package=package):
                self.assertFails(
                    f"does not allow {pinned(lock, package.lower())} pinned in {lock}",
                    (name, line(name, package), f"{package}>99\n"),
                )

    def test_dropped_direct_requirement_fails(self):
        for name, lock, package in ((TEST_INPUT, TEST_LOCK, "pytest-django"), (BASE_INPUT, BASE_LOCK, "boto3")):
            with self.subTest(package=package):
                self.assertFails(
                    f"{pinned(lock, package)} is a direct requirement of {name.split('/')[1]} in {lock}",
                    (name, line(name, package), ""),
                )

    def test_requirement_added_without_regenerating_fails(self):
        # asgiref is already pinned as Django's dependency, so only the "# via" annotation shows the change.
        self.assertFails(
            f"asgiref>=3.9 is not recorded as a direct requirement of requirements.txt in {BASE_LOCK}",
            (BASE_INPUT, None, "asgiref>=3.9\n"),
        )

    def test_backend_input_change_needs_both_locks_regenerated(self):
        # Only the backend lock was regenerated after asgiref became a direct requirement.
        block = pin_block(BASE_LOCK, "asgiref")
        parents = check_test_lock.parse_lock(ROOT / BASE_LOCK)[1]["asgiref"] + ["-r backend/requirements.txt"]
        via = "".join(f"    #   {entry}\n" for entry in parents)
        regenerated = block.partition("    # via")[0] + "    # via\n" + via
        code, output = self.run_check((BASE_INPUT, None, "asgiref>=3.9\n"), (BASE_LOCK, block, regenerated))
        self.assertEqual(code, 1, output)
        message = "asgiref>=3.9 is not recorded as a direct requirement of requirements.txt in"
        self.assertIn(f"{message} {TEST_LOCK}", output)
        self.assertNotIn(f"{message} {BASE_LOCK}", output)

    def test_unsupported_input_lines_fail(self):
        for text in (
            "-e .",
            "--index-url https://example.invalid/simple",
            "-r other.txt",
            "./vendor/package",
            "pytest-9.1.1.tar.gz",
            'six-1.17.0-py2.py3-none-any.whl ; sys_platform == "win32"',
        ):
            with self.subTest(line=text):
                self.assertFails(f"unsupported requirement line: {text}", (TEST_INPUT, None, f"{text}\n"))

    def test_hash_sign_without_space_is_not_a_comment(self):
        # As in pip and uv, "#" starts a comment only at the start of a line or after whitespace.
        old = line(TEST_INPUT, "pytest")
        self.assertFails(
            f"unsupported requirement line: {old.rstrip()}#note", (TEST_INPUT, old, f"{old.rstrip()}#note\n")
        )

    def test_input_without_requirements_fails(self):
        edits = [(TEST_INPUT, line(TEST_INPUT, package), "") for package in ("pytest", "pytest-django")]
        self.assertFails("no requirements found", *edits)

    # Requirement inputs: direct URLs, extras and markers.

    def test_direct_url_requirement_fails(self):
        for name, package, url in (
            (BASE_INPUT, "Django", "https://example.invalid/Django-6.1.2-py3-none-any.whl"),
            (TEST_INPUT, "pytest", "git+https://example.invalid/pytest@9.1.1"),
            (TEST_INPUT, "pytest", "file:///tmp/pytest-9.1.1-py3-none-any.whl"),
        ):
            with self.subTest(url=url):
                self.assertFails(
                    "direct URL requirements are not supported", (name, line(name, package), f"{package} @ {url}\n")
                )

    def test_dropped_extra_fails(self):
        self.assertFails(
            f"{pinned(BASE_LOCK, 'psycopg-binary')} is pinned in {BASE_LOCK}, but no requirement in",
            (BASE_INPUT, "psycopg[binary]", "psycopg"),
        )

    def test_added_extra_fails(self):
        for name, old, new, message in (
            (BASE_INPUT, "psycopg[binary]", "psycopg[binary,pool]", "does not pin psycopg-pool"),
            (BASE_INPUT, "uvicorn>", "uvicorn[standard]>", "does not pin httptools"),
            (BASE_INPUT, "Django>", "Django[argon2]>", "does not pin argon2-cffi"),
            (TEST_INPUT, "pytest>", "pytest[dev]>", "does not pin argcomplete"),
        ):
            with self.subTest(requirement=new):
                self.assertFails(message, (name, old, new))

    def test_swapped_extra_fails(self):
        code, output = self.run_check((BASE_INPUT, "psycopg[binary]", "psycopg[c]"))
        self.assertEqual(code, 1, output)
        self.assertIn("does not pin psycopg-c", output)
        self.assertIn(f"{pinned(BASE_LOCK, 'psycopg-binary')} is pinned in", output)

    def test_unknown_extra_fails(self):
        for name, lock, old, new, package, extra in (
            (BASE_INPUT, BASE_LOCK, "psycopg[binary]", "psycopg[binay]", "psycopg", "binay"),
            (TEST_INPUT, TEST_LOCK, "pytest-django>", "pytest-django[testing]>", "pytest-django", "testing"),
        ):
            with self.subTest(requirement=new):
                self.assertFails(f"{pinned(lock, package)} does not provide the extra {extra!r}", (name, old, new))

    def test_marker_false_on_the_target_fails(self):
        for name, package, marker, message in (
            (BASE_INPUT, "boto3", 'sys_platform == "win32"', "is a direct requirement of requirements.txt"),
            (BASE_INPUT, "daphne", 'python_version < "3.12"', "is a direct requirement of requirements.txt"),
            (BASE_INPUT, "boto3", 'platform_machine == "aarch64"', f"{pinned(BASE_LOCK, 'botocore')} is pinned in"),
            (TEST_INPUT, "pytest-django", 'os_name == "nt"', "is a direct requirement of requirements-test.txt"),
        ):
            lock = TEST_LOCK if name == TEST_INPUT else BASE_LOCK
            with self.subTest(package=package, marker=marker):
                if message.startswith("is a direct"):
                    message = f"{pinned(lock, package)} {message}"
                self.assertFails(message, self.with_marker(name, package, marker))

    def test_marker_forms_outside_the_supported_ones_fail(self):
        # packaging and uv give different results for these on the target, or uv rejects the marker;
        # see InProcessTests for more forms.
        for text in (
            'requests>=2.32 ; platform_release < "6"',
            'requests>=2.32 ; python_version in "3.12.1"',
            'requests>=2.32 ; extra == ""',
            'requests>=2.32 ; sys_platform === "linux"',
        ):
            with self.subTest(line=text):
                self.assertFails(f"unsupported marker in {text}", (BASE_INPUT, None, f"{text}\n"))

    # The environment whose metadata the check reads.

    def test_installed_version_other_than_the_locks_fails(self):
        six = pinned(TEST_LOCK, "six")
        self.assertFails(
            f"six is installed at {six.split('==')[1]}, but {TEST_LOCK} pins 0.0.1",
            (BASE_LOCK, six + " ", "six==0.0.1 "),
            (TEST_LOCK, six + " ", "six==0.0.1 "),
        )

    def test_package_not_installed_fails(self):
        pin = "zz-not-installed==1.0 \\\n    --hash=sha256:" + "0" * 64 + "\n    # via -r backend/requirements.txt\n"
        self.assertFails(
            "zz-not-installed==1.0 is not installed here",
            (BASE_INPUT, None, "zz-not-installed>=1\n"),
            (BASE_LOCK, None, pin),
            (TEST_LOCK, None, pin),
        )


class FakeMetadata:
    def __init__(self, extras):
        self.extras = list(extras)

    def get_all(self, key):
        return self.extras if key == "Provides-Extra" else None


class FakeDistribution:
    def __init__(self, requires=(), extras=()):
        self.requires = list(requires)
        self.metadata = FakeMetadata(extras)


WINDOWS = dict(
    check_test_lock.TARGET,
    os_name="nt",
    platform_machine="AMD64",
    platform_release="11",
    platform_system="Windows",
    sys_platform="win32",
)


class InProcessTests(unittest.TestCase):
    """The check's functions on made-up inputs and distributions, independent of the installed environment."""

    def walk(self, distributions, lock_names, *roots):
        lock = {name: ("1.0", frozenset({"sha256:" + "0" * 64})) for name in lock_names}
        with mock.patch.object(check_test_lock.importlib.metadata, "distribution", lambda name: distributions[name]):
            return check_test_lock.walk([Requirement(root) for root in roots], lock, "lock.txt")

    def test_markers_are_evaluated_for_the_target_not_the_host(self):
        distributions = {
            "app": FakeDistribution(
                [
                    'on-windows; sys_platform == "win32"',
                    'on-linux; sys_platform == "linux"',
                    'on-pypy; implementation_name == "pypy"',
                ]
            ),
            "on-linux": FakeDistribution(),
        }
        self.assertEqual(self.walk(distributions, ["app", "on-linux"], "app"), ({"app", "on-linux"}, []))
        # The same result when the machine running the check reports itself as Windows.
        with mock.patch.object(markers, "default_environment", lambda: dict(WINDOWS)):
            self.assertEqual(self.walk(distributions, ["app", "on-linux"], "app"), ({"app", "on-linux"}, []))

    def test_extras_requested_through_other_extras_are_followed(self):
        distributions = {
            "app": FakeDistribution(
                ['app[fast]; extra == "all"', 'speedup; extra == "fast"'], extras=["all", "fast"]
            ),
            "speedup": FakeDistribution(),
        }
        self.assertEqual(self.walk(distributions, ["app", "speedup"], "app[all]"), ({"app", "speedup"}, []))

    def test_extra_requested_later_is_followed(self):
        distributions = {
            "app": FakeDistribution(["lib", 'lib[feature]; extra == "full"'], extras=["full"]),
            "lib": FakeDistribution(['helper; extra == "feature"'], extras=["feature"]),
            "helper": FakeDistribution(),
        }
        self.assertEqual(
            self.walk(distributions, ["app", "lib", "helper"], "lib", "app[full]"), ({"app", "lib", "helper"}, [])
        )

    def test_dependency_problems_are_reported(self):
        distributions = {
            "app": FakeDistribution(
                [
                    "lib>=2",
                    "other[nope]",
                    "missing",
                    "direct @ https://example.invalid/d.whl",
                    'odd; os_name in "posix"',
                ]
            ),
            "lib": FakeDistribution(),
            "other": FakeDistribution(),
        }
        _, problems = self.walk(distributions, ["app", "lib", "other"], "app")
        self.assertEqual(
            problems,
            [
                "app==1.0 requires lib>=2, which does not allow lib==1.0 pinned in lock.txt",
                "app==1.0 requires missing, but lock.txt does not pin missing; regenerate the lock",
                "app==1.0 requires the direct URL 'direct @ https://example.invalid/d.whl', "
                "which the locks cannot pin",
                "app==1.0: cannot evaluate the dependency 'odd; os_name in \"posix\"' the way uv does: "
                "a comparison must be a variable, an operator, and a quoted value, in that order",
                "other==1.0 does not provide the extra 'nope'",
            ],
        )


    def test_input_markers_are_evaluated_for_the_target_not_the_host(self):
        requirements = [
            (1, Requirement('app; sys_platform == "linux"')),
            (2, Requirement('winonly; sys_platform == "win32"')),
        ]
        lock = {"app": ("1.0", frozenset({"sha256:" + "0" * 64}))}
        parents = {"app": ["-r requirements.txt"]}
        with mock.patch.object(markers, "default_environment", lambda: dict(WINDOWS)):
            problems = check_test_lock.check_input("requirements.txt", requirements, lock, parents, "lock.txt")
        self.assertEqual(problems, [])

    def test_only_marker_forms_packaging_and_uv_evaluate_alike_are_supported(self):
        # The first group is outside the supported forms; for most of them uv 0.12.24 and packaging give
        # different results on the target, or one of them rejects the marker. Each of the second group was
        # compiled with uv 0.12.24 for the target and agrees.
        for marker in (
            'platform_release < "5"',
            'platform_version < "5"',
            'implementation_version == "3.12.15"',
            'python_version in "3.12.1"',
            'python_version not in "3.121"',
            'python_full_version in "3.12.150"',
            'sys_platform == sys_platform',
            '"linux" == sys_platform',
            'python_full_version == "3.12.15+abc"',
            'python_full_version >= "3.12.15.dev0"',
            'python_version > "3.12.*"',
            'python_version != "3.12.1.*"',
            'python_version ~= "3.12"',
            'sys_platform === "linux"',
            'sys_platform < "m"',
            'extra == ""',
            'extra < "a"',
        ):
            with self.subTest(marker=marker):
                self.assertIsNotNone(check_test_lock.unsupported_marker(Requirement(f"six; {marker}").marker))
        for marker in (
            'sys_platform == "linux"',
            'platform_machine != ""',
            "python_version >= '3.12' and (os_name == \"posix\" or sys_platform == \"win32\")",
            'python_full_version < "3.12.15.0.1"',
            'os.name == "posix"',
        ):
            with self.subTest(marker=marker):
                self.assertIsNone(check_test_lock.unsupported_marker(Requirement(f"six; {marker}").marker))
        metadata = Requirement('six; extra == "socks" and python_version < "3.10"').marker
        self.assertIsNone(check_test_lock.unsupported_marker(metadata, metadata=True))
        self.assertIsNotNone(check_test_lock.unsupported_marker(metadata))
        for marker in ('extra != "socks"', 'extra < "socks"'):
            with self.subTest(marker=marker):
                metadata = Requirement(f"six; {marker}").marker
                self.assertIsNotNone(check_test_lock.unsupported_marker(metadata, metadata=True))


if __name__ == "__main__":
    unittest.main()
