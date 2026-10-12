"""Probe how this Python interpreter takes the backend lock's wheels (docs/CI-MATRIX.md, docs/DEPENDENCY-STRATEGY.md).

Run it with the interpreter under test, from a virtual environment that holds
packaging from the lock (it reads wheel tags with packaging.tags, as pip does):

    python -I scripts/ci/probe_python_abi.py wheels --lock LOCK --output FILE
    python -I scripts/ci/probe_python_abi.py requirements --lock LOCK --output FILE
    python -I scripts/ci/probe_python_abi.py packages --lock LOCK --uv UV --output FILE
    python -I scripts/ci/probe_python_abi.py install-stage --sync STATUS --check STATUS --log FILE --wheels FILE [--requirements FILE]
    python -I scripts/ci/probe_python_abi.py pin --lock LOCK --name NAME --output FILE

wheels: for every package the lock pins, the published wheels of that version this
  interpreter can install, and whether the lock holds the hash of one of them. A
  package with none is "uncovered": the lock cannot be installed binary-only here.
requirements: the requirements of the installed distributions that are not
  installed, or whose installed version they exclude, as uv pip check reports
  them, and whether the lock pins each one.
packages: for each package in TARGETS, the wheel tags published for this
  interpreter's platform, which of them it accepts, then an install with uv from
  the lock's hashes, binary only, the selected wheel's file name and SHA-256, the
  extension modules it installed, and, in a new process, the GIL state before and
  after the import, the import, and one call. It does not test thread safety.
install-stage: the status of a lane's install stage, from the exit statuses of
  uv pip sync and uv pip check, the installer's log, and the wheels and
  requirements reports: blocked when the lock lacks a wheel the installer names
  or a requirement this platform needs and uv pip check reports nothing else,
  failed for any other failure or when the install contradicts a report, passed
  when both commands passed. It exits 1 when the stage failed.
pin: the lock's lines, with their hashes, for the packages named, so that one
  of them (packaging) can be installed alone, binary only and hash-checked.

It reads the lock, the metadata of installed distributions, and PyPI's JSON API
for each pinned version (https://pypi.org/pypi/NAME/VERSION/json, or the files
--metadata-dir names); that is its only network use, and it sends nothing but
the package name and version. packages also runs uv, which downloads from PyPI
and installs into this interpreter's environment. It writes the output file,
the optional summary and GitHub output lines, and, for packages, a temporary
requirements file. It records no host name, account name, home directory or
environment variable, and never escalates privileges.

Exit status: 0 when nothing unexpected happened (a missing wheel is expected,
and is reported); 1 when an install, import or call failed unexpectedly, or a
report contradicts itself; 2 on a usage error or when the output cannot be
written or the metadata cannot be read.
"""

import argparse
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.request

try:
    from packaging import markers, requirements, specifiers, tags, utils, version as versions
except ImportError:  # Reported by main(); the functions below that need it are not reached.
    markers = requirements = specifiers = tags = utils = versions = None

