"""Check the backend locks against each other, their requirement inputs, and the installed metadata.

The backend lock (locks/cp312-linux-x86_64.txt) is compiled from
requirements.txt, and the integrated test lock (locks/cp312-linux-x86_64-test.txt)
from requirements.txt and requirements-test.txt with the backend lock as a
constraint, both for CPython 3.12.15 on x86_64-manylinux_2_31. The check fails
unless all of the following hold:

1. Each lock holds only `name==version` pins, each with at least one sha256
   hash and no duplicate, and its header records that target.
2. Every backend lock pin appears in the test lock with the same version and the
   same set of hashes.
3. Every package only in the test lock is pulled in, per uv's "# via"
   annotations, only by requirements-test.txt or by another test-only package.
4. Each requirement input holds only named requirements, with optional extras,
   version specifiers and markers: no direct URL, path, archive file name,
   editable or option line. Every marker, in an input or in the metadata of a
   package the walk in rule 6 reads, uses only the forms listed at
   STRING_VARIABLES below, which packaging and uv evaluate alike.
5. For each lock and each of its inputs: every requirement whose marker holds on
   the target is pinned at a version its specifier allows and is recorded as a
   direct requirement of that input, and nothing else is recorded as one. A
   requirement whose marker does not hold on the target counts as absent.
6. Every package the locks pin is installed in the running environment at the
   test lock's version, and following the installed packages' Requires-Dist
   metadata from each lock's inputs, with the requested extras and with markers
   evaluated for the target, reaches exactly the packages that lock pins. Every
   requested extra must be one the package provides, and every dependency
   specifier must allow the pinned version.

So an input change fails when, on the target, it adds or drops a direct
requirement, changes which packages the requirements, extras and markers reach,
or excludes a pinned version, and always when it breaks rule 4. Any other input
change passes, such as a widened or removed version range, or a lowered
minimum, that still allows the pin, a marker that holds on the target, or an
extra whose packages the lock already pins; while the index offers the same
files, regenerating then changes at most the locks' "# via" annotations. The
check does not re-resolve: it does not check that the pins are the newest
allowed versions, that the hashes belong to the files on the index (uv checks
the files it downloads against them when it installs), or anything about
another platform or Python version.

Usage, from the repository root, in the environment installed from the test
lock (it reads that environment's package metadata and needs only the standard
library and `packaging`, which both locks pin). `-I` keeps files in this
directory or the current one from standing in for installed packages:
    python -I scripts/ci/check_test_lock.py \
        backend/locks/cp312-linux-x86_64.txt backend/locks/cp312-linux-x86_64-test.txt \
        backend/requirements.txt backend/requirements-test.txt
"""

import importlib.metadata
import os
import re
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*\\?$")
HASH = re.compile(r"^\s+--hash=(sha256:[0-9a-f]{64})\s*\\?$")
VIA_INLINE = re.compile(r"^\s+# via (\S.*)$")
VIA_START = re.compile(r"^\s+# via$")
VIA_ITEM = re.compile(r"^\s+#   (\S.*)$")
COMMAND = re.compile(r"^#\s+uv pip compile\s")
# pip's rule: "#" starts a comment at the start of a line or after whitespace.
COMMENT = re.compile(r"(^|\s+)#.*$")
# pip reads a requirement name ending in most of these as a local file, and uv 0.12.24 one ending in
# .whl, .zip, .tar.gz, .tgz or .tar; the check rejects them all.
ARCHIVE = re.compile(r"\.(whl|zip|tar|tgz|tbz2?|txz|tlz|tar\.(gz|bz2|xz|lz|lzma|zst))$", re.IGNORECASE)

# Marker forms that packaging, which evaluates markers here, and uv 0.12.24,
# which compiles the locks, evaluate alike on the target (checked 2026-10-10 by
# compiling `six ; <marker>` for 224 markers): `and`, `or` and parentheses over
# comparisons of a variable on the left with a quoted value on the right, where
# the variable is one of STRING_VARIABLES compared with == or !=, or one of
# VERSION_VARIABLES compared with ==, !=, <, <=, > or >= to a plain release
# such as "3.12". The two disagree on other forms, such as `in`, `~=`, `===`,
# `platform_release`, a version with "*" or a local part, or `extra` in a
# requirements file, so those fail. Package metadata may also use
# `extra == "<name>"`.
STRING_VARIABLES = frozenset(
    {
        "implementation_name",
        "os_name",
        "platform_machine",
        "platform_python_implementation",
        "platform_system",
        "sys_platform",
    }
)
VERSION_VARIABLES = frozenset({"python_full_version", "python_version"})
PLAIN_RELEASE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
MARKER_TOKEN = re.compile(
    r"""\s*(?:([()])|(and|or)(?![\w.])|([A-Za-z_][\w.]*)|(==|!=|<=|>=|<|>)(?!=)|"([^"]*)"|'([^']*)')"""
)
MARKER_KINDS = ("paren", "boolean", "variable", "operator", "value", "value")

