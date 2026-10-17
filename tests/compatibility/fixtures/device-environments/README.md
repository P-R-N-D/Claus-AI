# Synthetic device environment fixtures

`scenarios.py` builds 15 profile/result pairs from the existing synthetic fixtures.
These are static schema and policy examples. No host, WSL2 installation, VM,
container, cluster, systemd service, GPU or NPU is started. Every stage is
`not-run`; every result is `synthetic: true`, has no timestamp, run or commit,
and remains `incomplete`. Export the bundle for inspection with:

```sh
python -I tests/compatibility/fixtures/device-environments/scenarios.py > /tmp/device-environments.json
python -I scripts/ci/tests/test_device_environments.py --verbose
```

The cases cover bare metal, a VM, Windows on Arm / WSL2 / Linux container,
rootless Podman, VM / rootless Podman / non-root application, a directly
managed host process, host systemd and Quadlet container management, container
PID 1 systemd, Kubernetes, K3s, OpenShift, GPU/NPU passthrough and a CPU-only
container. Service manager facts belong to their own layer. The container
orchestration scope describes its host's unit; it does not describe container
PID 1. cgroup v2 delegation, engine rootlessness, and workload identity remain
independent facts. The systemd container example is unprivileged.

The three cluster examples deliberately use rootful node engines with non-root
workloads and `no_new_privs: true`. Their profiles require rootless engines,
so policy validation must reject them with `policy-engine-rootless`. A normal
rootless engine fixture must also be rejected if mutated to rootful. GPU/NPU
passthrough examples must be rejected with `policy-minimal-devices`: they model
device access without granting approval for it. They leave backend usability
unknown. None has a policy exception or claims hardware execution. All 15 pairs
pass structural and cross-field validation when policy evaluation is disabled;
that check makes no privilege-policy claim.

The `x-device-plugin` extension describes the synthetic request/access route
separately from `container.devices`, engine mode, identity and `no_new_privs`.
Like other `extensions`, it is descriptive metadata, not a validated grant or a
security-policy input. A real device plugin request and grant would need
independent engine/cluster evidence and a scoped approved device exception.
Synthetic records cannot be submitted with `--require-real`.