SCHEMA = "claus-python-abi/1"
PYPI = "https://pypi.org/pypi/{name}/{version}/json"
METADATA_LIMIT = 16 * 1024 * 1024
FETCH_TIMEOUT = 30
FETCH_ATTEMPTS = 3
UV_TIMEOUT = 600
PROBE_TIMEOUT = 120
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
VERSION = re.compile(r"[0-9A-Za-z.+!_-]{1,64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
WHEEL_FILE = re.compile(r"[A-Za-z0-9._+-]{1,200}\.whl")
LOCK_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
LOCK_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
# uv pip check prints "Found N incompatibilities", then one line per problem that starts "The package `NAME`".
CHECK_COUNT = re.compile(r"Found ([0-9]+) incompatibilit(?:y|ies)")
CHECK_NOT_INSTALLED = re.compile(r"The package `[^`]+` requires `([A-Za-z0-9][A-Za-z0-9._-]*)[^`]*`, but it's not installed")

# The packages in the backend lock that build CPython extension modules, with code that imports each one and code that
# calls it. Other lock packages they need are installed from the lock too; COMPANIONS are lock packages that import a
# target rather than being imported by it (psycopg loads psycopg-binary).
TARGETS = {
    "autobahn": ("import autobahn.websocket.utf8validator as module", "assert module.Utf8Validator().validate(b'abc')[0]"),
    "cbor2": ("import cbor2; from cbor2 import _cbor2", "assert cbor2.loads(cbor2.dumps({'a': [1, 2]})) == {'a': [1, 2]}"),
    "cffi": ("import cffi, _cffi_backend", "assert cffi.FFI().new('int *', 7)[0] == 7"),
    "cryptography": ("from cryptography.hazmat.primitives import hashes", "digest = hashes.Hash(hashes.SHA256()); digest.update(b'abc'); assert digest.finalize().hex().startswith('ba7816bf')"),
    "greenlet": ("import greenlet", "assert greenlet.greenlet(lambda: 42).switch() == 42"),
    "msgpack": ("import msgpack; from msgpack import _cmsgpack", "assert msgpack.unpackb(msgpack.packb({'a': 1})) == {'a': 1}"),
    "psycopg-binary": ("import os; os.environ['PSYCOPG_IMPL'] = 'binary'; import psycopg", "assert psycopg.pq.__impl__ == 'binary' and psycopg.pq.version() > 0"),
    "pydantic-core": ("from pydantic_core import SchemaValidator, core_schema", "assert SchemaValidator(core_schema.int_schema()).validate_python('1') == 1"),
    "ujson": ("import ujson", "assert ujson.loads(ujson.dumps([1, 'a'])) == [1, 'a']"),
    "zope-interface": ("import zope.interface._zope_interface_coptimizations; from zope.interface import Interface, implementer", "I = type('I', (Interface,), {}); C = implementer(I)(type('C', (), {})); assert I.providedBy(C())"),
}
COMPANIONS = {"psycopg-binary": ("psycopg",)}

# Runs in a new process, so that each import starts from the GIL state the interpreter starts with.
IMPORT_PROBE = r"""
import json, sys, warnings
names = json.loads(sys.argv[1])
gil = (lambda: sys._is_gil_enabled()) if hasattr(sys, "_is_gil_enabled") else (lambda: None)
out = {"gil_before": gil(), "import": "not-run", "call": "not-run"}
scope = {}
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    try:
        exec(compile(names["import"], "<import>", "exec"), scope)
        out["import"] = "passed"
    except BaseException as exc:
        out["import"], out["import_error"] = "failed", type(exc).__name__
    out["gil_after_import"] = gil()
    if out["import"] == "passed":
        try:
            exec(compile(names["call"], "<call>", "exec"), scope)
            out["call"] = "passed"
        except BaseException as exc:
            out["call"], out["call_error"] = "failed", type(exc).__name__
    out["gil_after_call"] = gil()
out["warnings"] = [f"{item.category.__name__}: {item.message}" for item in caught][:10]
print(json.dumps(out))
"""


class ProbeError(Exception):
    pass


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# The lock and the index


def parse_lock(text):
    """{name: {"version", "hashes", "lines", "via"}} from a uv pip compile lock with hashes."""
    packages, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        match = LOCK_PIN.match(raw)
        if match:
            name = canonical(match.group(1))
            if name in packages:
                raise ProbeError(f"the lock pins {name} twice")
            current = packages[name] = {"version": match.group(2), "hashes": set(), "lines": [raw.rstrip(" \\")], "via": set()}
            continue
        if current is None or not line:
            continue
        hashes = LOCK_HASH.findall(line)
        if hashes:
            current["hashes"].update(hashes)
            current["lines"].extend(f"    --hash=sha256:{value}" for value in hashes)
        elif line.startswith("#"):
            via = line.lstrip("#").strip()
            via = via[len("via"):].strip() if via.startswith("via") else via
            if via and not via.startswith("-r"):
                current["via"].add(canonical(via))
    for name, package in packages.items():
        if not NAME.fullmatch(name) or not VERSION.fullmatch(package["version"]) or not package["hashes"]:
            raise ProbeError(f"the lock entry for {name} has no hash or an unexpected form")
    return packages


def requirement_text(lock, names):
    """Requirement lines, with hashes, for some of the lock's packages."""
    lines = []
    for name in sorted(names):
        entry = lock[name]
        pin = entry["lines"][0]
        lines.append(" \\\n".join([pin, *entry["lines"][1:]]))
    return "\n".join(lines) + "\n"


def lock_dependencies(lock, name):
    """The lock packages that `name` pulls in, by the lock's "# via" comments, transitively."""
    found, queue = set(), [name]
    while queue:
        current = queue.pop()
        for other, entry in lock.items():
            if current in entry["via"] and other not in found and other != name:
                found.add(other)
                queue.append(other)
    return found


_METADATA = {}


def fetch_metadata(name, version, directory=None):
    """PyPI's JSON for one release, from the API or from DIRECTORY/NAME-VERSION.json, read once per run."""
    key = (name, version, directory)
    if key not in _METADATA:
        _METADATA[key] = read_metadata(name, version, directory)
    return _METADATA[key]


def read_metadata(name, version, directory):
    if directory is not None:
        path = pathlib.Path(directory) / f"{name}-{version}.json"
        data = path.read_bytes()[:METADATA_LIMIT + 1]
    else:
        url = PYPI.format(name=name, version=version)
        for attempt in range(FETCH_ATTEMPTS):
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "claus-ci-probe-python-abi"})
                with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
                    data = response.read(METADATA_LIMIT + 1)
                break
            except OSError as exc:
                if attempt == FETCH_ATTEMPTS - 1:
                    raise ProbeError(f"cannot read the index metadata of {name} {version} ({type(exc).__name__})") from None
                time.sleep(2 ** (attempt + 1))
    if len(data) > METADATA_LIMIT:
        raise ProbeError(f"the index metadata of {name} {version} is larger than {METADATA_LIMIT} bytes")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProbeError(f"the index metadata of {name} {version} is not JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("urls"), list):
        raise ProbeError(f"the index metadata of {name} {version} lists no files")
    return document


def wheel_files(document, name, version):
    """The wheels of one release, each with its file name, SHA-256 and the tags its name expands to."""
    files = []
    for entry in document["urls"]:
        filename = entry.get("filename") if isinstance(entry, dict) else None
        if not isinstance(filename, str) or not filename.endswith(".whl"):
            continue
        sha256 = (entry.get("digests") or {}).get("sha256")
        if not WHEEL_FILE.fullmatch(filename) or not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
            raise ProbeError(f"the index lists a wheel of {name} with an unexpected name or digest")
        try:
            parsed_name, parsed_version, _, wheel_tags = utils.parse_wheel_filename(filename)
        except (utils.InvalidWheelFilename, versions.InvalidVersion):
            raise ProbeError(f"the index lists a wheel of {name} whose name cannot be read") from None
        if canonical(str(parsed_name)) != name or parsed_version != versions.Version(version):
            raise ProbeError(f"the index lists {filename} under {name} {version}")
        requires_python = entry.get("requires_python")
        files.append({"filename": filename, "sha256": sha256, "tags": sorted(str(tag) for tag in wheel_tags), "yanked": bool(entry.get("yanked")),
                      "requires_python": requires_python if isinstance(requires_python, str) else None})
    return files


# ---------------------------------------------------------------------------
# Tags


def interpreter_tags():
    """This interpreter's supported tags, most preferred first, as strings."""
    return [str(tag) for tag in tags.sys_tags()]


def admits(requires_python, python_version):
    """Whether a wheel's Requires-Python admits this Python version. An unreadable one excludes nothing, as pip treats it."""
    if not requires_python:
        return True
    try:
        return specifiers.SpecifierSet(requires_python).contains(python_version, prereleases=True)
    except specifiers.InvalidSpecifier:
        return True


def compatible(files, supported, lock_hashes, python_version=None):
    """Split a release's wheels into those this interpreter accepts whose hash the lock holds, and those it does not hold.

    A wheel is accepted when one of its tags is supported, it is not yanked, and its Requires-Python admits this
    version (its release, without a pre-release part, as pip compares it), as installers check. Each list is ordered
    by the interpreter's preference, as an installer ranks them.
    """
    python_version = python_version or ".".join(str(part) for part in sys.version_info[:3])
    rank = {tag: index for index, tag in enumerate(supported)}
    usable = [(min(rank[tag] for tag in item["tags"] if tag in rank), item) for item in files
              if not item["yanked"] and admits(item.get("requires_python"), python_version) and any(tag in rank for tag in item["tags"])]
    usable.sort(key=lambda pair: (pair[0], pair[1]["filename"]))
    in_lock = [item for _, item in usable if item["sha256"] in lock_hashes]
    not_in_lock = [item for _, item in usable if item["sha256"] not in lock_hashes]
    return in_lock, not_in_lock


def abi_categories(files, supported, version=None):
    """For this interpreter's version: whether a wheel is published for its platforms with each ABI tag, and whether it accepts that tag.

    The categories are cpXY-cpXY, cpXY-cpXYt, cpXY-abi3, cpXY-abi3t and cpXY-abi3.abi3t, by the Python and ABI parts
    of the wheel's file name; an older stable-ABI wheel, such as cp39-abi3, is listed under "older".
    """
    version = version or f"cp{sys.version_info[0]}{sys.version_info[1]}"
    platforms = {tag.rsplit("-", 1)[1] for tag in supported}
    accepted_abis = {tuple(tag.split("-")[:2]) for tag in supported}
    names = {f"{version}-{version}": (version, version), f"{version}-{version}t": (version, f"{version}t"),
             f"{version}-abi3": (version, "abi3"), f"{version}-abi3t": (version, "abi3t"), f"{version}-abi3.abi3t": (version, "abi3.abi3t")}
    out = {}
    for key, (python, abi) in names.items():
        published = any(item["filename"][:-4].split("-")[-3:-1] == [python, abi] and any(tag.rsplit("-", 1)[1] in platforms for tag in item["tags"]) for item in files)
        accepted = any((python, part) in accepted_abis for part in abi.split("."))
        out[key] = {"published": published, "accepted": accepted}
    # Stable-ABI wheels built for an older version, such as cp39-abi3, which a newer interpreter may also accept.
    older = sorted({tuple(tag.split("-")[:2]) for item in files for tag in item["tags"]
                    if tag.rsplit("-", 1)[1] in platforms and tag.split("-")[1] in ("abi3", "abi3t") and tag.split("-")[0] != version})
    out["older"] = [{"tag": f"{python}-{abi}", "published": True, "accepted": (python, abi) in accepted_abis} for python, abi in older]
    return out


def selected_wheel(installed_tags, files, lock_hashes):
    """The release's wheel whose name expands to exactly the tags in the installed WHEEL file, or None.

    Two files with the same tags (a build number) are told apart by the lock; when that is not enough, none is named.
    """
    matches = [item for item in files if set(item["tags"]) == set(installed_tags)]
    if len(matches) > 1:
        matches = [item for item in matches if item["sha256"] in lock_hashes]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Outcomes


def install_status(exit_status, in_lock, not_in_lock):
    """installed, no-wheel, not-in-lock, error (an unexpected failure) or contradiction."""
    if exit_status == 0:
        return "installed" if in_lock else "contradiction"
    if in_lock:
        return "error"
    return "not-in-lock" if not_in_lock else "no-wheel"


INSTALL_BLOCKED = ("no-wheel", "not-in-lock", "dependency-blocked")
INSTALL_FAILED = ("error", "contradiction", "dependency-failed")


def overall(packages):
    """(install status, reason, execute status) for the packages report.

    The import and call fail when either raised, or when a package enabled the GIL that a free-threaded build
    started without (docs/DEPENDENCY-STRATEGY.md: GIL re-enablement is a failed qualification).
    """
    blocked = [item["name"] for item in packages if item["install"]["status"] in INSTALL_BLOCKED]
    failed = [item["name"] for item in packages if item["install"]["status"] in INSTALL_FAILED]
    broken = [item["name"] for item in packages
              if item["probe"]["import"] == "failed" or item["probe"]["call"] == "failed" or item["probe"].get("gil_reenabled") is True]
    ran = [item["name"] for item in packages if item["probe"]["call"] == "passed"]
    if failed:
        install = ("failed", f"unexpected install failures: {', '.join(failed)}")
    elif blocked:
        install = ("blocked", f"no installable wheel or dependency in the lock for this interpreter: {', '.join(blocked)}")
    else:
        install = ("passed", "every package installed")
    execute = "failed" if broken else "passed" if ran else "not-run"
    return install[0], install[1], execute


def names_a_missing_wheel(log_text, names):
    """Whether the installer's output names one of these packages on a line that says it has no wheel, as uv does when it finds none.

    uv 0.12 prints "Because NAME==VERSION has no usable wheels" or "has no wheels with a matching platform tag", and
    "Wheels are required for `NAME`".
    """
    lines = [line.lower() for line in log_text.splitlines() if re.search(r"\bno (?:usable |compatible )?wheels?\b|\bwheels are required for\b", line.lower())]
    return any(re.search(rf"(?<![a-z0-9_.-]){re.escape(spelling)}(?![a-z0-9_-])", line)
               for name in names for spelling in {name, name.replace("-", "_")} for line in lines)


def install_stage(sync, check, log_text, wheels, requirements_report=None):
    """(status, reason) of a lane's install stage.

    `sync` is the exit status of uv pip sync; `check`, that of uv pip check, or None when it did not run.
    A failure the reports explain, a wheel or a platform requirement the lock lacks, is blocked: the
    lock is the prerequisite that is missing. Any other failure, and a report the install contradicts, is failed.
    """
    if wheels is None:
        return "failed", "the wheels report is missing, so the install cannot be explained"
    uncovered = wheels.get("uncovered", [])
    if sync != 0:
        if not uncovered:
            return "failed", "the installer failed although the lock has a wheel this interpreter can install for every package; see the install log"
        if not names_a_missing_wheel(log_text, uncovered):
            return "failed", f"the installer failed, but its output names no package the wheels report lacks ({', '.join(uncovered)}); see the install log"
        reason = f"the lock has no wheel this interpreter can install for {', '.join(uncovered)}"
        late = [name for name in uncovered if name in wheels.get("published_not_in_lock", [])]
        if late:
            reason += f"; a compatible wheel was published after the lock for {', '.join(late)}"
        return "blocked", reason
    if uncovered:
        return "failed", f"the install passed although the wheels report finds no wheel in the lock for {', '.join(uncovered)}"
    if check == 0:
        return "passed", "the lock installed binary-only with its hashes, and uv pip check passed"
    if requirements_report is None:
        return "failed", "uv pip check failed and the requirements report is missing; see the install log"
    missing = requirements_report.get("missing", [])
    # Only a requirement that is not installed and that the lock does not pin is a prerequisite the lock lacks;
    # one the lock pins, or an installed version the requirement excludes, is a failure.
    outside = sorted({item["requirement"] for item in missing if not item["in_lock"] and item.get("installed") is None})
    if not outside or any(item["in_lock"] or item.get("installed") is not None for item in missing):
        return "failed", "uv pip check failed for a reason the requirements report does not explain; see the install log"
    unexplained = unexplained_check_problems(log_text, set(outside))
    if unexplained:
        return "failed", f"uv pip check reported {unexplained}; see the install log"
    needed = "; ".join(f"{name} (required by {', '.join(sorted({item['required_by'] for item in missing if item['requirement'] == name}))})" for name in outside)
    return "blocked", f"the lock does not pin requirements this platform needs: {needed}"


def unexplained_check_problems(log_text, outside):
    """Which problems uv pip check printed are not one of these requirements missing, as a phrase, or None when none are.

    Each problem line must say that a requirement named in `outside` is not installed, and the count uv prints must
    match the lines, so that a version conflict, a broken or duplicate distribution, or output this does not
    recognise is a failure rather than a missing prerequisite.
    """
    lines = [line.strip() for line in log_text.splitlines()]
    problems = [line for line in lines if line.startswith("The package `")]
    counts = [int(match.group(1)) for line in lines if (match := CHECK_COUNT.fullmatch(line))]
    if counts != [len(problems)] or not problems:
        return "a problem count that does not match its problem lines"
    others = [line for line in problems if not (match := CHECK_NOT_INSTALLED.fullmatch(line)) or canonical(match.group(1)) not in outside]
    return f"{len(others)} of {len(problems)} problems that are not a requirement missing from the lock" if others else None


# ---------------------------------------------------------------------------
# This interpreter and its environment


def scrub(text):
    """Text without this environment's paths or control characters, so that a report holds no home directory and stays on one line."""
    for path in sorted({sys.prefix, sys.base_prefix, str(pathlib.Path.home())}, key=len, reverse=True):
        if path and len(path) > 1:
            text = text.replace(path, "<path>")
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:300]