# The lock headers must record this target, and markers are evaluated for it
# rather than for the machine running the check. These are the marker values uv
# 0.12.24 uses for `--python-version 3.12.15 --python-platform
# x86_64-manylinux_2_31` (checked 2026-10-10 by compiling `six ; <marker>`).
TARGET_FLAGS = (("--python-version", "3.12.15"), ("--python-platform", "x86_64-manylinux_2_31"))
TARGET = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.15",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.12.15",
    "python_version": "3.12",
    "sys_platform": "linux",
}
TARGET_LABEL = "CPython 3.12.15 on x86_64-manylinux_2_31"


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def unsupported_marker(marker, metadata=False):
    """Return why `marker` uses a form outside the ones packaging and uv evaluate alike, or None.

    `metadata` also allows `extra == "<name>"`, which package metadata uses to select an extra.
    packaging has parsed the marker, so its text is well formed; only the comparisons need checking.
    """
    text = str(marker).strip()
    tokens = []
    position = 0
    while position < len(text):
        match = MARKER_TOKEN.match(text, position)
        if match is None:
            return f"unsupported syntax at {text[position:]!r}"
        tokens.append((MARKER_KINDS[match.lastindex - 1], match.group(match.lastindex)))
        position = match.end()
    while tokens:
        kind, variable = tokens.pop(0)
        if kind in ("paren", "boolean"):
            continue
        if kind != "variable" or [token[0] for token in tokens[:2]] != ["operator", "value"]:
            return "a comparison must be a variable, an operator, and a quoted value, in that order"
        (_, operator), (_, value) = tokens[:2]
        del tokens[:2]
        if variable in STRING_VARIABLES:
            supported = operator in ("==", "!=")
        elif variable in VERSION_VARIABLES:
            supported = PLAIN_RELEASE.match(value) is not None
        else:
            supported = metadata and variable == "extra" and operator == "=="
        if not supported:
            return f"unsupported comparison {variable} {operator} {value!r}"
    return None


def holds(marker, extra=""):
    """Return whether `marker` holds on the target with `extra` requested; a missing marker always holds."""
    return marker is None or marker.evaluate(dict(TARGET, extra=extra))


def parse_lock(path):
    """Return ({normalized name: (version, frozenset of hashes)}, {normalized name: [via entries]}).

    Fails on anything unexpected, and unless the header records the target.
    """
    pins = {}
    parents = {}
    command = None
    current = None
    in_via = False
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.rstrip("\n")
            stripped = line.strip()
            if current is not None:
                via = VIA_INLINE.match(line)
                if via:
                    parents[current].append(via.group(1))
                    in_via = False
                    continue
                if VIA_START.match(line):
                    in_via = True
                    continue
                item = VIA_ITEM.match(line)
                if in_via and item:
                    parents[current].append(item.group(1))
                    continue
            in_via = False
            if current is None and command is None and COMMAND.match(line):
                # uv records `--flag value` or `--flag=value` as it was given; read both alike.
                command = [
                    part for word in line.split() for part in (word.split("=", 1) if word[:2] == "--" else [word])
                ]
                continue
            if not stripped or stripped.startswith("#"):
                continue
            requirement = REQUIREMENT.match(line)
            if requirement:
                name = normalize(requirement.group(1))
                if name in pins:
                    fail(f"{path}:{number}: duplicate pin for {name}")
                current = name
                pins[name] = (requirement.group(2), set())
                parents[name] = []
                continue
            digest = HASH.match(line)
            if digest and current is not None:
                pins[current][1].add(digest.group(1))
                continue
            fail(f"{path}:{number}: unexpected line: {stripped}")
    if not pins:
        fail(f"{path}: no pinned packages found")
    for name, (version, hashes) in pins.items():
        if not hashes:
            fail(f"{path}: {name}=={version} has no hashes")
    if command is None:
        fail(f"{path}: no 'uv pip compile' command in the header")
    for flag, value in TARGET_FLAGS:
        if command.count(flag) != 1 or command[command.index(flag) + 1 :][:1] != [value]:
            fail(
                f"{path}: the header does not record {flag} {value}; "
                f"this check evaluates markers for {TARGET_LABEL}"
            )
    return {name: (version, frozenset(hashes)) for name, (version, hashes) in pins.items()}, parents


