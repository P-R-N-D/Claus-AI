# Dependency Updates and Python 3.15 Wheel Strategy

## Status and purpose

Baseline and strategy recorded on 2026-10-09. The baseline below is installed and tested on the stated environment; the Python 3.15 sections record a qualification plan and dated upstream observations. This update does not change any `SEC-*` requirement status or establish production readiness.

Free-threaded compatibility is already the architecture direction in [CONTEXT.md](CONTEXT.md#concurrency-direction) and [ARCHITECTURE.md](ARCHITECTURE.md#python-concurrency). This document makes that direction actionable for Python 3.15's **Limited API and `abi3t` wheels**. Explicit synchronization, isolation, and the GIL-enabled compatibility fallback remain in force; adopting a stable ABI does not replace them.

The objective is the newest **supported, secure, reproducible combination** of packages, interpreter, ABI, and platform. A newer version is the first candidate, not permission to ignore its dependency constraints. Claus currently consumes third-party native extensions; it does not publish its own extension wheels.

The checked baseline is CPython 3.12.15 on Linux x86-64 with a hashed backend lock and a wheel inventory, plus Node 24.21.0/npm 11.21.0 with an npm lock. Neither Python 3.15 nor 3.15t is qualified. Automated dependency alerts, CI, ABI qualification and performance benchmarks remain planned work. The compatibility and security exceptions below prevent treating this batch as an unconditional all-clear.

## Version selection and adjustment

For each update batch:

1. Record the current lock/resolved graph, runtime, package-manager version, target OS/CPU/libc, and passing baseline. Query official release metadata and advisories again; exclude prereleases, yanked releases, and deprecated packages from the default candidate set.
2. Start with the latest stable direct dependencies. Resolve the complete graph, including extras, optional native packages, peer dependencies, build tools, and browser binaries. Keep coupled packages together: Next.js/config, React/React DOM/types, Pydantic/core, psycopg/binary, and each Playwright package with its own browsers.
3. If resolution or verification fails, identify the constraint owner and the concrete failure. First update its compatible parent/plugin; otherwise select the newest supported version satisfying the whole graph and the security floor. Change only the conflicting group, rather than reverting unrelated security updates.
4. A successful resolver run establishes metadata compatibility only. Admit a candidate after its applicable installation, functional, security, ABI, and performance checks pass. A missing wheel, unsupported Python release, or GIL re-enablement is a failed qualification, even if source installation could succeed.
5. Record any deviation from latest: package and dependency path, desired and selected versions, affected runtime/platform, error or upstream support evidence, security assessment, owner, tests, and exit condition. Review at the next relevant upstream release or within 30 days, whichever comes first. Expiry requires review, not an automatic unsafe unpin.

If no supported compatible development-tool release exists, explicitly record an EOL compatibility exception, its containment, owner and replacement deadline. Such an exception is not a supported baseline or a reason to lower the runtime security floor. Known unresolved advisories remain findings even when application runtime exposure has not been demonstrated.

| Failure | Adjustment policy |
|---|---|
| Peer/API mismatch | Update the related plugin/framework first; otherwise use the latest supported release of the conflicting package. Keep a removal condition for the constraint. |
| Wheel unavailable for an ABI/platform | Search secure supported versions and upstream wheel releases. If none qualifies, block that runtime lane and use the already-qualified fallback. An audited source build is a separate exception, not a silent installer fallback. |
| Latest package needs a newer interpreter | Qualify that interpreter independently or choose the latest secure package supporting the current interpreter. Do not change package, ABI, and interpreter together without isolating regressions. |
| Security fix conflicts with the graph | Prefer a compatible parent upgrade or replacement plan. Do not downgrade below the security floor to obtain a wheel. If no safe combination exists, record a blocker and containment plan. |
| Performance or behavior regression | Keep the newest candidate meeting the agreed checks; record the measured regression and retry conditions. Do not suppress a test to make the update pass. |

Do not use `--force`, `--legacy-peer-deps`, disabled certificate checks, renamed wheel tags, or prerelease enabling as a default resolution strategy. An npm override or Python constraint must name the affected dependency path, have compatibility/security evidence, and have an expiry; it must not disguise an unsupported combination.

### Reproducibility

- Keep npm's manifest and lock in the same update and verify with `npm ci` in a fresh environment. Review resolved URLs, integrity values, lifecycle scripts, and unexpected graph changes.
- Preserve backend direct intent in `backend/requirements.txt`; the checked graph is in [the backend lock](../backend/locks/cp312-linux-x86_64.txt), with exact transitive versions and hashes. [Its README](../backend/locks/README.md) records regeneration and fresh installation. Generate separate outputs where runtime/ABI/platform resolution differs. A Python 3.12 resolution is not a 3.15t lock.
- Pin the resolver/installer used to generate and consume the lock. Cache keys include interpreter version, ABI, OS, architecture/libc, tool versions, and lock digest. Virtual environments and native build outputs are never shared between `cp315` and `cp315t`.
- Keep [the artifact manifest](../backend/locks/cp312-linux-x86_64.artifacts.json) with distribution/version, filename, Python/ABI/platform tags, SHA-256, trusted source, observed native library versions and verification environment. It records what was installed, not an installer-enforced wheel allowlist or a complete native-library SBOM. A version-only lock is insufficient to distinguish ABI-specific artifacts.

## Applied baseline and verification

Selected after checking npm/PyPI release metadata on 2026-10-09. Versions below are the checked-in manifest/lock baseline, not a claim about any deployed environment. Re-query upstream metadata and advisories for each subsequent batch.

| Group | Applied version | Migration or selection |
|---|---|---|
| Next.js / eslint-config-next | 16.4.0 | Native flat ESLint configuration; Next 16 TypeScript output. Preserve backend trailing slashes through the proxy so `/core/health/` completes without a redirect loop. |
| axios / PostCSS | 1.20.0 / 8.5.29 | Refresh the complete lock, including nested dependencies. |
| React / React DOM / their types | 19.3.0 | Update together; initialize health loading state without synchronous effect setters and retain explicit retry behavior. |
| Node Playwright | 1.64.0 | Four desktop/mobile, light/dark projects. Paired Chromium download is blocked; see the browser exception below. |
| Tailwind / @tailwindcss/postcss | 4.3.3 | CSS import, matching PostCSS plugin, gradient utility and button cursor migration. Remove obsolete direct autoprefixer and FlatCompat/eslintrc dependencies and the empty Tailwind 3 configuration. |
| ESLint / TypeScript | 9.39.5 / 6.0.3 | ESLint 10 and TypeScript 7 conflict with the current plugins; ESLint 9 is an explicit EOL exception. TypeScript uses `~6.0.3` to preserve the lint tooling's `<6.1` bound. |
| Node / npm / Node types | 24.21.0 LTS / 11.21.0 / 24.19.1 | `.nvmrc`, manifest engines and package-manager pin. npm 12.2.0 fails the tested install path; npm 11 has separate security exceptions below. |
| CPython / uv / verification pip | 3.12.15 / 0.12.24 / 26.2.1 | `.python-version`; exact 50-package hash lock and actual wheel inventory. Package updates also passed the original 3.12.14 baseline before checking the interpreter patch. |
| Django / DRF | 6.1.2 / 3.18.3 | Update range ceilings and retain the existing routing, database and storage tests. Django's checked support table still excludes 3.15. |
| FastAPI / Starlette / Uvicorn | 0.143.0 / 1.7.0 / 0.54.0 | Raise the old FastAPI ceiling; select Starlette through its parent and verify the shared ASGI surface under both servers. |
| Other backend packages | Daphne 4.2.3; cors-headers 4.9.0; psycopg 3.3.6; boto3 1.43.110; Playwright 1.63.0 | Lock the complete graph, including cryptography 50.0.2 and pydantic-core 2.50.0. Python Playwright remains separate from the frontend test dependency. |
| SweetAlert2 | 11.26.25 | Already current; retained. |

Checks executed on Linux x86-64, glibc 2.41:

| Check | Result and scope |
|---|---|
| Fresh backend installations | uv 0.12.24 and, independently, pip 26.2.1 installed the 50 pinned packages with hashes and binary-only enforcement on CPython 3.12.15. `uv pip check`: compatible graph. The resolver targets `manylinux_2_31`; execution on glibc 2.31 was **not** tested. |
| Backend regression | `python backend/manage.py test core agent`: **20 passed** in each fresh environment; Django system check: no issues. `python backend/manage.py makemigrations --check --dry-run`: no changes detected. |
| Server entrypoints | Seven HTTP probes each against Daphne and Uvicorn: Core/Agent health, docs, OpenAPI, ReDoc, admin redirect and unknown-route 404; **14 passed**. No real PostgreSQL, object-store or browser lifecycle integration was exercised. |
| Frontend installation and compile | Node 24.21.0/npm 11.21.0: `npm ci`, `npm run lint`, `npm run build` passed. No forced peer resolution, vulnerability override or certificate bypass was used. |
| UI regression | `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm run test:visual`: **4 passed** using system Chromium **151.0.7922.173**. Both health cards and retries, exact proxy routes without redirects, both pages, console/page/request errors, HTTP errors and horizontal overflow checked. Eight full-page screenshots inspected for clipping. This is supplemental evidence, not qualification of Playwright's paired Chromium. |
| Application dependency audit | `npm audit --package-lock-only --omit=dev`: **0 findings**. Full lock audit: **5 high package entries** in one development dependency chain below. `pip-audit` 2.10.1 with the hashed Python lock: **0 known vulnerabilities across 50 packages**. This does not cover bundled native libraries, browser binaries or the npm CLI itself. |

Commands and setup are in [TESTING.md](TESTING.md), [frontend/README.md](../frontend/README.md) and [backend/locks/README.md](../backend/locks/README.md). The wheel inventory records CPython OpenSSL 3.5.9, cryptography OpenSSL 4.0.3, psycopg-binary libpq 18.6 and SQLite 3.53.1. These are inventory observations, not native-library vulnerability clearance. Python 3.15/3.15t, ABI performance comparisons, production load and paired-browser validation remain unverified.

## Compatibility and security exceptions

Owner for these exceptions: the repository maintainer responsible for this update. Recheck on the relevant upstream release or by **2026-11-08**, whichever comes first. Unresolved findings require an explicit disposition before deployment; this document does not accept their risk for a production environment.

| Exception | Evidence, scope and exit condition |
|---|---|
| ESLint 9.39.5 is EOL | Latest ESLint 10.12.0 is outside the current React, JSX accessibility and import plugins' peer ranges in the Next 16.4 graph. Keep 9.39.5 temporarily for reproducible linting; this is **not a supported lint baseline**. Lift the bound when the complete plugin set supports ESLint 10, or replace the incompatible tooling after equivalent lint coverage is demonstrated. |
| TypeScript 6.0.3 instead of 7.0.2 | `typescript-eslint@8.71.1` supports `>=4.8.4 <6.1.0`. The selected version passes lint/build. Recheck TypeScript 7 when the lint toolchain declares support; do not force the peer graph. |
| npm 11.21.0 instead of 12.2.0 | npm 12.2.0 failed lock generation with `EALLOWREMOTE` while extracting an official-registry Tailwind optional wasm tarball. npm 11.21.0 generated the lock and completed `npm ci`. Retry npm 12 when the registry-tarball handling is fixed; do not disable its remote-dependency protection to bypass the failure. |
| Unfixed development dependency advisory | `eslint-config-next@16.4.0 → @next/eslint-plugin-next@16.4.0 → fast-glob@3.3.1 → micromatch@4.0.8 → braces@3.0.3`: [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). The five high audit entries represent this chain, not five distinct vulnerabilities. No fixed braces release was available. npm's proposed downgrade to eslint-config-next 14.2.35 conflicts with this toolchain. No application request path to this use was found; retain trusted lint inputs and replace/update the chain when a compatible fix exists. |
| npm CLI bundled advisories | The **installer itself**, outside the application lock, has **12 advisory matches across 5 bundled components** (4 high, 7 moderate, 1 low). Both npm 11.21.0 and 12.2.0 contain the same affected versions listed below. CLI exploitability was not established. Use trusted registry/lock/config inputs and an isolated installation environment without production credentials; do not admit arbitrary git/file/URL dependencies or weaken integrity/script controls. Recheck the actual bundled versions in the next npm release; changing only the application lock cannot fix these. |
| Paired browser unavailable | Playwright 1.64.0's Chromium 156.0.8078.4 (revision 1248) download failed with HTTP 403 at `cdn.playwright.dev` in this environment. System Chromium 151 passed the four UI projects, but does not replace the paired-browser check. Run `npx playwright install chromium` and `npm run test:visual` without the executable override in an environment that can download that browser before claiming its qualification. |

The npm CLI's matching advisory records, checked against the registry on 2026-10-09:

| Bundled component | Advisory IDs |
|---|---|
| brace-expansion 5.0.9 | GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p, GHSA-q2hr-2g5m-vwhr |
| http-cache-semantics 4.2.0 | GHSA-ch52-4w7c-c8xp |
| ip-address 10.5.0 | GHSA-rpw4-54j3-4h4q, GHSA-2vr4-cq9g-pvrc, GHSA-j6r3-76f7-8jcv, GHSA-h3mg-xc3c-68pw |
| postcss-selector-parser 7.1.4 | GHSA-rj75-hqrm-r3gf |
| undici 6.28.0 | GHSA-3wwx-pv8p-q78v, GHSA-r53p-7pc4-xj5r, GHSA-rfgv-xxqx-mfg5 |

The lock's registry URLs and integrity records were inspected. The only dependency marked with an install script is `unrs-resolver@1.12.2` (`napi-postinstall`); npm reported that it was not covered by `allowScripts`, and the tested prebuilt resolver still supported lint/build. This observation is not a cross-platform installation guarantee or an enforced repository-wide script policy.

## Python 3.15: Limited API and wheel contract

The Limited API is the source-level C API subset; the Stable ABI is the resulting binary compatibility contract. Python 3.15 introduces `abi3t` through [PEP 803](https://peps.python.org/pep-0803/). It reduces rebuilds across future Python minor versions; it does not make arbitrary existing binaries thread-safe or portable across operating systems.

| Wheel tag example, before the platform suffix | Meaning and Claus handling |
|---|---|
| `cp315-cp315` | CPython 3.15, conventional ABI. Accept only in the corresponding GIL-enabled build lane. |
| `cp315-cp315t` | CPython 3.15, version-specific free-threaded ABI. Valid target when Limited API cannot supply the required functionality or performance. |
| `cp311-abi3` | Conventional Stable ABI with a 3.11 floor. Potentially usable on regular 3.15; **not** evidence of compatibility with 3.15t. |
| `cp315-abi3t` | Free-threaded Stable ABI with a 3.15 floor. Validate installer tags and upstream support; do not infer conventional-build support from this wheel tag alone. |
| `cp315-abi3.abi3t` | Compressed tag set explicitly advertising both stable ABIs. Preferred shared artifact when the package, platform, behavior, and performance qualify. |
| `py3-none-any` / `py3-none-<platform>` | No CPython-specific ABI tag. The latter may bundle native executables, as Playwright does. Neither tag establishes thread safety. |

The Python tag remains `cp315`; `cp315t` is the version-specific ABI tag. `python3.15t` is an interpreter naming convention. Wheel platform tags, `Requires-Python`, system libraries, and upstream support remain independent constraints. PEP 803 does not backport the new ABI to Python 3.14t or make old `abi3` wheels usable on free-threaded builds.

### Selection order

1. Choose the latest secure supported **package version** first. Do not choose an older vulnerable release merely because it has a stable-ABI wheel.
2. At that version, qualify an upstream dual `abi3.abi3t` artifact where available; otherwise an upstream `abi3t` artifact for the free-threaded lane. Share a dual artifact across the two lanes only when both advertise and pass support.
3. Accept upstream `cp315-cp315t` wheels when they are the maintained option or outperform/cover features missing from Limited API. `abi3t` is a maintenance advantage, not a requirement to replace every native wheel.
4. If there is no compatible published wheel, apply the version adjustment policy. Build-from-source exceptions require a pinned source, audited build dependencies, compiler/native-library versions, hashes, and all lane checks. Do not silently remove `psycopg[binary]`, drop Daphne, or switch database backends to turn a failing check green.
5. Re-test reused stable-ABI wheels on every supported Python minor release. ABI stability does not guarantee application semantics, security fixes, or unchanged performance.

### Current native dependency evidence

Wheel publication checked through version-specific PyPI JSON endpoints on 2026-10-09. These are 3.15 availability observations, not successful 3.15 installation or execution. Qualify the actual OS/CPU/libc separately; the initial resolver probe targeted Linux x86-64, glibc 2.31 from a Python 3.12 host.

| Dependency path / version | Published wheel evidence | Consequence |
|---|---|---|
| Daphne → TLS → cryptography 50.0.2 | Conventional `cp311-abi3` and **`cp315-abi3.abi3t`** wheels | First concrete dual-ABI qualification candidate; check bundled OpenSSL as well as the Python package. |
| FastAPI → Pydantic → pydantic-core 2.50.0 | `cp315-cp315` and `cp315-cp315t`; no `abi3t` wheel observed | Keep maintained version-specific wheels; do not force a Limited API rebuild for naming consistency. |
| Python Playwright → greenlet 3.5.6 | Both version-specific 3.15 ABIs | Upstream still labels free-threading experimental and documents races/GC/shutdown caveats. Browser lifecycle and concurrency remain qualification blockers until tested. |
| psycopg-binary 3.3.6 | `cp315-cp315`; no `cp315t` or `abi3t` wheel observed | Blocks a wheel-only 3.15t lane with the current binary extra. Pure-Python psycopg plus separately managed libpq would be an explicit, benchmarked alternative, not an automatic fallback. |
| Daphne → Autobahn 26.7.1 | No matching 3.15 or stable-ABI wheel observed | Track upstream artifacts or a reviewed build exception; do not remove the supported server path incidentally. |
| Daphne → Twisted → zope.interface 8.6 | 3.14/3.14t wheels, no matching 3.15/stable-ABI wheel observed | The regular 3.15 wheel-only resolver probe already fails on this path. Metadata allowing Python 3.15 is insufficient. |
| cffi 2.1.1, cbor2 6.1.5, msgpack 1.2.3, ujson 6.0.0 | Both version-specific 3.15 ABIs observed | Qualify these artifacts; their presence does not qualify the rest of the graph. |
| Django 6.1.2 and pure-Python packages | Installable-looking metadata is not an upstream runtime guarantee | Checked Django 6.1 documentation lists 3.12–3.14. Require explicit 3.15 framework support and application checks before promotion. |

For example, PyPI publishes `cryptography-50.0.2-cp315-abi3.abi3t-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`. This is the intended stable-ABI opportunity, rather than simply installing all `cp315t` wheels. Platform coverage and hashes must be recorded per artifact.

At the snapshot time, [PEP 790](https://peps.python.org/pep-0790/) listed 2026-10-09 as the **expected** 3.15.0 final date, and the checked CPython branch still identified itself as `3.15.0rc3+dev`. Confirm final release artifacts before production qualification; prerelease ABI experiments do not establish final-release support.

## Packaging and build qualification

### Consuming wheels

- Qualify the installer as well as `packaging`: pip vendors its own tag implementation and uv has its own resolver. Upgrading a project's `packaging` package alone need not update either installer. The inspected pip 26.2.1 and packaging 26.3 can enumerate `abi3t` tags; this was a synthetic tag check, not a 3.15t installation test.
- On the **actual target interpreter**, capture `python -VV`, build configuration, supported tags (`python -m pip debug --verbose` and `packaging.tags.sys_tags()`), tool versions, and a binary-only resolution/install from the reviewed lock. Confirm the selected filenames and hashes. Do not assume the installer selects a stable-ABI wheel if it also offers a version-specific wheel.
- Use distinct regular and free-threaded environments. With uv, use the target interpreter via `--python /path/to/target/python`; the initial probe's uv 0.12.19 did **not** accept `--python-version 3.15t`. The applied 3.12 lock uses uv 0.12.24, which has not qualified a 3.15t installation here. A cross-version `--python-version 3.15` probe on Python 3.12 is not a free-threaded qualification.
- The default qualification path is wheel-only (`--only-binary=:all:` for pip) and excludes prereleases. A cache hit, automatic source build, or fallback to an older interpreter must not conceal a missing artifact.
- Verify free-threaded build capability with `sysconfig.get_config_var("Py_GIL_DISABLED") == 1` and actual runtime state with `sys._is_gil_enabled()`. Check before imports, after importing the complete ASGI app and native modules, and after lazy database/TLS/browser initialization and workload execution. Capture extension warnings. Importing an unmarked extension can re-enable the GIL.
- First observe natural import behavior without forcing the GIL off. A `PYTHON_GIL=0` / `-X gil=0` override must not be used to hide unsupported extensions. After dependencies qualify, explicit GIL-on/off runs on 3.15t may diagnose behavior; GIL-on 3.15t is not binary-equivalent to a regular `cp315` build.

### Publishing or rebuilding extensions, if later required

Claus has no native extension build to migrate today. For an upstream contribution or a justified local build:

- Prefer a build backend with documented `abi3t` support. Python 3.15's documentation names meson-python, scikit-build-core, and Maturin; qualify the concrete backend/compiler versions and pin the build environment separately from application dependencies.
- `Py_LIMITED_API` targets conventional `abi3`; `Py_TARGET_ABI3T` targets `abi3t`. CPython 3.15 supports two dual-ABI build paths: define both macros, or define `Py_LIMITED_API` with a free-threaded build configuration, where `Py_GIL_DISABLED` makes `Py_TARGET_ABI3T` default to that value. The latter uses free-threaded headers on non-Windows systems and explicitly defines `Py_GIL_DISABLED` on Windows. Use a supported 3.15-or-newer ABI floor and the backend's documented mechanism; verify the resulting filename and wheel metadata.
- Renaming an existing wheel does not convert its ABI. Enabling only `Py_LIMITED_API` on a conventional build does not establish `abi3t` compatibility; the supported free-threaded configuration above is a distinct build path, not an exception to binary validation.
- Audit Limited API usage and free-threading module declarations, opaque object layout assumptions, export hooks, borrowed references, shared native state, and external C/Rust libraries. Use the 3.15 canonical C API/migration documentation, not older guidance saying free-threaded Stable ABI does not exist.
- Retain separate conventional `abi3` wheels for older supported Python releases, and version-specific wheels where the required API or performance cannot be met through Limited API. Build once per qualified OS/CPU/libc target; stable ABI does not collapse that platform matrix.

## Performance admission

Keep the existing free-threading direction. This policy measures the additional **ABI choice** and the readiness of each artifact; it does not infer speed from a wheel tag or defer thread safety until a later feature.

Run separate controlled comparisons with the same package versions and workload:

| Question | Comparison |
|---|---|
| Runtime effect | Regular 3.15 vs 3.15t at the same CPython patch, with the GIL actually disabled in the latter; fixed CPU quota, worker count, data, and native-library versions. Measure migration from the existing Python baseline separately. |
| Free-threaded Limited API cost | On the same 3.15t runtime, compare the version-specific `cp315t` artifact with `abi3t`/dual artifacts built from the same source, with matching native-library versions and comparable compiler settings. If comparable artifacts do not exist, record the comparison as unavailable. |
| Conventional-build dual-ABI cost | On the same regular 3.15 runtime, compare the dual artifact with the conventional `cp315` or `abi3` artifact it would replace, using the same source, matching native-library versions, and comparable compiler settings. Qualifying the dual artifact on 3.15t alone does not qualify its performance on regular 3.15. If comparable artifacts do not exist, record the comparison as unavailable. |

Separate interpreter upgrades, package updates, JIT/PGO/LTO changes, and worker tuning into distinct measurements. Do not attribute all changes to `abi3t`. JIT is a separate experiment, not a prerequisite for this strategy or an assumed speedup on free-threaded builds.

Use one warm-up followed by at least five measured runs at fixed concurrency, including 1/2/4/8 threads where the CPU quota permits. Record throughput per allocated CPU, p50/p95/p99 latency, peak/steady RSS, CPU time, event-loop lag, lock contention, errors/timeouts, and startup/import cost. Compare the same workload and settings; a health endpoint alone is insufficient.

Required workloads as they become available: ASGI routing and JSON validation; PostgreSQL connection/transaction concurrency; S3 uploads, collision retries, and presigning; and Playwright launch/page/close/cancellation. Current storage tests use a fake client, SQLite is the local fallback, and the browser factory does not launch a browser. Those facts are not production performance evidence.

Initial project acceptance budgets, to record before running a batch:

- Zero correctness, context-isolation, crash, deadlock, or unexpected GIL-reenable failures, including a concurrency/GC/shutdown soak of at least 30 minutes.
- Relative to the comparable qualified baseline: p95 regression at most 5%, p99 at most 10%, throughput per CPU reduction at most 5%, and peak RSS growth at most 10%; also meet the workload's absolute latency and memory limits. Report variation across runs, not only the best sample.
- A changed budget requires a recorded workload rationale and review. If Limited API fails these budgets but a maintained version-specific wheel passes, retain that wheel and record the exception. Reduced build-matrix maintenance may justify modest costs only within an explicit budget.

Carry existing concurrency rules into each ABI check: compound operations need explicit synchronization; DB/session/browser objects retain their documented thread or event-loop ownership. Recheck lazy S3 initialization across multiple storage instances: a per-instance lock does not by itself establish that boto3's shared default Session is safe. Native-library safety and client metadata/event-hook mutation require separate review. Test context propagation explicitly because thread context inheritance defaults differ between conventional and free-threaded builds; no personal/team identity may leak through reused workers.

## Security maintenance work

[SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) remains the only definition and status registry for `SEC-*` requirements. The following is an update/remediation plan; it does not mark any control implemented.

### Dependency and artifact security

- Audit the exact npm lock both with and without development dependencies, and audit each resolved Python lane. Include build tooling, browser executables, OS libraries, and libraries bundled into wheels or Node packages, such as OpenSSL, libpq, libvips/libheif, and Chromium. A Python-package-only scan cannot establish their safety.
- Stable ABI means an old wheel can keep loading across Python releases. It does **not** mean bundled libraries get security fixes when CPython is upgraded. Rebuild or replace affected artifacts, refresh hashes and the artifact inventory, and retest all consuming lanes.
- Use trusted registries and reviewed sources, integrity/hash verification, available provenance/attestations, and a per-release component inventory/SBOM. Inspect new maintainers, unexpected package substitutions, install scripts, and build dependencies in the changed graph. Perform build/install validation in an isolated environment without production credentials.
- Record advisory IDs, dependency paths, patched versions, exposure conditions, and residual risk. Do not treat every transitive advisory as a remotely exploitable application flaw, or dismiss development tooling merely because it is absent from production.

The pre-update npm audit reported 19 affected packages (1 critical, 15 high, 3 moderate), including 6 without dev dependencies (1 critical, 5 high). The applied graph reduces those results to 5 development-only high package entries and 0 production entries. Updating FastAPI removed the old Starlette 0.46.2 ceiling; the exact 50-package backend graph has no known pip-audit findings. The development chain, npm CLI findings and native/browser coverage limits remain explicit above. Counts are package-level findings or advisory matches as labelled, not proof of exploitability or comprehensive security clearance.

### Existing application gaps to preserve in the remediation backlog

| Work item | Current fact and required follow-through | Existing registry |
|---|---|---|
| Non-local settings | Development key fallback and DEBUG=true still exist. Plan enforced non-local secret/debug settings, host/CORS/CSRF/cookie/TLS configuration, and deployment checks; do not infer these from a dependency upgrade. | `SEC-SECRET-002`, `SEC-API-003` |
| API boundary | `/agent/*` bypasses Django middleware. Product authentication/authorization is absent; add default-deny controls before non-health product routes and review documentation endpoint exposure. | `SEC-AUTH-001`–`004`, `SEC-API-002` |
| Storage and credentials | Preserve validated names, conditional no-overwrite writes, and failure behavior. Presigned URLs still need scoped authorization and safe logging when product access exists. | `SEC-FILE-002`–`005`, `SEC-SECRET-003`, `SEC-IDEM-004` |
| Browser/runtime | Only a lazy factory exists. Browser/native-library patching, task isolation, cleanup, and credential/context separation remain required with the runtime implementation. | `SEC-RUNTIME-001`–`004` |

The maintainer owning an update owns its exceptions and rechecks. Planned operating cadence: review dependency/advisory changes weekly, check blockers when upstream releases appear, and re-qualify each release artifact before promotion. For a credible reachable critical issue, triage and contain within 24 hours; aim to remediate high issues within 7 days and other actionable findings within 30 days. A missing fix or inaccessible test environment requires a documented containment/ownership decision, not a silent deadline waiver. These are project maintenance targets; alerting and enforcement are not implemented.

## Implementation sequence and release evidence

| Stage | Concrete result | Exit evidence |
|---|---|---|
| 1. Establish baseline and patch security | Applied: Next/axios/PostCSS and FastAPI/Starlette updates, runtime pins, backend hash lock and wheel inventory. Package regression tests passed on 3.12.14 before checking 3.12.15. | Fresh uv/pip and npm installs, graph/advisory checks and backend/frontend checks above. Residual findings are not cleared by this stage. |
| 2. Complete compatible stable updates | Applied: Next 16, Tailwind 4, React, Django/DRF/Uvicorn migrations and compatible tool versions. | Existing server paths and UI surfaces pass the recorded checks. ESLint EOL, development/npm CLI advisories and paired-browser qualification remain open. |
| 3. Qualify Python 3.15 artifacts | Planned, blocked: capture final-release status, upstream runtime support, platform wheel matrix and installer capabilities; test regular 3.15 and 3.15t separately. | Every graph edge has a supported artifact/build exception; no accidental sdist fallback, missing binary extra, or GIL re-enablement. |
| 4. Prefer validated stable-ABI artifacts | Planned: admit dual/abi3t wheels package by package; retain qualified version-specific wheels where needed. | ABI/runtime comparisons, functional/security results, artifact hashes/native-library inventory, and rollback record. |

Current blockers keep the 3.15 lanes unqualified; they do not suspend the free-threading architecture requirement or justify postponing security fixes on the existing runtime. Release only the combination that has passed, while tracking missing upstream wheels/support explicitly.

For each batch retain the interpreter/ABI/platform and tool versions, manifest/lock/artifact digests, dependency diff, compatibility exceptions, advisory disposition, commands and results, performance data, and an independent review. Roll back code, runtime, and the matching known-good lock/artifacts together. Preserve security floors; never solve a regression by reinstalling a known vulnerable graph. Database/schema changes are outside this strategy's update batches and must follow their own approved migration/rollback plan.

Verification follows [TESTING.md](TESTING.md), including Django checks and core/agent tests, both Daphne-backed runserver and direct Uvicorn routes, frontend lint/build/Playwright, and the additional ABI/performance checks here. Changed security boundaries and cited requirement statuses follow [CODE-REVIEW.md](CODE-REVIEW.md) and [SECURITY-REVIEW.md](SECURITY-REVIEW.md). This batch changes dependency/build configuration and docs; it adds no CI, dependency-alert automation or deployment infrastructure. Independent review evidence belongs in the accompanying PR, separately from the author's self-assessment and test results.

## Evidence and sources

The applied-baseline section records actual installation, application regression, visual and package-audit checks. Separate 3.15 strategy evidence consists of official npm/PyPI version and peer metadata; published wheel filenames/tags for the 50-package graph; synthetic tag enumeration with pip 26.2.1/packaging 26.3; and a binary-only regular-3.15 resolver probe. That probe failed through Daphne → Twisted → zope.interface. uv warned that the actual interpreter was 3.12.14, so this is only a target-metadata probe. No 3.15/3.15t interpreter, workload on those runtimes, or ABI performance benchmark was executed. The initial uv 0.12.19 `--python-version 3.15t` attempt was rejected by its parser and supplies no free-threaded resolver evidence.

Authoritative references checked on 2026-10-09 (live branches/registry entries can change):

- [PEP 790: 3.15 release schedule](https://peps.python.org/pep-0790/) and [source](https://github.com/python/peps/blob/main/peps/pep-0790.rst).
- [PEP 803: abi3t](https://peps.python.org/pep-0803/), [3.15 C API and ABI stability](https://github.com/python/cpython/blob/3.15/Doc/c-api/stable.rst), and [3.15 What's New](https://github.com/python/cpython/blob/3.15/Doc/whatsnew/3.15.rst).
- [CPython free-threading guide](https://github.com/python/cpython/blob/3.15/Doc/howto/free-threading-python.rst): build detection, actual GIL state, import behavior, synchronization and context defaults.
- [Django 6.1 Python support](https://github.com/django/django/blob/stable/6.1.x/docs/faq/install.txt), [greenlet caveats](https://github.com/python-greenlet/greenlet/blob/master/docs/caveats.rst), [psycopg release notes](https://github.com/psycopg/psycopg/blob/master/docs/news.rst), and [boto3 client threading](https://github.com/boto/boto3/blob/develop/docs/source/guide/clients.rst).
- Wheel records: [cryptography 50.0.2](https://pypi.org/pypi/cryptography/50.0.2/json), [pydantic-core 2.50.0](https://pypi.org/pypi/pydantic-core/2.50.0/json), [greenlet 3.5.6](https://pypi.org/pypi/greenlet/3.5.6/json), [psycopg-binary 3.3.6](https://pypi.org/pypi/psycopg-binary/3.3.6/json), [Autobahn 26.7.1](https://pypi.org/pypi/autobahn/26.7.1/json), [zope.interface 8.6](https://pypi.org/pypi/zope-interface/8.6/json).
- Compatibility records: [Next 15.5.27](https://registry.npmjs.org/next/15.5.27), [Next 16.4.0](https://registry.npmjs.org/next/16.4.0), [eslint-plugin-react 7.37.5](https://registry.npmjs.org/eslint-plugin-react/7.37.5), [typescript-eslint 8.71.1](https://registry.npmjs.org/typescript-eslint/8.71.1), [TypeScript 6.0.3](https://registry.npmjs.org/typescript/6.0.3), [FastAPI 0.115.14](https://pypi.org/pypi/fastapi/0.115.14/json), and [Starlette 0.46.2 advisories](https://pypi.org/pypi/starlette/0.46.2/json).
- Tool exception records: [ESLint version support](https://eslint.org/version-support/), [npm 11.21.0](https://registry.npmjs.org/npm/11.21.0), [npm 12.2.0](https://registry.npmjs.org/npm/12.2.0), and [npm registry bulk advisory endpoint](https://registry.npmjs.org/-/npm/v1/security/advisories/bulk) (POST with the installed component versions).