def interpreter_facts():
    config = sysconfig.get_config_var
    try:
        libc = os.confstr("CS_GNU_LIBC_VERSION")
    except (AttributeError, ValueError, OSError):
        libc = None
    facts = {
        "python_vv": scrub(f"Python {sys.version}".replace("\n", " ")),
        "version": platform.python_version(),
        "implementation": {"name": sys.implementation.name, "cache_tag": sys.implementation.cache_tag, "multiarch": getattr(sys.implementation, "_multiarch", None)},
        "soabi": config("SOABI"),
        "py_gil_disabled": config("Py_GIL_DISABLED"),
        "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None,
        "extension_suffixes": list(importlib.machinery.EXTENSION_SUFFIXES),
        "platform": sysconfig.get_platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "pointer_bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "libc": libc,
        "macos": platform.mac_ver()[0] or None,
        "windows": platform.version() if platform.system() == "Windows" else None,
    }
    return facts


def installed_distribution(name):
    importlib.invalidate_caches()
    try:
        return importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def installed_tags(distribution):
    text = distribution.read_text("WHEEL") or ""
    return sorted({line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("Tag:")})


def extension_suffixes(distribution):
    """The suffixes of the extension modules a distribution installed, such as .abi3t.so or .cp315-win_amd64.pyd."""
    known = sorted(importlib.machinery.EXTENSION_SUFFIXES, key=len, reverse=True)
    found = set()
    for item in distribution.files or []:
        base = pathlib.PurePosixPath(str(item)).name
        if not base.endswith((".so", ".pyd", ".dylib")) or ".dist-info" in str(item):
            continue
        suffix = next((suffix for suffix in known if base.endswith(suffix)), None)
        found.add(suffix if suffix else base[base.index("."):] if "." in base else base)
    return sorted(found)


