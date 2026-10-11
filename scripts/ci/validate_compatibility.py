"""Validate compatibility profiles and run results against the model in docs/CI-MATRIX.md.

A document is a profile (a declared lane) or a result (one run on one lane, as
scripts/ci/collect_environment.py writes it). Each is checked in two layers:

1. Structure, from tests/compatibility/schema.json, by a small interpreter of
   the JSON Schema keywords in SUPPORTED_KEYWORDS. A schema that uses any other
   keyword is rejected before any document is read, so the schema cannot ask
   for a check this script does not perform. This is not a general JSON Schema
   implementation; see docs/CI-MATRIX.md for where it is stricter.
2. Rules across fields that JSON Schema cannot express: provenance and
   evidence, layer nesting, OS, CPU and ABI consistency, stage order, CPU
   fallback reported as accelerator success, the privilege policy and its
   exceptions, the link between a result and its profile, and support claims
   against docs/DEPENDENCY-STRATEGY.md. These run only on a document whose
   structure is valid. Each finding has a stable code.

Standard library only. Usage, from the repository root:
    python -I scripts/ci/validate_compatibility.py [--profile PROFILE.json]
        [--dependency-strategy docs/DEPENDENCY-STRATEGY.md] [--require-real]
        [--no-policy] [--summary FILE] DOCUMENT.json...

Exit status: 0 when every document is valid (warnings allowed), 1 when any
document is invalid, 2 on a usage error or a file or schema that cannot be used.
"""

import argparse
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = ROOT / "tests" / "compatibility" / "schema.json"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

# Keywords that check an instance, and keywords that only annotate or hold subschemas.
CHECKING_KEYWORDS = frozenset({
    "type", "enum", "const", "properties", "required", "additionalProperties", "propertyNames",
    "items", "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
    "$ref", "if", "then", "else",
})
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "$comment", "$defs", "title", "description"})
SUPPORTED_KEYWORDS = CHECKING_KEYWORDS | ANNOTATION_KEYWORDS
JSON_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})

STAGES = ("detect", "install", "initialize", "execute", "correctness", "security", "performance")
PREREQUISITES = {
    "detect": (),
    "install": ("detect",),
    "initialize": ("install",),
    "execute": ("initialize",),
    "correctness": ("execute",),
    "security": ("detect",),
    "performance": ("correctness",),
}
RAN = ("passed", "failed")
KNOWN = ("observed", "inferred", "declared")

DEFAULT_POLICY = {
    "app-non-root": "required",
    "engine-rootless": "preferred",
    "no-privileged-container": "required",
    "no-privilege-escalation": "required",
    "minimal-capabilities": "required",
    "minimal-devices": "required",
}
# Which exception scope list an exception for each rule must name; None means no scope.
RULE_SCOPE = {
    "app-non-root": None,
    "engine-rootless": "layers",
    "no-privileged-container": "layers",
    "no-privilege-escalation": "stages",
    "minimal-capabilities": "capabilities",
    "minimal-devices": "devices",
}

ARCH_ALIASES = {
    "amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64", "i386": "x86", "i486": "x86",
    "i586": "x86", "i686": "x86", "ia32": "x86", "armv7l": "armv7", "arm": "armv7", "loong64": "loongarch64",
    "powerpc64le": "ppc64le",
}
ARCH_BITS = {
    "x86_64": 64, "aarch64": 64, "riscv64": 64, "ppc64le": 64, "s390x": 64, "loongarch64": 64,
    "x86": 32, "armv7": 32,
}
# (inner, outer) architecture pairs that run without emulation: 32-bit code on a 64-bit kernel of the same family.
COMPATIBLE = frozenset({("x86", "x86_64"), ("armv7", "aarch64")})
KNOWN_OS_FAMILIES = frozenset({"linux", "windows", "macos", "freebsd"})
KNOWN_TECHNOLOGIES = frozenset({
    "kvm", "qemu", "hyper-v", "vmware", "virtualbox", "xen", "parallels", "apple-virtualization",
    "firecracker", "amazon", "google", "wsl2",
    "docker", "podman", "containerd", "cri-o", "lxc", "systemd-nspawn",
})
KNOWN_BACKEND_APIS = frozenset({
    "cpu", "cuda", "rocm", "oneapi", "level-zero", "opencl", "vulkan-compute", "directcompute",
    "directml", "metal", "mps", "coreml", "qnn", "wasm", "webgpu", "webgl", "webnn",
})
KNOWN_ORCHESTRATION = frozenset({"none", "kubernetes", "k3s", "openshift", "nomad", "compose", "quadlet", "systemd"})
KNOWN_SERVICE_MANAGERS = frozenset({"none", "systemd", "launchd", "windows-scm", "openrc"})
NODE_PLATFORMS = {"linux": "linux", "win32": "windows", "darwin": "macos", "freebsd": "freebsd"}

# C0 and C1 controls, DEL, and the Unicode bidirectional controls.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f؜‎‏‪-‮⁦-⁩]")
SECRET_SHAPES = (
    ("GitHub token", re.compile(r"\bgh[opsur]_[A-Za-z0-9]{30,}")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key ID", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.")),
)


class SchemaError(Exception):
    """The schema uses a keyword or a value this validator cannot apply."""


class Findings:
    def __init__(self):
        self.items = []

    def error(self, code, path, message):
        self.items.append(("error", code, path, message))

    def warning(self, code, path, message):
        self.items.append(("warning", code, path, message))

    @property
    def errors(self):
        return [item for item in self.items if item[0] == "error"]

    def codes(self, level="error"):
        return sorted({code for kind, code, _, _ in self.items if kind == level})


# ---------------------------------------------------------------------------
# Loading


