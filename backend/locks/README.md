# Backend dependency locks

`../requirements.txt` is the direct dependency input. `cp312-linux-x86_64.txt` is the exact 50-package graph with SHA-256 hashes, generated using **uv 0.12.24**, targeting CPython **3.12.15** and `x86_64-manylinux_2_31`. It is not a universal or free-threaded lock.

From the repository root, after installing that uv version:

```bash
uv venv --python 3.12.15
source .venv/bin/activate
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
uv pip check
```

Regenerate after reviewing the direct input and upstream advisories:

```bash
uv pip compile backend/requirements.txt \
  --python-version 3.12.15 \
  --python-platform x86_64-manylinux_2_31 \
  --generate-hashes --only-binary :all: \
  --output-file backend/locks/cp312-linux-x86_64.txt \
  --index-url https://pypi.org/simple --no-emit-index-url
```

Add `--upgrade` when intentionally refreshing already-pinned transitive versions. Review the resulting graph and hashes, install in a fresh environment, and repeat [the checks](../../docs/TESTING.md). Never regenerate just to bypass a hash mismatch.

The lock contains hashes for multiple artifacts of each pinned version. The separate `cp312-linux-x86_64.artifacts.json` records the actual 50 wheel filenames, tags, URLs and hashes selected by a verification installation with **pip 26.2.1** on CPython 3.12.15, Linux x86-64, **glibc 2.41**, plus observed OpenSSL/libpq/SQLite versions. Both uv and pip installations passed the 20 backend tests. The resolver's glibc 2.31 target is not proof that the selected glibc 2.41-host artifacts run on 2.31; qualify the actual deployment platform independently.

To refresh that evidence, use pip 26.2.1 in a fresh target environment:

```bash
python -m pip install --require-hashes --only-binary=:all: \
  --index-url https://pypi.org/simple \
  --report /tmp/claus-backend-install-report.json \
  -r backend/locks/cp312-linux-x86_64.txt
```

Record the report's wheel filenames, tags, SHA-256 values, trusted URLs and runtime/tool versions; retain the matching lock digest and recheck native-library versions. A report from an already-satisfied environment can omit installed packages, so it cannot replace the complete artifact inventory. The manifest is evidence, not an installer-enforced restriction to those particular wheel files, and it is not a complete native-library SBOM or vulnerability scan.

For another Python minor, free-threaded ABI, OS, CPU or libc, resolve from the input into a separately named lock and run its own installation, application and artifact checks. Do not reuse this manifest as support evidence. Python 3.15/3.15t remains unqualified; see [DEPENDENCY-STRATEGY.md](../../docs/DEPENDENCY-STRATEGY.md).