def unmet_requirements(lock):
    """Requirements of installed distributions that are not met, with the distributions that need them.

    "installed" is None when the requirement is not installed, and the installed version when the requirement excludes it.
    """
    environment = markers.default_environment()
    installed = {canonical(item.metadata["Name"]): item.version for item in importlib.metadata.distributions() if item.metadata["Name"]}
    missing = []
    for distribution in importlib.metadata.distributions():
        name = canonical(distribution.metadata["Name"] or "")
        for text in distribution.requires or []:
            requirement = requirements.Requirement(text)
            if requirement.marker is not None and not requirement.marker.evaluate({**environment, "extra": ""}):
                continue
            needed = canonical(requirement.name)
            if needed not in installed:
                missing.append({"requirement": needed, "required_by": name, "in_lock": needed in lock, "installed": None})
                continue
            try:
                met = requirement.specifier.contains(installed[needed], prereleases=True)
            except versions.InvalidVersion:
                met = False
            if not met:
                missing.append({"requirement": needed, "required_by": name, "in_lock": needed in lock, "installed": installed[needed]})
    return sorted(missing, key=lambda item: (item["requirement"], item["required_by"]))


# ---------------------------------------------------------------------------
# Commands


def wheels_report(lock, metadata_dir=None):
    supported = interpreter_tags()
    packages, uncovered, late = [], [], []
    for name in sorted(lock):
        entry = lock[name]
        files = wheel_files(fetch_metadata(name, entry["version"], metadata_dir), name, entry["version"])
        in_lock, not_in_lock = compatible(files, supported, entry["hashes"])
        packages.append({
            "name": name, "version": entry["version"],
            "compatible_in_lock": [item["filename"] for item in in_lock],
            "compatible_not_in_lock": [item["filename"] for item in not_in_lock],
        })
        if not in_lock:
            uncovered.append(name)
            if not_in_lock:
                late.append(name)
    return {"packages": packages, "uncovered": uncovered, "published_not_in_lock": late}