def load_json(path):
    """Read a JSON file strictly: no duplicate keys, NaN or Infinity, and a size limit."""
    data = pathlib.Path(path).read_bytes()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"larger than {MAX_DOCUMENT_BYTES} bytes")
    text = data.decode("utf-8")
    if text.startswith("﻿"):
        raise ValueError("starts with a byte order mark")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def constant(name):
        raise ValueError(f"non-standard number {name}")

    def real(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError(f"number out of range {text}")
        return value

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=real)
    except RecursionError:
        raise ValueError("nested too deeply") from None


# ---------------------------------------------------------------------------
# Structure: a JSON Schema subset


def check_schema(schema):
    """Reject a schema that uses a keyword or value outside the supported subset."""
    defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    stack = [("#", schema)]
    while stack:
        where, node = stack.pop()
        if isinstance(node, bool):
            continue
        if not isinstance(node, dict):
            raise SchemaError(f"{where}: a schema must be an object or a boolean")
        unknown = sorted(set(node) - SUPPORTED_KEYWORDS)
        if unknown:
            raise SchemaError(f"{where}: unsupported keyword(s) {', '.join(unknown)}")
        if "$defs" in node and where != "#":
            raise SchemaError(f"{where}: $defs is supported only at the root")
        if "type" in node:
            types = node["type"] if isinstance(node["type"], list) else [node["type"]]
            if not types or any(t not in JSON_TYPES for t in types):
                raise SchemaError(f"{where}: bad type {node['type']!r}")
        if "required" in node and not (isinstance(node["required"], list) and all(isinstance(r, str) for r in node["required"])):
            raise SchemaError(f"{where}: required must be a list of strings")
        if "enum" in node and not isinstance(node["enum"], list):
            raise SchemaError(f"{where}: enum must be a list")
        for key in ("minItems", "maxItems", "minLength", "maxLength"):
            if key in node and not (type(node[key]) is int and node[key] >= 0):
                raise SchemaError(f"{where}: {key} must be a non-negative integer")
        for key in ("minimum", "maximum"):
            if key in node and not (type(node[key]) in (int, float)):
                raise SchemaError(f"{where}: {key} must be a number")
        if "pattern" in node:
            try:
                re.compile(node["pattern"])
            except (re.error, TypeError) as exc:
                raise SchemaError(f"{where}: bad pattern: {exc}") from None
        if "$ref" in node:
            match = re.fullmatch(r"#/\$defs/([A-Za-z0-9_]+)", str(node["$ref"]))
            if not match or match.group(1) not in defs:
                raise SchemaError(f"{where}: unresolvable $ref {node['$ref']!r}")
        for key in ("properties", "$defs"):
            if key in node:
                if not isinstance(node[key], dict):
                    raise SchemaError(f"{where}: {key} must be an object")
                stack.extend((f"{where}/{key}/{name}", sub) for name, sub in node[key].items())
        for key in ("items", "additionalProperties", "propertyNames", "if", "then", "else"):
            if key in node:
                stack.append((f"{where}/{key}", node[key]))


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return a == b