def read_input(path):
    """Return [(line number, Requirement)] for `path`, failing on any line that is not a named requirement."""
    requirements = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = COMMENT.sub("", line).strip()
            if not text:
                continue
            try:
                requirement = Requirement(text)
            except InvalidRequirement as error:
                fail(f"{path}:{number}: unsupported requirement line: {text} ({error})")
            if requirement.url:
                fail(
                    f"{path}:{number}: direct URL requirements are not supported, because the locks pin "
                    f"only index releases by version and hash: {text}"
                )
            if ARCHIVE.search(requirement.name):
                fail(f"{path}:{number}: unsupported requirement line: {text} (pip and uv read it as a local file)")
            reason = None if requirement.marker is None else unsupported_marker(requirement.marker)
            if reason:
                fail(
                    f"{path}:{number}: unsupported marker in {text}: {reason}; this check accepts only the marker "
                    "forms that packaging and uv evaluate alike"
                )
            requirements.append((number, requirement))
    if not requirements:
        fail(f"{path}: no requirements found")
    return requirements


def is_direct(entry, input_name):
    """Return whether a "# via" entry records a direct requirement of the input file named `input_name`."""
    return entry.startswith("-r ") and os.path.basename(entry[3:]) == input_name


def check_input(path, requirements, lock, parents, lock_path):
    """Return problems where the requirements in `path` and the lock's direct entries for it disagree."""
    problems = []
    names = set()
    input_name = os.path.basename(path)
    for number, requirement in requirements:
        if not holds(requirement.marker):
            continue
        name = normalize(requirement.name)
        names.add(name)
        if name not in lock:
            problems.append(f"{path}:{number}: {requirement} is not pinned in {lock_path}; regenerate the lock")
        elif not requirement.specifier.contains(lock[name][0], prereleases=True):
            problems.append(
                f"{path}:{number}: {requirement} does not allow {name}=={lock[name][0]} pinned in {lock_path}; "
                "regenerate the lock"
            )
        elif not any(is_direct(entry, input_name) for entry in parents[name]):
            problems.append(
                f"{path}:{number}: {requirement} is not recorded as a direct requirement of {input_name} "
                f"in {lock_path}; regenerate the lock"
            )
    for name in sorted(lock.keys() - names):
        if any(is_direct(entry, input_name) for entry in parents[name]):
            problems.append(
                f"{name}=={lock[name][0]} is a direct requirement of {input_name} in {lock_path}, "
                f"but {path} does not require it on {TARGET_LABEL}; regenerate the lock"
            )
    return problems


def check_environment(lock, lock_path):
    """Return problems where the running environment does not hold the lock's versions."""
    problems = []
    for name, (version, _) in sorted(lock.items()):
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(
                f"{name}=={version} is not installed here; "
                f"run this check in the environment installed from {lock_path}"
            )
            continue
        try:
            same = Version(installed) == Version(version)
        except InvalidVersion:
            same = installed == version
        if not same:
            problems.append(
                f"{name} is installed at {installed}, but {lock_path} pins {version}; "
                f"run this check in the environment installed from {lock_path}"
            )
    return problems


