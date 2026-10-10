"""Check that the backend test lock pins the backend lock unchanged.

The test lock (locks/cp312-linux-x86_64-test.txt) is the backend lock's graph
plus the test-only packages. Every package in the backend lock must appear in
the test lock at the same version with the same set of hashes, and every other
package in the test lock must be pulled in only by requirements-test.txt or by
another test-only package (read from uv's "# via" annotations). So the tests
run against the graph that the backend lock installs, and a package that the
backend lock dropped or never received cannot hide in the test lock.

Usage, from the repository root (standard library only):
    python scripts/ci/check_test_lock.py backend/locks/cp312-linux-x86_64.txt backend/locks/cp312-linux-x86_64-test.txt
"""

import re
import sys

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


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    base_path, test_path = argv[1], argv[2]
    base, _ = parse_lock(base_path)
    test, test_parents = parse_lock(test_path)
    problems = []
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
        f"and the {len(test_only)} test-only packages come only from requirements-test.txt."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