def run_uv(uv, lock, names):
    """Install some lock packages with uv into this environment, binary only and hash-checked; return the exit status."""
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "requirements.txt"
        path.write_text(requirement_text(lock, names), encoding="utf-8")
        command = [uv, "pip", "install", "--no-config", "--python", sys.executable, "--default-index", "https://pypi.org/simple",
                   "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(path)]
        print(f"$ uv pip install --require-hashes --only-binary :all: --no-deps {' '.join(sorted(names))}", flush=True)
        try:
            process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=UV_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  uv could not be run ({type(exc).__name__})", flush=True)
            return None
        print(process.stdout.decode("utf-8", "replace")[-4000:], flush=True)
        return process.returncode


def import_probe(target):
    code_import, code_call = TARGETS[target]
    command = [sys.executable, "-I", "-c", IMPORT_PROBE, json.dumps({"import": code_import, "call": code_call})]
    try:
        process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"import": "failed", "call": "not-run", "import_error": type(exc).__name__, "warnings": []}
    try:
        out = json.loads(process.stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, ValueError, IndexError):
        return {"import": "failed", "call": "not-run", "import_error": f"exit status {process.returncode}", "warnings": []}
    out["warnings"] = [scrub(text) for text in out.get("warnings", [])]
    before, after = out.get("gil_before"), out.get("gil_after_call")
    out["gil_reenabled"] = before is False and after is True
    return out


