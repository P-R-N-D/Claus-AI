# Backend dependency locks

`../requirements.txt` is the direct dependency input for the resolver, not an install target: installing it directly resolves current releases and does not enforce the transitive security floors held only in this lock, such as Starlette 1.7.0 and urllib3 2.8.0 (see [Security floors and release freshness](../../docs/DEPENDENCY-STRATEGY.md#security-floors-and-release-freshness)). `cp312-linux-x86_64.txt` is the exact 50-package graph with SHA-256 hashes, generated using **uv 0.12.24**, targeting CPython **3.12.15** and `x86_64-manylinux_2_31`. It is not a universal or free-threaded lock.

From the repository root, after installing that uv version:

```bash
uv venv --managed-python --python 3.12.15
source .venv/bin/activate
python -c "import sqlite3; print(sqlite3.sqlite_version)"  # must print 3.37.0 or newer
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
uv pip check
```

`--managed-python` makes uv use its python-build-standalone CPython rather than a system interpreter, whose SQLite can be older than Django 6.1's 3.37.0 floor. Record `python -VV` and `sys.executable` with any qualification result.

Regenerate after reviewing the direct input and upstream advisories, and apply the release-freshness rule in [DEPENDENCY-STRATEGY.md](../../docs/DEPENDENCY-STRATEGY.md#security-floors-and-release-freshness): a release younger than 7 days at selection time gets a recorded freshness and provenance review unless the project has adopted a cooldown, and no security floor is lowered to satisfy one.

```bash
uv pip compile backend/requirements.txt \
  --python-version 3.12.15 \
  --python-platform x86_64-manylinux_2_31 \
  --generate-hashes --only-binary :all: \
  --output-file backend/locks/cp312-linux-x86_64.txt \
  --index-url https://pypi.org/simple --no-emit-index-url
```

Add `--upgrade` when intentionally refreshing already-pinned transitive versions. Review the resulting graph and hashes, install in a fresh environment, and repeat [the checks](../../docs/TESTING.md). Never regenerate just to bypass a hash mismatch.

The lock contains hashes for multiple artifacts of each pinned version. The separate `cp312-linux-x86_64.artifacts.json` records the actual 50 wheel filenames, tags, URLs and hashes selected by a verification installation with **pip 26.2.1** on CPython 3.12.15, Linux x86-64, **glibc 2.41**, plus observed OpenSSL/libpq/SQLite versions. Both uv and pip installations passed the 20 backend tests that existed at the time (the suite now has 23; see [TESTING.md](../../docs/TESTING.md)). The resolver's glibc 2.31 target is not proof that the selected glibc 2.41-host artifacts run on 2.31; qualify the actual deployment platform independently. On glibc 2.28 through 2.33, which includes that 2.31 target, wheel-tag priority makes the installer select cryptography's `manylinux_2_28` wheel (static tag-selection reasoning, not an executed install) instead of the `manylinux_2_34` wheel recorded in the manifest; its hash is also in the lock, but it is a different artifact from the one inventoried.

To refresh that evidence, use pip 26.2.1 in a fresh target environment:

```bash
python -m pip install --require-hashes --only-binary=:all: \
  --index-url https://pypi.org/simple \
  --report /tmp/claus-backend-install-report.json \
  -r backend/locks/cp312-linux-x86_64.txt
```

Record the report's wheel filenames, tags, SHA-256 values, trusted URLs and runtime/tool versions; retain the matching lock digest and recheck native-library versions. A report from an already-satisfied environment can omit installed packages, so it cannot replace the complete artifact inventory. The manifest is evidence, not an installer-enforced restriction to those particular wheel files, and it is not a complete native-library SBOM or vulnerability scan.

Other platforms: this lock has been installed only on Linux x86-64. [Platform and accelerator lanes](../../docs/DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes) records static, unexecuted evidence for the rest. Linux arm64 (glibc), including NVIDIA DGX Spark and WSL2 on Windows on Arm, and macOS 15 or later on Apple Silicon are statically resolvable: every pin has a hashed wheel for them. Using this lock there is an unqualified local-development shortcut; qualifying such a platform needs its own separately named lock. Native Windows cannot produce a working environment from it: on Windows x64 it installs without `tzdata`, which Django and psycopg require on Windows, and on Windows on Arm devices such as NVIDIA RTX Spark or Qualcomm Snapdragon PCs the install fails because `autobahn`, `cryptography` and `psycopg-binary` have no `win_arm64` wheels; use WSL2 there. macOS 14 or older, Intel Macs and musl (Alpine) are not binary-installable. The graph contains no CUDA or other accelerator-specific package. Statically resolvable is not qualified; a platform counts as checked only after its own installation and application checks.

For another Python minor, free-threaded ABI, OS, CPU or libc, resolve from the input into a separately named lock and run its own installation, application and artifact checks. Do not reuse this manifest as support evidence. Python 3.15/3.15t remains unqualified; see [DEPENDENCY-STRATEGY.md](../../docs/DEPENDENCY-STRATEGY.md).

## Integrated test lock

`cp312-linux-x86_64-test.txt` (added 2026-10-10) is the environment for running the backend tests with pytest. It resolves `../requirements.txt` and `../requirements-test.txt` together, constrained by `cp312-linux-x86_64.txt`, so it contains the backend lock's 50 pins with identical versions and hashes plus 5 test-only packages: pytest 9.1.1, pytest-django 4.14.0, pluggy 1.6.0, iniconfig 2.3.1 and Pygments 2.21.0 (packaging 26.3, which pytest also needs, is already in the backend lock). It was generated with **uv 0.12.24** for the same CPython **3.12.15** and `x86_64-manylinux_2_31` target and is Linux x86-64 only: on Windows, pytest also needs `colorama`, which is not in the lock. The backend lock is unchanged and remains the application baseline; install the test lock only where the tests run.

```bash
uv pip compile backend/requirements.txt backend/requirements-test.txt \
  -c backend/locks/cp312-linux-x86_64.txt \
  --python-version 3.12.15 \
  --python-platform x86_64-manylinux_2_31 \
  --generate-hashes --only-binary :all: \
  --output-file backend/locks/cp312-linux-x86_64-test.txt \
  --index-url https://pypi.org/simple --no-emit-index-url
python scripts/ci/check_test_lock.py backend/locks/cp312-linux-x86_64.txt backend/locks/cp312-linux-x86_64-test.txt
```

Regenerate it whenever the backend lock changes, in the same change, and apply the same release-freshness rule. `scripts/ci/check_test_lock.py` (standard library only) fails when a backend lock package is missing from the test lock or has another version or hash set, or when a package only in the test lock is pulled in by anything other than `requirements-test.txt` and its dependencies; CI runs it in the pytest job. No artifact manifest was recorded for the five test packages; they are pure-Python wheels.