def pointer(path, key):
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def validate_schema(instance, schema, root, path, findings):
    """Apply `schema` to `instance`, adding one "schema" finding per failed keyword."""
    if schema is True:
        return
    if schema is False:
        findings.error("schema", path or "/", "no value is allowed here")
        return
    if "$ref" in schema:
        validate_schema(instance, root["$defs"][schema["$ref"].rsplit("/", 1)[1]], root, path, findings)
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        actual = json_type(instance)
        if actual not in allowed and not (actual == "integer" and "number" in allowed):
            findings.error("schema", path or "/", f"type is {actual}, expected {' or '.join(allowed)}")
            return
    if "enum" in schema and not any(json_equal(instance, option) for option in schema["enum"]):
        findings.error("schema", path or "/", f"{instance!r} is not one of {schema['enum']}")
    if "const" in schema and not json_equal(instance, schema["const"]):
        findings.error("schema", path or "/", f"{instance!r} is not {schema['const']!r}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            findings.error("schema", path or "/", f"shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            findings.error("schema", path or "/", f"longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            findings.error("schema", path or "/", f"does not match {schema['pattern']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            findings.error("schema", path or "/", f"less than {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            findings.error("schema", path or "/", f"greater than {schema['maximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            findings.error("schema", path or "/", f"fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            findings.error("schema", path or "/", f"more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_schema(item, schema["items"], root, pointer(path, index), findings)
    if isinstance(instance, dict):
        for name in schema.get("required", ()):
            if name not in instance:
                findings.error("schema", path or "/", f"missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if "propertyNames" in schema:
                validate_schema(name, schema["propertyNames"], root, pointer(path, name), findings)
            if name in properties:
                validate_schema(value, properties[name], root, pointer(path, name), findings)
            elif "additionalProperties" in schema:
                if schema["additionalProperties"] is False:
                    findings.error("schema", pointer(path, name), "property is not allowed")
                else:
                    validate_schema(value, schema["additionalProperties"], root, pointer(path, name), findings)
    if "if" in schema:
        probe = Findings()
        validate_schema(instance, schema["if"], root, path, probe)
        branch = "then" if not probe.items else "else"
        if branch in schema:
            validate_schema(instance, schema[branch], root, path, findings)


# ---------------------------------------------------------------------------
# Helpers for rules across fields


def walk(document):
    """Yield (path, value) for every value in the document, without recursion."""
    stack = [("", document)]
    while stack:
        path, value = stack.pop()
        yield path, value
        if isinstance(value, dict):
            stack.extend((pointer(path, key), item) for key, item in value.items())
        elif isinstance(value, list):
            stack.extend((pointer(path, index), item) for index, item in enumerate(value))


def is_fact(value):
    return isinstance(value, dict) and "provenance" in value and "value" in value


def known(fact):
    """The value of a fact whose provenance is observed, inferred or declared, else None."""
    if is_fact(fact) and fact["provenance"] in KNOWN:
        return fact["value"]
    return None


def normalize_arch(name):
    return ARCH_ALIASES.get(name, name) if isinstance(name, str) else None


def arch_family(arch):
    if arch in ("x86_64", "x86"):
        return "x86"
    if arch in ("aarch64", "armv7"):
        return "arm"
    return None


def runs_natively_on(arch, other):
    """Whether code built for `arch` can run without emulation where `other` is native; None if either is not listed."""
    if arch not in ARCH_BITS or other not in ARCH_BITS:
        return None
    return arch == other or (arch, other) in COMPATIBLE


def python_platform_target(platform):
    """Map sysconfig.get_platform() to (OS family, architecture); either may be None."""
    if not isinstance(platform, str):
        return None, None
    if platform.startswith("linux-"):
        return "linux", normalize_arch(platform[len("linux-"):])
    windows = {"win32": "x86", "win-amd64": "x86_64", "win-arm64": "aarch64", "win-arm32": "armv7"}
    if platform in windows:
        return "windows", windows[platform]
    match = re.fullmatch(r"macosx-[0-9.]+-([A-Za-z0-9_]+)", platform)
    if match:
        arch = match.group(1)
        return "macos", None if arch in ("universal2", "intel", "fat", "fat3", "fat64") else normalize_arch(arch)
    match = re.fullmatch(r"freebsd-[^-]+-([A-Za-z0-9_]+)", platform)
    if match:
        return "freebsd", normalize_arch(match.group(1))
    return None, None


def layers_of(document):
    return document["environment"]["layers"]


# ---------------------------------------------------------------------------
# Rules that apply to every document


def check_strings(document, findings):
    for path, value in walk(document):
        texts = [value] if isinstance(value, str) else list(value) if isinstance(value, dict) else []
        for text in texts:
            if CONTROL.search(text):
                findings.error("control-character", path or "/", "contains a control or bidirectional formatting character")
            for label, shape in SECRET_SHAPES:
                if shape.search(text):
                    findings.error("secret-like-value", path or "/", f"looks like a {label}; records are published as CI artifacts")


def check_facts(document, findings):
    profile = document["kind"] == "profile"
    for path, value in walk(document):
        if not is_fact(value):
            continue
        provenance = value["provenance"]
        has_value = value["value"] is not None
        evidence = value.get("evidence") or []
        if provenance in ("observed", "inferred"):
            if not has_value:
                findings.error("fact-provenance", path, f"{provenance} fact has no value; use unknown with a reason")
            if not evidence:
                findings.error("fact-provenance", path, f"{provenance} fact has no evidence")
        elif provenance == "declared":
            if not has_value:
                findings.error("fact-provenance", path, "declared fact has no value; use unknown with a reason")
        else:
            if has_value:
                findings.error("fact-provenance", path, f"{provenance} fact has a value")
            if not value.get("reason"):
                findings.error("fact-provenance", path, f"{provenance} fact has no reason")
        if profile and provenance in ("observed", "inferred"):
            findings.error("profile-provenance", path, "a profile declares; it cannot hold observed or inferred facts")


def evidence_refs(document):
    for path, value in walk(document):
        if isinstance(value, dict) and isinstance(value.get("evidence"), list):
            for index, ref in enumerate(value["evidence"]):
                yield pointer(pointer(path, "evidence"), index), ref


def check_synthetic(document, findings):
    synthetic = document["synthetic"]
    for path, ref in evidence_refs(document):
        if synthetic and not ref.startswith("synthetic:"):
            findings.error("synthetic-marking", path, "a synthetic document must mark all evidence synthetic:")
        if not synthetic and ref.startswith("synthetic:"):
            findings.error("synthetic-marking", path, "synthetic: evidence in a document not marked synthetic")
    if document["kind"] == "profile":
        prefixed = document["id"].startswith("synthetic-")
        if synthetic != prefixed:
            findings.error("synthetic-marking", "/id", "a profile id starts with synthetic- exactly when the profile is synthetic")
    else:
        if synthetic and known(document["run"]["ci"]["system"]) == "github-actions":
            findings.error("synthetic-marking", "/run/ci/system", "a synthetic result cannot claim a GitHub Actions run")
        if synthetic and document["generated_at"] is not None:
            findings.error("synthetic-marking", "/generated_at", "a synthetic result has no collection time")
        if not synthetic and document["generated_at"] is None:
            findings.error("synthetic-marking", "/generated_at", "a result that is not synthetic records when it was collected")


def check_names(document, findings):
    """Warn on identifiers outside the documented lists; reject aliases of known architectures."""
    def arch(path, fact):
        value = known(fact)
        if value is None:
            return
        if value in ARCH_ALIASES:
            findings.error("arch-name", path, f"write {ARCH_ALIASES[value]!r}, not {value!r}")
        elif value not in ARCH_BITS:
            findings.warning("unrecognized-value", path, f"architecture {value!r} is not in the documented list; no consistency rule applies to it")

    def listed(path, fact, names, what):
        value = known(fact) if is_fact(fact) else fact
        if value is not None and value not in names:
            findings.warning("unrecognized-value", path, f"{what} {value!r} is not in the documented list")

    for index, layer in enumerate(layers_of(document)):
        base = f"/environment/layers/{index}"
        arch(f"{base}/cpu/arch", layer["cpu"]["arch"])
        listed(f"{base}/os/family", layer["os"]["family"], KNOWN_OS_FAMILIES, "OS family")
        listed(f"{base}/technology", layer["technology"], KNOWN_TECHNOLOGIES, "technology")
        listed(f"{base}/service_manager/name", layer["service_manager"]["name"], KNOWN_SERVICE_MANAGERS, "service manager")
        if "container" in layer:
            listed(f"{base}/container/orchestration/name", layer["container"]["orchestration"]["name"], KNOWN_ORCHESTRATION, "orchestration")
    runtimes = document["workload"]["runtimes"]
    arch("/workload/runtimes/python/arch", runtimes["python"]["arch"])
    arch("/workload/runtimes/node/arch", runtimes["node"]["arch"])
    for index, backend in enumerate(document["compute"]["backends"]):
        listed(f"/compute/backends/{index}/api", backend["api"], KNOWN_BACKEND_APIS, "backend API")


def check_layers(document, findings):
    layers = layers_of(document)
    for index, layer in enumerate(layers):
        base = f"/environment/layers/{index}"
        kind = known(layer["kind"])
        if kind == "host" and index != 0:
            findings.error("layer-structure", f"{base}/kind", "a host can only be the outermost layer")
        if kind in ("vm", "wsl2", "container") and index == 0:
            findings.error("layer-structure", f"{base}/kind", f"a {kind} needs an enclosing layer; add the host, with unknown facts if they cannot be seen")
        if "container" in layer and kind != "container":
            findings.error("layer-structure", f"{base}/container", "only a layer whose kind is container has container facts")
        if kind == "container" and "container" not in layer:
            findings.error("layer-structure", base, "a container layer records its engine mode, privileged state, devices and orchestration")
        if kind == "container" and known(layer["os"]["kernel"]) is not None:
            findings.error("layer-structure", f"{base}/os/kernel", "a container shares its kernel; record it on the layer that runs the kernel")
        if kind == "host" and known(layer["cpu"]["execution"]) == "emulated":
            findings.error("cpu-contradiction", f"{base}/cpu/execution", "the outermost host cannot be emulated")
        if index == 0:
            continue
        parent = layers[index - 1]
        family, parent_family = known(layer["os"]["family"]), known(parent["os"]["family"])
        if kind == "container" and family and parent_family and family != parent_family:
            findings.error("os-contradiction", f"{base}/os/family", f"a container shares the {parent_family} kernel of the layer below it, so it cannot be {family}")
        if kind == "wsl2" and parent_family and parent_family != "windows":
            findings.error("os-contradiction", f"{base}/kind", f"WSL2 runs on Windows, not {parent_family}")
        if kind == "wsl2" and family and family != "linux":
            findings.error("os-contradiction", f"{base}/os/family", "WSL2 runs a Linux kernel")
        arch, parent_arch = known(layer["cpu"]["arch"]), known(parent["cpu"]["arch"])
        if runs_natively_on(arch, parent_arch) is False and known(layer["cpu"]["execution"]) == "native":
            findings.error("cpu-contradiction", f"{base}/cpu/execution", f"{arch} on {parent_arch} cannot run natively; it is emulated")


def check_workload(document, findings):
    innermost = layers_of(document)[-1]
    family = known(innermost["os"]["family"])
    layer_arch = known(innermost["cpu"]["arch"])
    runtimes = document["workload"]["runtimes"]
    python = runtimes["python"]
    base = "/workload/runtimes/python"

    platform_family, platform_arch = python_platform_target(known(python["platform"]))
    if platform_family and family and platform_family != family:
        findings.error("os-contradiction", f"{base}/platform", f"a {platform_family} Python cannot run on {family} in the innermost layer")
    python_arch = known(python["arch"])
    # On Linux the platform tag follows uname, so a 32-bit build on a 64-bit kernel is not a contradiction.
    if runs_natively_on(python_arch, platform_arch) is False and runs_natively_on(platform_arch, python_arch) is False:
        findings.error("abi-contradiction", f"{base}/arch", f"the platform tag says {platform_arch}, the arch fact says {python_arch}")
    bits = known(python["pointer_bits"])
    if python_arch in ARCH_BITS and bits is not None and bits != ARCH_BITS[python_arch]:
        findings.error("abi-contradiction", f"{base}/pointer_bits", f"{python_arch} has {ARCH_BITS[python_arch]}-bit pointers, not {bits}")

    for name, runtime in (("python", python), ("node", runtimes["node"])):
        arch = known(runtime["arch"])
        if runs_natively_on(arch, layer_arch) is False and known(runtime["execution"]) == "native":
            findings.error("cpu-contradiction", f"/workload/runtimes/{name}/execution", f"a {arch} {name} on a {layer_arch} layer is emulated, not native")

    node_platform = known(runtimes["node"]["platform"])
    if node_platform in NODE_PLATFORMS and family and NODE_PLATFORMS[node_platform] != family:
        findings.error("os-contradiction", "/workload/runtimes/node/platform", f"a {node_platform} Node.js cannot run on {family}")

    if known(python["implementation"]) == "cpython":
        version = known(python["version"])
        match = re.match(r"(\d+)\.(\d+)", version or "")
        if match:
            major, minor = match.groups()
            cache_tag = known(python["cache_tag"])
            if cache_tag is not None and cache_tag != f"cpython-{major}{minor}":
                findings.error("abi-contradiction", f"{base}/cache_tag", f"CPython {major}.{minor} has cache tag cpython-{major}{minor}, not {cache_tag}")
            soabi = re.match(r"(?:cpython-|cp)(\d)(\d+)(t?)", known(python["soabi"]) or "")
            if soabi:
                if soabi.group(1) != major or soabi.group(2) != minor:
                    findings.error("abi-contradiction", f"{base}/soabi", f"the ABI tag is for another version than {version}")
                free_threaded = known(python["free_threaded_build"])
                if free_threaded is not None and free_threaded != bool(soabi.group(3)):
                    findings.error("abi-contradiction", f"{base}/free_threaded_build", "the free-threaded flag disagrees with the ABI tag's t suffix")
        if known(python["free_threaded_build"]) is False and known(python["gil_enabled"]) is False:
            findings.error("abi-contradiction", f"{base}/gil_enabled", "a build without free threading always holds the GIL")


def check_compute(document, findings):
    backends = document["compute"]["backends"]
    ids = [backend["id"] for backend in backends]
    for index, backend in enumerate(backends):
        if ids.count(backend["id"]) > 1:
            findings.error("backend-reference", f"/compute/backends/{index}/id", f"duplicate backend id {backend['id']!r}")
        if (backend["kind"] == "cpu") != (backend["api"] == "cpu"):
            findings.error("backend-reference", f"/compute/backends/{index}", "the cpu backend kind and the cpu API go together")


# ---------------------------------------------------------------------------
# Rules for results


def overall_of(stages):
    statuses = [stages[name]["status"] for name in STAGES]
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses and all(status in ("passed", "not-applicable") for status in statuses):
        return "passed"
    return "incomplete"


def check_stages(document, findings):
    stages = document["stages"]
    backends = {backend["id"]: backend for backend in document["compute"]["backends"]}
    for name in STAGES:
        stage = stages[name]
        base = f"/stages/{name}"
        status = stage["status"]
        if status in RAN and not stage.get("evidence"):
            findings.error("stage-fields", f"{base}/evidence", f"a {status} stage cites the log or artifact that shows it")
        if status != "passed" and not stage.get("reason"):
            findings.error("stage-fields", f"{base}/reason", f"a {status} stage says why")
        if status == "not-run" and stage.get("evidence"):
            findings.error("stage-fields", f"{base}/evidence", "a stage that did not run has no result to cite; it may be failed or blocked instead")
        if status in RAN and "privilege" not in stage:
            findings.error("stage-fields", f"{base}/privilege", f"a {status} stage records whether it escalated privileges")
        for prerequisite in PREREQUISITES[name]:
            before = stages[prerequisite]["status"]
            if status in RAN and before not in ("passed", "not-applicable"):
                findings.error("stage-order", f"{base}/status", f"{name} cannot have {status} while {prerequisite} is {before}; record it as blocked or not-run")
        compute = stage.get("compute")
        if compute is None:
            continue
        requested = backends.get(compute["requested"])
        effective = backends.get(compute["effective"]) if compute["effective"] is not None else None
        if requested is None:
            findings.error("backend-reference", f"{base}/compute/requested", f"no backend {compute['requested']!r} in /compute/backends")
        if compute["effective"] is not None and effective is None:
            findings.error("backend-reference", f"{base}/compute/effective", f"no backend {compute['effective']!r} in /compute/backends")
        if status not in RAN and compute["effective"] is not None:
            findings.error("backend-reference", f"{base}/compute/effective", f"a {status} stage used no backend")
        if status == "passed" and effective is not None and known(effective["available"]) is not True:
            findings.error("backend-reference", f"{base}/compute/effective", "the backend that ran is not recorded as available")
        if status == "passed" and requested is not None and requested["kind"] in ("gpu", "npu"):
            if effective is None or effective["id"] != requested["id"]:
                ran = "nothing" if effective is None else f"{effective['id']} ({effective['kind']})"
                findings.error("accelerator-fallback", f"{base}/compute", f"{requested['kind']} backend {requested['id']} was requested but {ran} ran; a fallback is not an accelerator pass")
    expected = overall_of(stages)
    if document["overall"] != expected:
        findings.error("overall-mismatch", "/overall", f"the stages give {expected}, not {document['overall']}")


def check_identity(document, findings):
    identity = document["workload"]["identity"]
    family = known(layers_of(document)[-1]["os"]["family"])
    uid, superuser = known(identity["uid"]), known(identity["superuser"])
    if family == "linux" and uid is not None and superuser is not None and superuser != (uid == 0):
        findings.error("identity-contradiction", "/workload/identity/superuser", f"uid {uid} {'is' if uid == 0 else 'is not'} root in its user namespace")
    initial, uid_map = known(identity["user_namespace"]["initial"]), known(identity["user_namespace"]["uid_map"])
    # A nested namespace can also map 0 0 4294967295, so only one direction is checked.
    if initial is True and uid_map is not None and uid_map != [[0, 0, 4294967295]]:
        findings.error("identity-contradiction", "/workload/identity/user_namespace/uid_map", "the initial user namespace maps 0 0 4294967295")


def check_run(document, findings):
    ci, revision = document["run"]["ci"], document["run"]["revision"]
    if known(ci["system"]) != "github-actions":
        return
    for name in ("event", "repository", "run_id", "run_attempt", "run_url", "job"):
        if known(ci[name]) is None:
            findings.error("ci-evidence", f"/run/ci/{name}", "a GitHub Actions record names its run")
    repository, run_id, url = known(ci["repository"]), known(ci["run_id"]), known(ci["run_url"])
    if repository and run_id and url and not re.fullmatch(rf"https://[A-Za-z0-9.-]+/{re.escape(repository)}/actions/runs/{re.escape(run_id)}(/attempts/[1-9][0-9]*)?", url):
        findings.error("ci-evidence", "/run/ci/run_url", "the run URL does not point at this repository's run")
    if known(revision["event_sha"]) is None:
        findings.error("ci-evidence", "/run/revision/event_sha", "a GitHub Actions record keeps GITHUB_SHA")
    if known(ci["event"]) in ("pull_request", "pull_request_target"):
        for name in ("pr_head_sha", "pr_base_sha"):
            if known(revision[name]) is None:
                findings.error("ci-evidence", f"/run/revision/{name}", "a pull request run records the head and base it compared")


# ---------------------------------------------------------------------------
# Privilege policy


def effective_policy(profile, findings):
    rules = dict(DEFAULT_POLICY)
    exceptions = []
    if profile is None:
        return rules, exceptions
    for rule, level in profile["policy"].get("rules", {}).items():
        if DEFAULT_POLICY[rule] == "required" and level != "required":
            findings.error("policy-weakened", f"/policy/rules/{rule}", f"{rule} is required by default; record an approved exception instead of weakening it")
        else:
            rules[rule] = level
    seen = set()
    for index, exception in enumerate(profile["policy"].get("exceptions", [])):
        base = f"/policy/exceptions/{index}"
        if exception["id"] in seen:
            findings.error("policy-exception", f"{base}/id", f"duplicate exception id {exception['id']!r}")
        seen.add(exception["id"])
        scope_key = RULE_SCOPE[exception["rule"]]
        for key, values in exception["scope"].items():
            if key != scope_key and values:
                findings.error("policy-exception", f"{base}/scope/{key}", f"an exception to {exception['rule']} cannot be scoped by {key}")
        if scope_key and not exception["scope"].get(scope_key):
            findings.error("policy-exception", f"{base}/scope", f"an exception to {exception['rule']} names the {scope_key} it covers")
        approval = exception["approval"]
        if approval["status"] == "approved" and not (approval.get("reference") and approval.get("date")):
            findings.error("policy-exception", f"{base}/approval", "an approved exception records where and when it was approved")
        exceptions.append(exception)
    return rules, exceptions


def policy_violations(result):
    """Yield (rule, scope key, scope item, path, message, detail) for each item that does not comply.

    Findings that share a rule, a path and a message are reported once, with their details joined.
    """
    identity = result["workload"]["identity"]
    superuser = known(identity["superuser"])
    if superuser is not False:
        state = "runs as the superuser" if superuser else "is not shown to run as a non-root user"
        yield "app-non-root", None, None, "/workload/identity/superuser", f"the workload {state}", None
    for index, layer in enumerate(layers_of(result)):
        if known(layer["kind"]) != "container":
            continue
        container = layer.get("container", {})
        base = f"/environment/layers/{index}/container"
        mode = known(container.get("engine_mode"))
        if mode != "rootless":
            yield "engine-rootless", "layers", index, f"{base}/engine_mode", f"the container engine is {mode or 'not shown to be rootless'}", None
        privileged = known(container.get("privileged"))
        if privileged is not False:
            yield "no-privileged-container", "layers", index, f"{base}/privileged", "the container is privileged" if privileged else "the container is not shown to be unprivileged", None
        devices = known(container.get("devices"))
        if devices is None:
            yield "minimal-devices", "devices", None, f"{base}/devices", "the devices passed to the container are not known", None
        for device in devices or []:
            yield "minimal-devices", "devices", device, f"{base}/devices", "devices are passed to the container", device
    for name in STAGES:
        stage = result["stages"][name]
        if stage["status"] not in RAN:
            continue
        escalation = known(stage.get("privilege", {}).get("escalation"))
        if escalation != "none":
            yield "no-privilege-escalation", "stages", name, f"/stages/{name}/privilege/escalation", f"{name} {'used ' + escalation if escalation else 'is not shown to have used no escalation'}", None
    effective = result["workload"]["isolation"]["capabilities"]["effective"]
    if effective["provenance"] == "not-applicable":
        return
    capabilities = known(effective)
    if capabilities is None:
        yield "minimal-capabilities", "capabilities", None, "/workload/isolation/capabilities/effective", "the effective capabilities are not known", None
    for capability in capabilities or []:
        yield "minimal-capabilities", "capabilities", capability, "/workload/isolation/capabilities/effective", "the workload holds capabilities", capability


def check_policy(result, profile, findings):
    # Errors in the profile's own policy are reported when the profile is validated.
    rules, exceptions = effective_policy(profile, Findings())
    used, groups = set(), {}
    for rule, scope_key, item, path, message, detail in policy_violations(result):
        matching = [
            exception for exception in exceptions
            if exception["rule"] == rule and (scope_key is None or (item is not None and item in exception["scope"].get(scope_key, [])))
        ]
        approved = [exception for exception in matching if exception["approval"]["status"] == "approved"]
        if approved:
            used.update(exception["id"] for exception in approved)
            continue
        unapproved = ", ".join(f"{exception['id']} ({exception['approval']['status']})" for exception in matching)
        details = groups.setdefault((rule, path, message, unapproved), [])
        if detail is not None:
            details.append(detail)
    for (rule, path, message, unapproved), details in groups.items():
        text = f"{message}: {', '.join(details)}" if details else message
        if rules[rule] == "preferred":
            findings.warning(f"policy-{rule}", path, text)
        elif unapproved:
            findings.error("policy-exception-not-approved", path, f"{text}; exception {unapproved} is not approved")
        else:
            findings.error(f"policy-{rule}", path, f"{text}, and no approved exception covers it")
    for exception in exceptions:
        if exception["approval"]["status"] == "approved" and exception["id"] not in used:
            findings.warning("policy-exception-unused", "/policy/exceptions", f"exception {exception['id']} covers nothing in this result")


# ---------------------------------------------------------------------------
# Profiles, and results against their profile


def check_support(profile, strategy_lanes, findings):
    support = profile["support"]
    status, lane = support["status"], support["lane"]
    if status == "checked" and profile["synthetic"]:
        findings.error("support-claim", "/support/status", "a synthetic profile cannot claim a checked lane")
    if status == "not-assessed":
        if lane is not None:
            findings.error("support-claim", "/support/lane", "a lane that docs/DEPENDENCY-STRATEGY.md lists has a status there; use it")
        return
    if lane is None:
        findings.error("support-claim", "/support/lane", f"a {status} claim names its docs/DEPENDENCY-STRATEGY.md lane")
        return
    if strategy_lanes is None:
        if status == "checked":
            findings.warning("support-claim-unverified", "/support/status", "checked claim not compared with docs/DEPENDENCY-STRATEGY.md; pass --dependency-strategy")
        return
    if lane not in strategy_lanes:
        findings.error("support-claim", "/support/lane", f"docs/DEPENDENCY-STRATEGY.md has no lane {lane!r}")
    elif strategy_lanes[lane] != status:
        findings.error("support-claim", "/support/status", f"docs/DEPENDENCY-STRATEGY.md records {lane!r} as {strategy_lanes[lane] or 'a mixed status'}, not {status}")


def check_evidence_sources(profile, findings):
    if profile["synthetic"]:
        return
    for index, source in enumerate(profile["evidence_sources"]):
        workflow = ROOT / source["workflow"]
        base = f"/evidence_sources/{index}"
        if not source["workflow"].startswith(".github/workflows/") or ".." in source["workflow"] or not workflow.is_file():
            findings.error("evidence-source", f"{base}/workflow", f"no workflow file {source['workflow']}")
            continue
        text = workflow.read_text(encoding="utf-8")
        if not re.search(rf"^  {re.escape(source['job'])}:[ \t]*$", text, re.MULTILINE):
            findings.error("evidence-source", f"{base}/job", f"{source['workflow']} has no job {source['job']!r}")


COMPARED_LAYER_FACTS = (("kind",), ("technology",), ("os", "family"), ("os", "name"), ("os", "version"), ("os", "kernel"), ("cpu", "arch"), ("cpu", "execution"), ("service_manager", "name"))
COMPARED_CONTAINER_FACTS = (("engine_mode",), ("privileged",), ("devices",), ("orchestration", "name"))


def dig(mapping, keys):
    for key in keys:
        if not isinstance(mapping, dict) or key not in mapping:
            return None
        mapping = mapping[key]
    return mapping


def check_against_profile(result, profile, findings):
    if result["profile_id"] != profile["id"]:
        findings.error("profile-link", "/profile_id", f"the result names profile {result['profile_id']!r}, not {profile['id']!r}")
    if result["synthetic"] != profile["synthetic"]:
        findings.error("profile-link", "/synthetic", "a real result is checked against a real profile, and a synthetic one against a synthetic one")
    job = known(result["run"]["ci"]["job"])
    if known(result["run"]["ci"]["system"]) == "github-actions" and job not in [source["job"] for source in profile["evidence_sources"]]:
        findings.error("profile-link", "/run/ci/job", f"job {job!r} is not an evidence source of profile {profile['id']}")

    def compare(path, declared_fact, observed_fact):
        declared, observed = known(declared_fact), known(observed_fact)
        if declared is None:
            return
        if observed is None:
            findings.error("profile-unverified", path, f"the profile declares {declared!r}, but the run did not establish it; declare only what the evidence-source job can observe")
        elif not json_equal(declared, observed):
            findings.error("profile-mismatch", path, f"the profile declares {declared!r}, the result has {observed!r}")

    declared_layers, observed_layers = layers_of(profile), layers_of(result)
    if len(declared_layers) != len(observed_layers):
        findings.error("profile-mismatch", "/environment/layers", f"the profile declares {len(declared_layers)} layers, the result has {len(observed_layers)}")
    else:
        for index, (declared, observed) in enumerate(zip(declared_layers, observed_layers)):
            for keys in COMPARED_LAYER_FACTS:
                compare(f"/environment/layers/{index}/{'/'.join(keys)}", dig(declared, keys), dig(observed, keys))
            for keys in COMPARED_CONTAINER_FACTS:
                compare(f"/environment/layers/{index}/container/{'/'.join(keys)}", dig(declared, ("container",) + keys), dig(observed, ("container",) + keys))
    for runtime in ("python", "node"):
        for name, fact in profile["workload"]["runtimes"][runtime].items():
            compare(f"/workload/runtimes/{runtime}/{name}", fact, result["workload"]["runtimes"][runtime][name])
    observed_backends = {backend["id"]: backend for backend in result["compute"]["backends"]}
    for backend in profile["compute"]["backends"]:
        other = observed_backends.get(backend["id"])
        if other is None:
            findings.error("profile-mismatch", "/compute/backends", f"the profile's backend {backend['id']!r} is missing from the result")
        elif (other["kind"], other["api"]) != (backend["kind"], backend["api"]):
            findings.error("profile-mismatch", "/compute/backends", f"backend {backend['id']!r} has another kind or API than the profile declares")
    for name in profile["required_stages"]:
        if result["stages"][name]["status"] != "passed":
            findings.error("required-stage", f"/stages/{name}/status", f"profile {profile['id']} requires {name} to pass; it is {result['stages'][name]['status']}")


def read_strategy_lanes(path):
    """Map each lane in the DEPENDENCY-STRATEGY.md lane table to its backend lock status."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    section = re.search(r"^## Platform and accelerator lanes\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not section:
        raise ValueError("no 'Platform and accelerator lanes' section")
    raw = {}
    for line in section.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("| ") or len(cells) < 3 or cells[0] in ("Lane",) or set(cells[0]) <= set("-: "):
            continue
        raw[cells[0]] = cells[2]
    if not raw:
        raise ValueError("no lanes in the lane table")

    def status(cell, depth=0):
        same = re.match(r"Same as (.+)$", cell)
        if same and depth < 3:
            return status(raw.get(same.group(1).strip(), ""), depth + 1)
        if cell.startswith("Checked"):
            return "checked"
        if cell.startswith("Statically resolvable"):
            return "statically-resolvable"
        if re.match(r"(Not supported|Not binary-installable|Incomplete)\b", cell):
            return "not-supported"
        return None

    return {lane: status(cell) for lane, cell in raw.items()}


# ---------------------------------------------------------------------------
# Entry points


def validate_document(document, schema, profile=None, strategy_lanes=None, evaluate_policy=True, require_real=False):
    """Return the Findings for one parsed document."""
    findings = Findings()
    validate_schema(document, schema, schema, "", findings)
    if findings.errors:
        findings.warning("semantic-skipped", "/", "rules across fields were not checked because the structure is invalid")
        return findings
    if require_real and document["synthetic"]:
        findings.error("synthetic-marking", "/synthetic", "a synthetic document where a measured one is required")
    check_strings(document, findings)
    check_facts(document, findings)
    check_synthetic(document, findings)
    check_names(document, findings)
    check_layers(document, findings)
    check_workload(document, findings)
    check_compute(document, findings)
    if document["kind"] == "profile":
        check_support(document, strategy_lanes, findings)
        check_evidence_sources(document, findings)
        effective_policy(document, findings)
        return findings
    check_stages(document, findings)
    check_identity(document, findings)
    check_run(document, findings)
    if profile is not None:
        check_against_profile(document, profile, findings)
    elif document["profile_id"] is not None:
        findings.warning("profile-unchecked", "/profile_id", "the result names a profile that was not given; the default policy applies")
    if evaluate_policy:
        check_policy(document, profile, findings)
    return findings


def load_schema(path=DEFAULT_SCHEMA):
    schema = load_json(path)
    check_schema(schema)
    return schema


def markdown_cell(text):
    return CONTROL.sub(" ", str(text)).replace("|", "\\|").replace("`", "'")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("documents", nargs="+", type=pathlib.Path, help="profile or result JSON files")
    parser.add_argument("--schema", type=pathlib.Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", type=pathlib.Path, help="profile that every result document is checked against")
    parser.add_argument("--dependency-strategy", type=pathlib.Path, help="compare profile support claims with this file's lane table")
    parser.add_argument("--require-real", action="store_true", help="reject synthetic documents")
    parser.add_argument("--no-policy", action="store_true", help="skip the privilege policy (for records of machines it is not meant for)")
    parser.add_argument("--summary", type=pathlib.Path, help="append a Markdown summary to this file")
    args = parser.parse_args(argv)

    try:
        schema = load_schema(args.schema)
    except (OSError, ValueError, SchemaError) as exc:
        print(f"error: cannot use schema {args.schema}: {exc}", file=sys.stderr)
        return 2
    strategy_lanes = None
    if args.dependency_strategy:
        try:
            strategy_lanes = read_strategy_lanes(args.dependency_strategy)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read lanes from {args.dependency_strategy}: {exc}", file=sys.stderr)
            return 2
    profile = None
    if args.profile:
        try:
            profile = load_json(args.profile)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read profile {args.profile}: {exc}", file=sys.stderr)
            return 2
        profile_findings = validate_document(profile, schema, strategy_lanes=strategy_lanes) if isinstance(profile, dict) else None
        if not isinstance(profile, dict) or profile.get("kind") != "profile" or profile_findings.errors:
            print(f"error: {args.profile} is not a valid profile", file=sys.stderr)
            for level, code, path, message in (profile_findings.items if profile_findings else []):
                print(f"  {level.upper()} {code} {path}: {message}", file=sys.stderr)
            return 2

    invalid = 0
    lines = ["### Compatibility record validation", "", "| Document | Kind | Result | Errors | Warnings |", "|---|---|---|---|---|"]
    details = []
    for path in args.documents:
        try:
            document = load_json(path)
        except OSError as exc:
            print(f"error: cannot read {path}: {exc.strerror or exc}", file=sys.stderr)
            return 2
        except (ValueError, UnicodeDecodeError) as exc:
            findings = Findings()
            findings.error("json", "/", f"not strict JSON: {exc}")
            document = None
        else:
            findings = validate_document(document, schema, profile, strategy_lanes, not args.no_policy, args.require_real)
        errors = findings.errors
        warnings = [item for item in findings.items if item[0] == "warning"]
        verdict = "invalid" if errors else "valid"
        invalid += bool(errors)
        kind = document.get("kind") if isinstance(document, dict) else "?"
        print(f"{path}: {verdict} {kind} ({len(errors)} errors, {len(warnings)} warnings)")
        for level, code, where, message in findings.items:
            print(f"  {level.upper()} {code} {where}: {message}")
            details.append(f"- {level} `{markdown_cell(code)}` {markdown_cell(path.name)} {markdown_cell(where)}: {markdown_cell(message)}")
        lines.append(f"| {markdown_cell(path.name)} | {markdown_cell(kind)} | {verdict} | {len(errors)} | {len(warnings)} |")

    if args.summary:
        lines += [""] + details[:200] + [
            "",
            "A valid record shows that the schema, the validator and the probe worked on this runner for this commit. It is not a support claim for this or any other platform; support status is recorded only in docs/DEPENDENCY-STRATEGY.md.",
            "",
        ]
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
