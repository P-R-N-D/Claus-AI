"""Build synthetic environment examples; never inspect or execute a platform.

Existing fixtures supply the schema's shared fields. These small transformations
keep the distinctions under test visible without duplicating thousands of lines
of runtime and identity facts. Every returned result is synthetic and incomplete.
"""

import copy
import json
import pathlib


FIXTURES = pathlib.Path(__file__).resolve().parent.parent


def fact(value, source):
    return {"value": value, "provenance": "observed", "evidence": [f"synthetic:{source}"]}


def unknown(reason):
    return {"value": None, "provenance": "unknown", "reason": reason}


def na(reason):
    return {"value": None, "provenance": "not-applicable", "reason": reason}


def template(name):
    return json.loads((FIXTURES / f"{name}.result.json").read_text(encoding="utf-8"))


def manager(name="none", scope=None, source="service-manager scenario"):
    return {"name": fact(name, source), "scope": fact(scope, source) if scope else na("no service manager")}


def orchestration(name="none", scope=None):
    return {
        "name": fact(name, "engine-side orchestration scenario"),
        "version": unknown("version not specified by this scenario") if name != "none" else na("no orchestrator"),
        "scope": fact(scope, "engine-side unit scope") if scope else na("no user/system unit scope"),
    }


def bare_metal():
    result = template("linux-x64-vm")
    host = result["environment"]["layers"][-1]
    host.update(label="Synthetic bare metal", kind=fact("host", "bare-metal scenario"),
                technology=na("the outermost host runs on hardware"), service_manager=manager())
    host["cpu"]["execution"] = fact("native", "bare-metal scenario")
    result["environment"]["layers"] = [host]
    return result


def podman(vm=False):
    result = template("vm-podman-quadlet-systemd")
    layers = result["environment"]["layers"]
    layers[-1]["service_manager"] = manager()
    layers[-1]["container"]["orchestration"] = orchestration()
    if not vm:
        result["environment"]["layers"] = [bare_metal()["environment"]["layers"][0], layers[-1]]
    result["workload"]["isolation"]["cgroup"]["delegated"] = fact(False, "delegation is independent of rootless engine")
    return result


def declared(node):
    """Turn synthetic result facts into profile declarations; do not invent observations."""
    if isinstance(node, dict):
        if "provenance" in node:
            if node["provenance"] in ("observed", "inferred"):
                return {"value": copy.deepcopy(node["value"]), "provenance": "declared"}
            return copy.deepcopy(node)
        return {key: declared(value) for key, value in node.items()}
    if isinstance(node, list):
        return [declared(value) for value in node]
    return node


def pair(name, result):
    """No stage in these examples ran, including detection; approval is never fabricated."""
    result["profile_id"] = f"synthetic-device-{name}"
    result["generated_at"] = None
    result["synthetic"] = True
    result["overall"] = "incomplete"
    result["collector"] = {"name": "synthetic-device-environments", "version": 1}
    result["stages"] = {
        stage: {"status": "not-run", "reason": "static environment fixture; no process ran on this platform"}
        for stage in result["stages"]
    }
    result["extensions"] = {"x-note": "Synthetic environment contract fixture, never a measured platform result."}
    profile = {
        "schema_version": 1, "kind": "profile", "synthetic": True,
        "id": result["profile_id"], "title": f"Synthetic device environment: {name}",
        "support": {"status": "not-assessed", "lane": None,
                    "reference": "docs/DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes"},
        "evidence_sources": [], "required_stages": [],
        "environment": declared(result["environment"]),
        "workload": {"runtimes": declared(result["workload"]["runtimes"])},
        "compute": declared(result["compute"]),
        "policy": {"rules": {"engine-rootless": "required"}, "exceptions": []},
    }
    return profile, result


def scenarios():
    """Map scenario to (profile, result, expected policy error codes)."""
    examples = {}

    def add(name, result, errors=()):
        examples[name] = (*pair(name, result), frozenset(errors))

    add("bare-metal", bare_metal())
    add("vm", template("linux-x64-vm"))
    add("windows-arm-wsl2-container", template("windows-arm64-wsl2-podman"))
    add("rootless-podman", podman())
    add("vm-rootless-podman-nonroot", podman(vm=True))

    direct = bare_metal()
    direct["environment"]["layers"][0]["service_manager"] = manager("systemd", "user", "host systemd user unit")
    add("host-systemd-process", direct)

    for controller in ("systemd", "quadlet"):
        controlled = podman()
        controlled["environment"]["layers"][0]["service_manager"] = manager("systemd", "user", "host systemd user manager")
        controlled["environment"]["layers"][-1]["container"]["orchestration"] = orchestration(controller, "user")
        add(f"host-{controller}-container", controlled)

    inside = podman()
    inside["environment"]["layers"][-1]["service_manager"] = manager("systemd", "system", "container /proc/1/comm")
    inside["workload"]["isolation"]["cgroup"]["delegated"] = fact(True, "container cgroup v2 delegation scenario")
    add("container-pid1-systemd", inside)

    for cluster in ("kubernetes", "k3s", "openshift"):
        result = template("kubernetes-arm64-cuda")
        layer = result["environment"]["layers"][-1]
        layer["service_manager"] = manager()
        layer["container"]["devices"] = fact([], "no device plugin allocation")
        layer["container"]["orchestration"] = orchestration(cluster)
        layer["extensions"] = {"x-device-plugin": {"requested": False, "access": "not-requested"}}
        result["compute"]["backends"] = result["compute"]["backends"][:1]
        result["compute"]["accelerator_inventory"] = unknown("device plugin absence does not establish node hardware absence")
        # Non-root workload and allowPrivilegeEscalation=false do not make the node engine rootless.
        add(cluster, result, ("policy-engine-rootless",))

    for kind, api, device in (("gpu", "cuda", "nvidia-gpu"), ("npu", "qnn", "qualcomm-npu")):
        result = podman()
        layer = result["environment"]["layers"][-1]
        layer["container"]["devices"] = fact([device], "engine-side passed device scenario")
        layer["extensions"] = {"x-device-plugin": {"requested": False, "access": "engine-device-allowlist"}}
        result["compute"]["backends"].append({
            "id": api, "kind": kind, "api": api,
            "available": unknown("device passed through; no usable runtime or kernel execution established"),
            "device": fact(device, "synthetic device class only; no serial number"),
            "version": unknown("driver not probed"), "features": unknown("features not probed"),
        })
        result["compute"]["accelerator_inventory"] = fact(True, "engine-side passed device scenario")
        add(f"{kind}-passthrough", result, ("policy-minimal-devices",))

    cpu_only = podman()
    cpu_only["compute"]["accelerator_inventory"] = fact(False, "synthetic CPU-only container inventory")
    add("cpu-only-container", cpu_only)
    return examples


if __name__ == "__main__":
    # A single JSON bundle on stdout is easy to inspect/archive; it is not a Result document.
    print(json.dumps({name: {"profile": profile, "result": result, "expected_policy_errors": sorted(errors)}
                      for name, (profile, result, errors) in scenarios().items()}, indent=2))
