# Claus CI Matrix and Compatibility Model

Added 2026-10-11. This document defines how a compatibility lane is described, how a run on a lane is recorded, and what the validator checks. Commands and the CI job are listed in [TESTING.md](TESTING.md); which lanes are supported is recorded only in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes); CI permissions and trust boundaries are in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "CI workflow".

Implemented today: the schema, the validator, the environment collector, one real profile, nine synthetic fixtures, their tests, and one CI job on the checked lane (GitHub-hosted `ubuntu-24.04` x86-64). No other lane runs in CI. Nothing in this model is a support claim, and a synthetic fixture is not evidence that any platform works.

## Files

| Path | Role |
|---|---|
| `tests/compatibility/schema.json` | Structure of profiles and results, written in the JSON Schema subset described under "Structure" below. |
| `tests/compatibility/profiles/` | Real profiles: one per lane that a CI job records. Today only `linux-x64-gha.json`. |
| `tests/compatibility/fixtures/` | Synthetic profiles and results for scenarios no job runs (Windows on Arm, WSL2, Podman, Kubernetes, CUDA). They test the model, not a platform. |
| `scripts/ci/collect_environment.py` | Records the environment of the process that runs it as a result document. |
| `scripts/ci/validate_compatibility.py` | Checks profiles and results: structure from the schema, then the rules under "Rules across fields". |
| `scripts/ci/tests/test_compatibility_schema.py`, `scripts/ci/tests/test_environment_probe.py` | Tests of the validator and the collector, including documents that must fail. |

All of these use the Python standard library only and run without Django.

## Profiles and results

A **profile** declares a lane: the layers it runs on, the runtimes it uses, its compute backends, the stages a run must pass, its privilege policy, the CI job that records it, and its support status. A profile holds only `declared`, `unknown`, and `not-applicable` facts; it cannot claim to have observed anything.

A **result** records one run: what the collector observed on the machine, the stages and their status, and the CI run and commits it belongs to. `schema_version` is 1 for both; `kind` is `profile` or `result`.

A document with `synthetic: true` was never measured. All its evidence references start with `synthetic:`, a synthetic profile's `id` starts with `synthetic-`, and a synthetic result has `generated_at: null` and cannot name a GitHub Actions run. `--require-real` rejects synthetic documents, and a real result can only be checked against a real profile.

## Axes

Each axis is a separate field. Host, guest, container, and workload are never merged into one OS or CPU value.

| Axis | Where it is recorded |
|---|---|
| Execution layers | `environment.layers`, outermost first, 1 to 8 layers. `kind` is `host`, `vm`, `wsl2`, or `container`; `technology` names the hypervisor or engine (`kvm`, `hyper-v`, `wsl2`, `podman`, `containerd`, ...). |
| OS per layer | `os.family`, `os.name`, `os.version`, `os.kernel`. A container layer has no kernel of its own. |
| CPU per layer | `cpu.arch` and `cpu.execution` (`native` or `emulated`). |
| Service manager per layer | `service_manager.name` and `scope`, for example systemd inside a container, or a host systemd running Quadlet units with user scope. |
| Container engine and orchestration | `container.engine_mode` (`rootless` or `rootful`), `privileged`, `devices`, `orchestration.name`, `version`, and `scope`, on container layers only. |
| Workload identity | `workload.identity`: `uid`, `gid`, `superuser`, and the user namespace (`initial`, `uid_map`). |
| Workload isolation | `workload.isolation`: `no_new_privs`, effective and bounding capabilities, seccomp mode, AppArmor, SELinux, cgroup version and delegation. |
| Runtimes and ABI | `workload.runtimes.python` (implementation, version, cache tag, SOABI, platform tag, build architecture, pointer size, free-threaded build, GIL state, libc, execution) and `workload.runtimes.node` (version, module ABI, N-API version, platform, architecture, execution). |
| Compute backends | `compute.backends`: each has an `id`, a `kind` (`cpu`, `gpu`, `npu`, `other`), an `api`, availability, device, version, and features. A CPU-only lane lists one `cpu` backend. |
| Run and revision | `run.ci` (system, event, repository, ref, run ID and attempt, run URL, workflow ref, job, runner environment, OS, architecture, image) and `run.revision` (see "Commits" below). |
| Stages | `stages`: the seven stages below, each with its status. |