def probe_package(target, lock, uv, supported, metadata_dir=None):
    entry = lock[target]
    files = wheel_files(fetch_metadata(target, entry["version"], metadata_dir), target, entry["version"])
    in_lock, not_in_lock = compatible(files, supported, entry["hashes"])
    item = {
        "name": target, "version": entry["version"],
        "categories": abi_categories(files, supported),
        "compatible_in_lock": [wheel["filename"] for wheel in in_lock],
        "compatible_not_in_lock": [wheel["filename"] for wheel in not_in_lock],
        "thread_safety": {"status": "not-tested", "reason": "this probe imports and calls each package once, from one thread"},
        "probe": {"import": "not-run", "call": "not-run"},
    }
    status = install_status(run_uv(uv, lock, [target]), [wheel["filename"] for wheel in in_lock], [wheel["filename"] for wheel in not_in_lock])
    item["install"] = {"status": status}
    if status != "installed":
        return item
    distribution = installed_distribution(target)
    wheel_tags = installed_tags(distribution) if distribution else []
    chosen = selected_wheel(wheel_tags, files, entry["hashes"])
    item["selected"] = {
        "filename": chosen["filename"] if chosen else None,
        "sha256": chosen["sha256"] if chosen else None,
        "in_lock": bool(chosen and chosen["sha256"] in entry["hashes"]),
        "tags": wheel_tags,
        "preferred": bool(chosen and in_lock and chosen["filename"] == in_lock[0]["filename"]),
    }
    item["extension_suffixes"] = extension_suffixes(distribution) if distribution else []
    if chosen is None or not item["selected"]["in_lock"]:
        item["install"]["status"] = "contradiction"
        return item
    # The lock packages the target needs, and those that load it, installed the same way.
    others = set(COMPANIONS.get(target, ()))
    for name in list(others):
        others |= lock_dependencies(lock, name)
    others |= lock_dependencies(lock, target)
    others = {name for name in others if installed_distribution(name) is None}
    if others:
        dependency_status = run_uv(uv, lock, others)
        if dependency_status != 0:
            # Find out whether a dependency lacks a wheel here, or failed for another reason.
            reports = {name: compatible(wheel_files(fetch_metadata(name, lock[name]["version"], metadata_dir), name, lock[name]["version"]), supported, lock[name]["hashes"]) for name in others}
            lacking = sorted(name for name, (found, _) in reports.items() if not found)
            item["install"] = {"status": "dependency-blocked" if lacking else "dependency-failed", "dependencies": lacking or sorted(others)}
            return item
    unmet = [entry for entry in unmet_requirements(lock) if entry["required_by"] in ({target} | others | set(COMPANIONS.get(target, ())))]
    if unmet:
        blocked = all(not entry["in_lock"] and entry.get("installed") is None for entry in unmet)
        item["install"] = {"status": "dependency-blocked" if blocked else "dependency-failed", "dependencies": sorted({entry["requirement"] for entry in unmet})}
        return item
    item["probe"] = import_probe(target)
    return item