def walk(roots, lock, lock_path):
    """Follow the installed Requires-Dist metadata from `roots` on the target.

    `roots` are the input requirements whose markers hold on the target;
    check_input has already checked their pins. Returns (names reached, problems).
    """
    problems = []
    extras = {}
    pending = []

    def reach(requirement, source):
        name = normalize(requirement.name)
        if name not in lock:
            if source is not None:
                problems.append(
                    f"{source} requires {requirement}, but {lock_path} does not pin {name}; regenerate the lock"
                )
            return
        version = lock[name][0]
        if source is not None and not requirement.specifier.contains(version, prereleases=True):
            problems.append(
                f"{source} requires {requirement}, which does not allow {name}=={version} pinned in {lock_path}"
            )
        requested = {normalize(extra) for extra in requirement.extras}
        if name not in extras:
            extras[name] = requested
            pending.append((name, {""} | requested))
        elif requested - extras[name]:
            pending.append((name, requested - extras[name]))
            extras[name] |= requested

    for requirement in roots:
        reach(requirement, None)
    while pending:
        name, active = pending.pop()
        source = f"{name}=={lock[name][0]}"
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{source} is not installed here, so its dependencies cannot be read")
            continue
        provided = {normalize(extra) for extra in distribution.metadata.get_all("Provides-Extra") or []}
        for extra in sorted(active - {""} - provided):
            problems.append(f"{source} does not provide the extra {extra!r}")
        for text in distribution.requires or []:
            try:
                dependency = Requirement(text)
            except InvalidRequirement as error:
                problems.append(f"{source}: cannot read the dependency {text!r} ({error})")
                continue
            if dependency.marker is None:
                needed = "" in active
            else:
                reason = unsupported_marker(dependency.marker, metadata=True)
                if reason:
                    problems.append(f"{source}: cannot evaluate the dependency {text!r} the way uv does: {reason}")
                    continue
                needed = any(holds(dependency.marker, extra) for extra in active)
            if not needed:
                continue
            if dependency.url:
                problems.append(f"{source} requires the direct URL {text!r}, which the locks cannot pin")
                continue
            reach(dependency, source)
    return set(extras), problems


def main(argv):
    if len(argv) != 5:
        sys.exit(__doc__)
    base_path, test_path, base_input, test_input = argv[1:]
    base, base_parents = parse_lock(base_path)
    test, test_parents = parse_lock(test_path)
    base_requirements = read_input(base_input)
    test_requirements = read_input(test_input)
    problems = check_input(base_input, base_requirements, base, base_parents, base_path)
    problems += check_input(base_input, base_requirements, test, test_parents, test_path)
    problems += check_input(test_input, test_requirements, test, test_parents, test_path)
    for name, (version, hashes) in sorted(base.items()):
        if name not in test:
            problems.append(f"{name}=={version} is missing from {test_path}")
            continue
        test_version, test_hashes = test[name]
        if test_version != version:
            problems.append(f"{name}: {base_path} pins {version}, {test_path} pins {test_version}")
        elif test_hashes != hashes:
            problems.append(f"{name}=={version}: hash sets differ between the two locks")
    test_only = test.keys() - base.keys()
    test_input_name = os.path.basename(test_input)
    for name in sorted(test_only):
        via = test_parents[name]
        if not via:
            problems.append(f"{name}=={test[name][0]} has no '# via' annotation in {test_path}")
            continue
        outside = [
            entry for entry in via if not is_direct(entry, test_input_name) and normalize(entry) not in test_only
        ]
        if outside:
            problems.append(
                f"{name}=={test[name][0]} is only in {test_path} but is pulled in by {', '.join(outside)}; "
                f"regenerate {base_path} first, then {test_path}"
            )
    environment = check_environment(test, test_path)
    problems += environment
    if environment:
        problems.append("The metadata walk was skipped, because the running environment does not hold the test lock")
    else:
        for lock, lock_path, inputs in (
            (base, base_path, [(base_input, base_requirements)]),
            (test, test_path, [(base_input, base_requirements), (test_input, test_requirements)]),
        ):
            roots = [
                requirement
                for _, requirements in inputs
                for _, requirement in requirements
                if holds(requirement.marker)
            ]
            reached, walked = walk(roots, lock, lock_path)
            problems += walked
            for name in sorted(lock.keys() - reached):
                problems.append(
                    f"{name}=={lock[name][0]} is pinned in {lock_path}, but no requirement in "
                    f"{' or '.join(path for path, _ in inputs)} needs it on {TARGET_LABEL}, directly or through "
                    "the installed metadata; regenerate the lock"
                )
    extra = sorted(f"{name}=={test[name][0]}" for name in test_only)
    print(f"{base_path}: {len(base)} packages; {test_path}: {len(test)} packages")
    print("Test-only packages: " + (", ".join(extra) if extra else "none"))
    if problems:
        for problem in dict.fromkeys(problems):
            print(f"::error::{problem}")
        return 1
    print(
        f"All {len(base)} backend lock pins appear in the test lock with identical versions and hashes, "
        f"the {len(test_only)} test-only packages come only from {test_input_name}, both locks satisfy their "
        f"requirement inputs, and on {TARGET_LABEL} the inputs' requirements, extras and markers reach exactly "
        f"the {len(base)} and {len(test)} pinned packages through the installed metadata."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
