"""Check that pytest collects exactly the tests Django's test runner discovers.

CI runs the backend suite with both `python manage.py test core agent` and
pytest. A pytest configuration that silently collects fewer (or other) tests
would still pass, so this compares the two test ID sets without running them.

Usage, from backend/ with the test lock installed:
    python ../scripts/ci/check_test_parity.py
"""

import os
import subprocess
import sys

LABELS = ["core", "agent"]


def django_test_ids():
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.test.runner import DiscoverRunner
    from django.test.utils import iter_test_cases

    django.setup()
    suite = DiscoverRunner(verbosity=0).build_suite(LABELS)
    return {test.id() for test in iter_test_cases(suite)}


def pytest_test_ids():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        sys.exit(f"pytest --collect-only exited {result.returncode}")
    ids = set()
    for line in result.stdout.splitlines():
        if "::" not in line:
            continue
        parts = line.strip().split("::")
        if len(parts) != 3 or not parts[0].endswith(".py"):
            sys.exit(f"Unexpected pytest node ID (expected path::Class::method): {line}")
        path, cls, method = parts
        module = path[: -len(".py")].replace("/", ".")
        ids.add(f"{module}.{cls}.{method}")
    return ids


def main():
    django_ids = django_test_ids()
    pytest_ids = pytest_test_ids()
    print(f"Django runner: {len(django_ids)} tests; pytest: {len(pytest_ids)} tests")
    if not django_ids:
        print("::error::Django discovered no tests")
        return 1
    only_django = sorted(django_ids - pytest_ids)
    only_pytest = sorted(pytest_ids - django_ids)
    for test_id in only_django:
        print(f"::error::Collected by Django only: {test_id}")
    for test_id in only_pytest:
        print(f"::error::Collected by pytest only: {test_id}")
    if only_django or only_pytest:
        return 1
    print("Both runners collect the same test IDs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