def packages_report(lock, uv, metadata_dir=None, targets=None):
    supported = interpreter_tags()
    names = targets or sorted(TARGETS)
    for name in names:
        if name not in TARGETS or name not in lock:
            raise ProbeError(f"{name} is not a target the lock pins")
    packages = [probe_package(name, lock, uv, supported, metadata_dir) for name in names]
    install, reason, execute = overall(packages)
    return {"packages": packages, "outcome": {"install": install, "install_reason": reason, "execute": execute}}


def summary_lines(report):
    lines = [f"### Python ABI probe: {report['interpreter']['python_vv'][:100]}", ""]
    facts = report["interpreter"]
    lines += [f"SOABI `{facts['soabi']}`, Py_GIL_DISABLED `{facts['py_gil_disabled']}`, GIL enabled `{facts['gil_enabled']}`, platform `{facts['platform']}`, extension suffixes `{' '.join(facts['extension_suffixes'])}`", ""]
    if "wheels" in report:
        uncovered = report["wheels"]["uncovered"]
        lines += [f"Lock packages without a wheel this interpreter can install: {', '.join(uncovered) or 'none'}", ""]
    if "requirements" in report:
        missing = report["requirements"]["missing"]
        lines += [f"Unmet requirements of installed packages: {', '.join(sorted({item['requirement'] for item in missing})) or 'none'}", ""]
    if "packages" in report:
        lines += ["| Package | Install | Selected wheel | Extension suffixes | GIL before / after | Import | Call |", "|---|---|---|---|---|---|---|"]
        for item in report["packages"]["packages"]:
            selected = item.get("selected", {}).get("filename") or "none"
            probe = item["probe"]
            gil = f"{probe.get('gil_before')} / {probe.get('gil_after_call')}" if probe["import"] != "not-run" else "-"
            lines.append(f"| {item['name']} {item['version']} | {item['install']['status']} | {selected} | {' '.join(item.get('extension_suffixes', [])) or '-'} | {gil} | {probe['import']} | {probe['call']} |")
        outcome = report["packages"]["outcome"]
        lines += ["", f"Install: {outcome['install']} ({outcome['install_reason']}); import and call: {outcome['execute']}. Thread safety was not tested.", ""]
    return lines


