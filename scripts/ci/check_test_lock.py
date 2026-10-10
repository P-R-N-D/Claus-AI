"""Check that the backend test lock pins the backend lock unchanged.

The test lock (locks/cp312-linux-x86_64-test.txt) is the backend lock's graph
plus the test-only packages. Every package in the backend lock must appear in
the test lock at the same version with the same set of hashes, and every other
package in the test lock must be pulled in only by requirements-test.txt or by
another test-only package (read from uv's "# via" annotations). So the tests
run against the graph that the backend lock installs, and a package that the
backend lock dropped or never received cannot hide in the test lock.

It also reads the two requirement inputs: every requirement in
requirements.txt must be pinned in the backend lock, and every requirement in
requirements-test.txt in the test lock, at a version its specifier allows, and
neither lock may keep a direct requirement its input no longer lists. An input
changed without regenerating its lock therefore fails.

Usage, from the repository root, in the test lock environment (it needs only
the standard library and `packaging`, which both locks pin):
    python scripts/ci/check_test_lock.py \
        backend/locks/cp312-linux-x86_64.txt backend/locks/cp312-linux-x86_64-test.txt \
        backend/requirements.txt backend/requirements-test.txt
"""

import os
import re
import sys

from packaging.requirements import InvalidRequirement, Requirement

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*\\?$")
HASH = re.compile(r"^\s+--hash=(sha256:[0-9a-f]{64})\s*\\?$")
VIA_INLINE = re.compile(r"^\s+# via (\S.*)$")
VIA_START = re.compile(r"^\s+# via$")
VIA_ITEM = re.compile(r"^\s+#   (\S.*)$")


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path):
    """Return ({normalized name: (version, frozenset of hashes)}, {normalized name: [via entries]}).

    Fails on anything unexpected.
    """
    pins = {}
    parents = {}
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
            if not stripped or stripped.startswith("#"):
                continue
            requirement = REQUIREMENT.match(line)
            if requirement:
                name = normalize(requirement.group(1))
                if name in pins:
                    sys.exit(f"{path}:{number}: duplicate pin for {name}")
                current = name
                pins[name] = (requirement.group(2), set())
                parents[name] = []
                continue
            digest = HASH.match(line)
            if digest and current is not None:
                pins[current][1].add(digest.group(1))
                continue
            sys.exit(f"{path}:{number}: unexpected line: {stripped}")
    if not pins:
        sys.exit(f"{path}: no pinned packages found")
    for name, (version, hashes) in pins.items():
        if not hashes:
            sys.exit(f"{path}: {name}=={version} has no hashes")
    return {name: (version, frozenset(hashes)) for name, (version, hashes) in pins.items()}, parents


def is_test_input(entry):
    return entry.startswith("-r ") and entry.endswith("requirements-test.txt")


def check_inputs(path, lock, parents, lock_path):
    """Return problems where the requirements in `path` and the lock's direct entries for it disagree."""
    problems = []
    names = set()
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.split("#", 1)[0].strip()
            if not text:
                continue
            try:
                requirement = Requirement(text)
            except InvalidRequirement as error:
                sys.exit(f"{path}:{number}: unsupported requirement line: {text} ({error})")
            name = normalize(requirement.name)
            names.add(name)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            if name not in lock:
                problems.append(f"{path}:{number}: {requirement} is not pinned in {lock_path}; regenerate the lock")
            elif not requirement.specifier.contains(lock[name][0], prereleases=True):
                problems.append(
                    f"{path}:{number}: {requirement} does not allow {name}=={lock[name][0]} pinned in {lock_path}; "
                    "regenerate the lock"
                )
    if not names:
        sys.exit(f"{path}: no requirements found")
    input_name = os.path.basename(path)
    for name in sorted(lock.keys() - names):
        if any(entry.startswith("-r ") and os.path.basename(entry[3:]) == input_name for entry in parents[name]):
            problems.append(
                f"{name}=={lock[name][0]} is a direct requirement of {input_name} in {lock_path}, "
                f"but {path} no longer lists it; regenerate the lock"
            )
    return problems


def main(argv):
    if len(argv) != 5:
        sys.exit(__doc__)
    base_path, test_path, base_input, test_input = argv[1:]
    base, base_parents = parse_lock(base_path)
    test, test_parents = parse_lock(test_path)
    problems = check_inputs(base_input, base, base_parents, base_path)
    problems += check_inputs(test_input, test, test_parents, test_path)
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
    for name in sorted(test_only):
        via = test_parents[name]
        if not via:
            problems.append(f"{name}=={test[name][0]} has no '# via' annotation in {test_path}")
            continue
        outside = [entry for entry in via if not is_test_input(entry) and normalize(entry) not in test_only]
        if outside:
            problems.append(
                f"{name}=={test[name][0]} is only in {test_path} but is pulled in by {', '.join(outside)}; "
                f"regenerate {base_path} first, then {test_path}"
            )
    extra = sorted(f"{name}=={test[name][0]}" for name in test_only)
    print(f"{base_path}: {len(base)} packages; {test_path}: {len(test)} packages")
    print("Test-only packages: " + (", ".join(extra) if extra else "none"))
    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1
    print(
        f"All {len(base)} backend lock pins appear in the test lock with identical versions and hashes, "
        f"the {len(test_only)} test-only packages come only from requirements-test.txt, "
        "and both locks satisfy their requirement inputs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