Documented values: architectures `x86_64`, `aarch64`, `x86`, `armv7`, `riscv64`, `ppc64le`, `s390x`, `loongarch64` (aliases such as `amd64` or `arm64` are errors; write the canonical name); OS families `linux`, `windows`, `macos`, `freebsd`; backend APIs `cpu`, `cuda`, `rocm`, `oneapi`, `level-zero`, `opencl`, `vulkan-compute`, `directcompute`, `directml`, `metal`, `mps`, `coreml`, `qnn`, `wasm`, `webgpu`, `webgl`, `webnn`; and the technologies, orchestrators, and service managers listed in the validator. Any other value is accepted with an `unrecognized-value` warning, and no consistency rule applies to it. An unrecognized value is never treated as supported.

`extensions` objects with `x-` keys may appear on documents, layers, backends, and stages for data the schema has no field for. The validator checks their strings for control characters and secret-shaped values and nothing else, and never reads them as evidence. There are no per-technology validators.

### Layers and nesting

Layers run from the outermost (index 0) inwards; the workload runs in the last one. The validator requires:

- A `host` is only ever index 0, and a `vm`, `wsl2`, or `container` always has a layer around it. When the outer layer cannot be seen, it is still listed, with `unknown` facts and a reason.
- A container shares the kernel of the layer below it, so it has the same OS family and records no kernel.
- WSL2 runs on a Windows layer and is itself Linux.
- Only container layers carry container facts, and every container layer carries them.

Examples, from the fixtures and the profile:

| Scenario | Layers |
|---|---|
| GitHub-hosted runner | host (all facts unknown) / vm (Hyper-V, Ubuntu 24.04, x86-64) |
| Windows on Arm, native | host (Windows, aarch64) |
| Windows on Arm, WSL2, rootless Podman | host (Windows, aarch64) / wsl2 (Ubuntu, aarch64) / container (Podman, rootless) |
| VM with a Podman container running systemd | host / vm (KVM, Fedora, SELinux enforcing) / container (systemd inside; the VM's systemd runs it as a user-scope Quadlet unit) |
| Kubernetes pod with CUDA on aarch64 | host (Linux, aarch64) / container (containerd, rootful, orchestration kubernetes, device `nvidia-gpu`) |

### Facts and provenance

Every measurable value is a fact: `{value, provenance, evidence, reason}`.

| Provenance | Meaning | Required |
|---|---|---|
| `observed` | Read on the machine by the collector. | A value and at least one evidence reference. |
| `inferred` | Derived from named evidence. | A value and evidence. |
| `declared` | Stated by a profile, a workflow, or a tool, not measured. | A value. |
| `unknown` | Not established. | `value: null` and a reason. |
| `not-applicable` | Has no meaning here. | `value: null` and a reason. |

Evidence references have the form `<kind>:<location>`, with kind `file`, `env`, `python`, `cmd`, `event`, `git`, `tool`, `doc`, `profile`, `workflow`, `log`, `artifact`, or `synthetic`. A reference names where a value came from; it never holds the value of a secret or the contents of a file.

### CPU execution

`native` means no user-mode emulation was found; `emulated` means some layer translates the instructions. The collector compares the architecture a process was built for with the kernel's own architecture from `/proc/sys/kernel/arch` (Linux 6.1 and later), which qemu-user's `uname` interception does not rewrite. The Python architecture comes from the interpreter's build triplet (`sys.implementation._multiarch`, then `SOABI`, then the ELF header of the executable), not from `sysconfig.get_platform()`, which on Linux follows `uname`. Without `/proc/sys/kernel/arch`, a process is reported `emulated` only when `/proc/cpuinfo` shows another CPU family, and `unknown` otherwise. 32-bit code on a 64-bit kernel of the same family (`x86` on `x86_64`, `armv7` on `aarch64`) is native.

Limits: a whole system emulated by its hypervisor looks native from inside; translators that also present the emulated architecture as the kernel's (such as FEX or box64 setups, or Rosetta inside a Linux VM) have not been evaluated; and a layer's own `cpu.execution` is `unknown` in a collected record, because a guest cannot see whether the layer below emulates it.

## Privilege model (Rootless First)

These facts are independent, and none is derived from another:

| Fact | Field | Note |
|---|---|---|
| The workload's user | `workload.identity.superuser`, `uid` | Root or not inside its own user namespace. |
| User namespace | `user_namespace.initial`, `uid_map` | `initial` is read from the inode of `/proc/self/ns/user`. An identity `uid_map` (`0 0 4294967295`) does not prove the initial namespace, because a nested namespace can have one too. |
| Engine mode | `container.engine_mode` | Whether the engine runs without root on its host. |
| Privileged container | `container.privileged` | |
| Capabilities | `isolation.capabilities.effective`, `bounding` | |
| No new privileges, seccomp, AppArmor, SELinux | `isolation.*` | AppArmor's label is read from `/proc/self/attr/apparmor/current` only; SELinux's context only when SELinux is the first major security module listed. |
| cgroup version and delegation | `isolation.cgroup` | The version is the file system mounted at `/sys/fs/cgroup`; delegation is read for cgroup v2, as a process without root or capabilities, from write permission on its own cgroup. |
| Devices | `container.devices` | Devices the engine passes in. |
| Provisioning and runtime privilege | `stages.<stage>.privilege.escalation` | `none`, `sudo`, `root-login`, `setuid`, or `other`, per stage. An `install` that used `sudo` and an `execute` that used `none` are recorded as such. |

A rootless engine does not make the process inside the container non-root: uid 0 in a rootless Podman container is still the superuser of its namespace, and `app-non-root` fails for it. A root process inside a container does not make the engine rootful.

### Policy

| Rule | Default | Checks |
|---|---|---|
| `app-non-root` | required | `superuser` is false. |
| `engine-rootless` | preferred | Every container layer's engine is rootless. |
| `no-privileged-container` | required | Every container layer is unprivileged. |
| `no-privilege-escalation` | required | Every stage that ran used no escalation. |
| `minimal-capabilities` | required | The workload holds no effective capabilities. |
| `minimal-devices` | required | No device is passed to a container. |

A rule passes only when the record shows compliance: an `unknown` value counts as a violation. `minimal-capabilities` checks the effective set the workload holds. The bounding set, which limits what a setuid program could gain, is recorded but not checked, because a non-root process on an ordinary host has the full bounding set. Inside a user namespace other than the initial one, capabilities apply to that namespace only; the rule still counts them. A violation of a required rule is an error, and of a preferred rule a warning. A profile can raise a rule from preferred to required; it cannot lower a required rule (`policy-weakened`). Inside a container the collector cannot see the privileged state or the devices, and Podman writes the engine's rootless state to `/run/.containerenv` only for privileged containers, so a container lane's record fails the default policy until its job records those facts from the engine side; no job does that yet.

A profile that needs to break a rule records an exception with `id`, `rule`, `reason`, `scope` (the layers, stages, capabilities, or devices it covers, as the rule requires), `verification`, `end_condition`, and `approval` (`approved`, `pending`, or `rejected`, with a reference and a date when approved). Only an approved exception covers a violation, and only inside its scope; a pending or rejected one leaves the error (`policy-exception-not-approved`), and an approved one that covers nothing gives a warning. The validator checks that the approval is recorded, not that it is genuine: reviewing an exception's approval reference is a reviewer's step. A profile cannot hold observed facts, so an exception cannot be used to state facts a run did not show.

## Stages and statuses

| Stage | What it covers | Needs |
|---|---|---|
| `detect` | The environment was recorded. The collector sets it. | |
| `install` | The lane's tools and dependencies were installed. | `detect` |
| `initialize` | The application or runtime started and loaded its configuration. | `install` |
| `execute` | The workload ran. | `initialize` |
| `correctness` | Its results were checked against expectations. | `execute` |
| `security` | Security checks ran on this lane. | `detect` |
| `performance` | It was measured against a budget. | `correctness` |

Statuses: `passed`, `failed`, `blocked` (could not run because a prerequisite failed or something it needs is missing), `not-run` (nobody ran it), and `not-applicable`. A `passed` or `failed` stage cites the log or artifact that shows it and records its privilege escalation; every status other than `passed` gives a reason; a `not-run` stage cites nothing; and a stage can be `passed` or `failed` only when every stage it needs is `passed` or `not-applicable`.

`overall` is derived: `failed` if any stage failed, `passed` if every stage is `passed` or `not-applicable` and at least one passed, and `incomplete` otherwise. The current CI job runs `detect` only, so its records are `incomplete` by design.

A stage that used a compute backend records `compute.requested` and `compute.effective` as backend IDs. A stage cannot pass when a `gpu` or `npu` backend was requested and another backend, or none, ran (`accelerator-fallback`): record such a run as `failed` with the reason, or request the CPU backend for a CPU run.

### Stage status and support status

Stage status describes one run. Support status describes a lane and is recorded only in the [lane table](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes), whose words map as follows: Checked to `checked`, Statically resolvable to `statically-resolvable`, Not supported, not binary-installable, or incomplete to `not-supported`. A profile copies that status into `support.status` with the lane's name; `not-assessed` is for a lane the table does not list. With `--dependency-strategy`, the validator fails a profile whose status differs from the table, and a synthetic profile cannot claim `checked`.

No run changes support status. A passed stage, a green job, or a new profile never promotes a lane; promoting one, for example to Checked, is a reviewed change to [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md) with its evidence. `linux-x64-gha` claims `checked` because the table records Linux x86-64 glibc as Checked from earlier installs, not because of the compatibility job.

## Validation

### Structure: a JSON Schema subset

The validator interprets these keywords and no others: `type`, `enum`, `const`, `properties`, `required`, `additionalProperties`, `propertyNames`, `items`, `minItems`, `maxItems`, `minLength`, `maxLength`, `pattern`, `minimum`, `maximum`, `$ref`, `if`, `then`, and `else`, with `$schema`, `$id`, `$comment`, `$defs`, `title`, and `description` accepted as annotations. A schema that uses any other keyword (such as `oneOf`, `anyOf`, `allOf`, `not`, `format`, `patternProperties`, `uniqueItems`, or `unevaluatedProperties`) is rejected before any document is read, with exit status 2, so the schema cannot ask for a check the validator does not perform. It is not a general JSON Schema implementation. Where its result can differ from a JSON Schema 2020-12 validator:

- `$ref` resolves only `#/$defs/<name>`, and `$defs` is allowed only at the root.
- `pattern` is matched with Python's `re.search`, not the ECMA-262 dialect. The committed patterns use only syntax both read the same way.
- A number written with a fraction or an exponent, such as `1.0`, is a `number`, not an `integer`.
- After a `type` mismatch, the other keywords of that subschema are not applied, so one finding is reported for it.
- Before the schema runs, a document is rejected (`json`) if it is larger than 2 MiB, is not UTF-8, starts with a byte order mark, repeats an object key, holds `NaN`, `Infinity`, or a number outside the double range, or nests too deeply.

The rules across fields run only on a document whose structure is valid; otherwise a `semantic-skipped` warning says so.

### Rules across fields

Each finding has a stable code. Errors make the document invalid; warnings do not.

| Code | Level | Catches |
|---|---|---|
| `schema` | error | A structure error from the schema. |
| `json` | error | A file that cannot be read as strict JSON. |
| `control-character`, `secret-like-value` | error | A control or bidirectional formatting character, or a value shaped like a GitHub, AWS, Slack, or JWT token or a private key. |
| `fact-provenance` | error | A fact whose value, evidence, or reason does not fit its provenance. |
| `profile-provenance` | error | An observed or inferred fact in a profile. |
| `synthetic-marking` | error | Synthetic and real data mixed, or a synthetic document where `--require-real` asks for a measured one. |
| `arch-name` | error | An alias instead of the canonical architecture name. |
| `unrecognized-value` | warning | A value outside the documented lists. |
| `layer-structure` | error | Layer order or container facts that break the nesting rules. |
| `os-contradiction` | error | A container with another OS family than its parent, WSL2 off Windows or not Linux, or a runtime built for another OS than the innermost layer. |
| `cpu-contradiction` | error | Emulated execution declared native (a layer, Python, or Node whose architecture cannot run natively on the layer below), or an emulated outermost host. |
| `abi-contradiction` | error | A Python platform tag, architecture, pointer size, cache tag, SOABI, free-threading flag, or GIL state that disagree. |
| `identity-contradiction` | error | `superuser` that disagrees with uid 0, or an initial user namespace without the identity map. |
| `backend-reference` | error | Duplicate backend IDs, a stage naming a backend that is not listed, a backend used by a stage that did not run, or a passing stage on a backend not recorded as available. |
| `accelerator-fallback` | error | A GPU or NPU stage that passed on another backend. |
| `stage-fields`, `stage-order` | error | A stage without its evidence, reason, or privilege record, a not-run stage with evidence, or a stage that ran before its prerequisites passed. |
| `overall-mismatch` | error | `overall` that the stages do not give. |
| `ci-evidence` | error | A GitHub Actions record without its run, its `GITHUB_SHA`, or, for a pull request, its head and base, or with a run URL for another repository or run. |
| `policy-<rule>` | error or warning | A policy violation with no approved exception; a warning for a preferred rule. |
| `policy-exception`, `policy-exception-not-approved`, `policy-weakened` | error | A malformed or unscoped exception, a violation covered only by an unapproved exception, or a profile that lowers a required rule. |
| `policy-exception-unused` | warning | An approved exception that covers nothing in the result. |
| `support-claim` | error | A support status that differs from the lane table, names no lane, or is `checked` in a synthetic profile. |
| `support-claim-unverified` | warning | A `checked` claim validated without `--dependency-strategy`. |
| `evidence-source` | error | A profile's evidence source that names a missing workflow file or a job the file does not define. |
| `profile-link` | error | A result checked against a profile it does not name, a real result against a synthetic profile or the reverse, or a job that is not one of the profile's evidence sources. |
| `profile-mismatch`, `profile-unverified` | error | A layer count, layer fact, container fact, runtime fact, or backend that differs from the profile, or a declared fact the run did not establish. |
| `required-stage` | error | A stage the profile requires that did not pass. |
| `profile-unchecked` | warning | A result naming a profile that was not given; the default policy applies. |

### Detection failures and validation failures

These are kept apart. A file the collector cannot read gives an `unknown` fact with a reason, not an error. An unexpected exception inside one probe is recorded in `probe_errors` with the probe's name; the other probes' values stay in the record, `detect` becomes `failed`, and the collector exits 1 after writing the record. The validator then reports the failed `detect` as `required-stage` against a profile that requires it. A record that breaks a rule fails with that rule's code. The collector exits 2 on a usage error or when it cannot write the record, and the validator exits 2 on a usage error or an unusable file or schema.

## Collector

The collector reads `/proc`, `/sys`, `/etc/os-release`, two files under `/run`, the checkout's `.git/HEAD`, the head and base SHAs from the GitHub Actions event file, a fixed allowlist of environment variables, and the output of `node -p` run with an empty environment. It never iterates the environment, uses no network, never escalates privileges, changes no system state (it does not touch `binfmt_misc`, which can trigger an automount, and decides cgroup delegation from permission bits without writing), and writes only its output file and the optional summary. It records no host name, account name, home directory, network address, serial number, or other environment variable. On systems other than Linux it records the OS family and the Python runtime and leaves the rest `unknown`.

It describes its own process. It does not see the privileges or environment of other steps in the same job; a job that runs later stages passes their status, evidence, and escalation with the `--stage`, `--stage-reason`, `--stage-evidence`, and `--stage-escalation` options, and the values are recorded as declared. It does not assume a guest can see its physical host: on a VM, the host layer's facts are `unknown`.

## Profiles, workflows, and evidence

- A profile's `evidence_sources` name a workflow file under `.github/workflows/` and a job in it; the validator checks that both exist.
- A result names its profile in `profile_id`. With `--profile`, the validator checks that the IDs match, that a GitHub Actions record's job is one of the profile's evidence sources, and that every declared fact in the profile matches the result. A fact the profile leaves `unknown` is not compared, so a profile declares only what its job can observe.
- The CI job validates its record with `--require-real`, `--profile`, and `--dependency-strategy`, then uploads it as an artifact (see [TESTING.md](TESTING.md) "Continuous integration").

### Commits

`run.revision` keeps four commit IDs apart: `tested_sha`, the commit the checkout holds (`.git/HEAD`); `event_sha`, `GITHUB_SHA`, which for a `pull_request` run is GitHub's temporary merge commit; and `pr_head_sha` and `pr_base_sha`, the pull request's head and base, read from the event file. Only these two values are taken from the event payload. The run URL is inferred from `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, and `GITHUB_RUN_ID`.

### Current lanes and fixtures

| Document | Kind | Support status | Run by |
|---|---|---|---|
| `profiles/linux-x64-gha.json` | real profile, required stage `detect` | `checked` (Linux x86-64 glibc) | `compatibility` job in `.github/workflows/ci.yml` |
| `fixtures/linux-x64-vm.*` | synthetic, CPU only | `not-assessed` | none |
| `fixtures/windows-arm64-native.*` | synthetic, install failed, QNN NPU listed | `not-supported` | none |
| `fixtures/windows-arm64-wsl2-podman.*` | synthetic, rootless Podman with a non-root user | `statically-resolvable` | none |
| `fixtures/vm-podman-quadlet-systemd.result.json` | synthetic, systemd in the container and on the VM | no profile | none |
| `fixtures/kubernetes-arm64-cuda.*` | synthetic, CUDA, approved device exception | `not-assessed` | none |

## Adding a lane

A later change that adds an OS, ABI, accelerator, or runtime lane:

1. Adds a profile under `tests/compatibility/profiles/` that declares only what its job can observe, copies its support status from the lane table (or uses `not-assessed`), and names the job in `evidence_sources`.
2. Adds the job, which runs the collector with `--profile-id` and passes the stages its other steps ran with the `--stage*` options, citing their logs or artifacts and their privilege escalation, then runs the validator with `--require-real`, `--profile`, and `--dependency-strategy`.
3. Adds whatever probe the lane needs that the collector does not have, such as Windows or macOS facts, engine-side container facts, or accelerator inventory, without per-technology validators. Values outside the documented lists warn until a consistency rule for them is added; data without a field goes in `x-` extensions. A change that removes or narrows a field increments `schema_version`.
4. Changes a lane's support status only by editing [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md) with the evidence.

## What a passing job means

A passing compatibility job shows, for that commit and runner, that the collector ran there and wrote a record that is structurally valid, breaks no rule above, matches every fact the profile declares, and passes `detect`; that the committed profiles validate against the lane table; and that the validator's and collector's tests passed, including the documents that must fail.

It does not show that any other OS, architecture, Python or Node version, or accelerator works; that the application installs, starts, or behaves correctly on this lane (those stages are `not-run`, and `overall` is `incomplete`); that no step of the job escalated privileges (GitHub-hosted runners provide passwordless `sudo`, which this job does not use and the collector does not test, and membership in the `docker` group, which would also be root-equivalent, is not checked; `detect`'s `none` is declared by the collector); or that emulation was ruled out beyond the limits under "CPU execution". The job is not a required check, and a green run is not a review or an approval.