def write_output(path, pairs):
    if path is None:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in pairs:
            if "\n" in value or "\r" in value:
                raise ProbeError(f"the output {key} spans lines")
            handle.write(f"{key}={value}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("command", choices=("wheels", "requirements", "packages", "install-stage", "pin"))
    parser.add_argument("--lock", type=pathlib.Path)
    parser.add_argument("--uv", help="the uv executable, for packages")
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--summary", type=pathlib.Path)
    parser.add_argument("--github-output", type=pathlib.Path)
    parser.add_argument("--metadata-dir", type=pathlib.Path, help="read NAME-VERSION.json files here instead of PyPI")
    parser.add_argument("--target", action="append", help="probe only these packages (packages)")
    parser.add_argument("--sync", help="uv pip sync's exit status (install-stage)")
    parser.add_argument("--check", help="uv pip check's exit status, or skip (install-stage)")
    parser.add_argument("--log", type=pathlib.Path, help="the installer's output (install-stage)")
    parser.add_argument("--wheels", type=pathlib.Path, help="a wheels report (install-stage)")
    parser.add_argument("--requirements", type=pathlib.Path, help="a requirements report (install-stage)")
    parser.add_argument("--name", action="append", help="a lock package to pin (pin)")
    args = parser.parse_args(argv)

    if args.command == "install-stage":
        if args.sync is None or args.check is None or args.log is None or args.wheels is None:
            parser.error("install-stage needs --sync, --check, --log and --wheels")
        if not re.fullmatch(r"[0-9]{1,3}", args.sync) or not re.fullmatch(r"[0-9]{1,3}|skip", args.check):
            parser.error("--sync takes an exit status, and --check an exit status or skip")
        try:
            wheels = json.loads(args.wheels.read_text(encoding="utf-8"))["wheels"] if args.wheels.exists() else None
            requirements_report = json.loads(args.requirements.read_text(encoding="utf-8"))["requirements"] if args.requirements and args.requirements.exists() else None
            log_text = args.log.read_text(encoding="utf-8", errors="replace") if args.log.exists() else ""
            status, reason = install_stage(int(args.sync), None if args.check == "skip" else int(args.check), log_text, wheels, requirements_report)
            write_output(args.github_output, [("status", status), ("reason", reason)])
        except (OSError, KeyError, TypeError, ValueError, ProbeError) as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(f"install: {status} ({reason})")
        return 1 if status == "failed" else 0

    if args.command == "pin":
        if args.lock is None or args.output is None or not args.name:
            parser.error("pin needs --lock, --name and --output")
        try:
            lock = parse_lock(args.lock.read_bytes().decode("utf-8"))
            names = {canonical(name) for name in args.name}
            missing = sorted(names - set(lock))
            if missing:
                raise ProbeError(f"the lock does not pin {', '.join(missing)}")
            args.output.write_text(requirement_text(lock, names), encoding="utf-8")
        except (OSError, UnicodeDecodeError, ProbeError) as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.lock is None or args.output is None:
        parser.error(f"{args.command} needs --lock and --output")
    if tags is None:
        print("error: packaging is not installed in this environment; install it from the lock first", file=sys.stderr)
        return 2
    try:
        lock_bytes = args.lock.read_bytes()
        lock = parse_lock(lock_bytes.decode("utf-8"))
        report = {"schema": SCHEMA, "interpreter": interpreter_facts(),
                  "lock": {"path": args.lock.as_posix() if not args.lock.is_absolute() else args.lock.name, "sha256": hashlib.sha256(lock_bytes).hexdigest()}}
        failed = False
        if args.command == "wheels":
            report["wheels"] = wheels_report(lock, args.metadata_dir)
            pairs = [("uncovered", ",".join(report["wheels"]["uncovered"]))]
        elif args.command == "requirements":
            report["requirements"] = {"missing": unmet_requirements(lock)}
            pairs = [("missing", ",".join(sorted({item["requirement"] for item in report["requirements"]["missing"]})))]
        else:
            if not args.uv:
                parser.error("packages needs --uv")
            report["wheels"] = wheels_report(lock, args.metadata_dir)
            report["packages"] = packages_report(lock, args.uv, args.metadata_dir, args.target)
            outcome = report["packages"]["outcome"]
            failed = outcome["install"] == "failed" or outcome["execute"] == "failed"
            pairs = [("install", outcome["install"]), ("install-reason", outcome["install_reason"]), ("execute", outcome["execute"])]
        text = json.dumps(report, indent=2, ensure_ascii=True, sort_keys=False) + "\n"
        args.output.write_text(text, encoding="utf-8")
        write_output(args.github_output, pairs)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write("\n".join(summary_lines(report)) + "\n")
    except (OSError, UnicodeDecodeError, ValueError, ProbeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for line in summary_lines(report):
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
