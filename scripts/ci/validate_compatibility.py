"""Validate compatibility profiles and run results against the model in docs/CI-MATRIX.md.

A document is a profile (a declared lane) or a result (one run on one lane, as
scripts/ci/collect_environment.py writes it). Each is checked in two layers:

1. Structure, from tests/compatibility/schema.json, by a small interpreter of
   the JSON Schema keywords in SUPPORTED_KEYWORDS. A schema that uses any other
   keyword is rejected before any document is read, so the schema cannot ask
   for a check this script does not perform. This is not a general JSON Schema
   implementation; see docs/CI-MATRIX.md for where its result can differ.
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
import datetime
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
DIALECT = "https://json-schema.org/draft/2020-12/schema"
# Pattern syntax that Python's re and ECMA-262 read differently: an escaped letter other than t, n, r, f,
# v, x or u (shorthand classes and anchors follow Unicode or another definition, and \a, \N and \U exist
# in Python only), a \u escape of a surrogate (a code point in Python, a UTF-16 code unit in ECMA-262),
# (? groups other than (?:, {,n}, possessive quantifiers, and a class that opens with ] or ^]. Only an
# unescaped one counts; inside a character class some of these are portable, and are rejected anyway.
NON_PORTABLE_PATTERN = re.compile(r"(?<!\\)(?:\\\\)*(?:\\(?![tnrfvxu])[A-Za-z]|\\u[dD][89a-fA-F]|\(\?(?!:)|\{,|[*+?}]\+|\[\^?\])")
# Keywords whose subschema applies to the same value, so a $ref cycle through them never ends.
SAME_VALUE_KEYWORDS = ("if", "then", "else")

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
# Every workload is detected, installed, initialized, run and checked, so only these stages can be not-applicable.
OPTIONAL_STAGES = ("security", "performance")
# Stages that run the workload itself, and so run on a profile's accelerator backend.
WORKLOAD_STAGES = ("execute", "correctness", "performance")
RAN = ("passed", "failed")
# Evidence kinds that show what a stage did in a real run; detect may also cite the collector itself.
STAGE_EVIDENCE = ("log:", "artifact:")
KNOWN = ("observed", "inferred", "declared")
# Provenances that show something about a run. A declared value in a result is a statement, so it
# can be checked for contradictions but cannot show compliance with the policy or with a profile.
MEASURED = ("observed", "inferred")
POSIX_FAMILIES = ("linux", "macos", "freebsd")

DEFAULT_POLICY = {
    "app-non-root": "required",
    "engine-rootless": "preferred",
    "no-privileged-container": "required",
    "no-privilege-escalation": "required",
    "minimal-capabilities": "required",
    "minimal-devices": "required",
}
# The exception scope lists an exception to each rule must name. A violation is covered only when every
# one of its values is in the matching list, so an escalation exception names both the stages and the methods.
RULE_SCOPE = {
    "app-non-root": (),
    "engine-rootless": ("layers",),
    "no-privileged-container": ("layers",),
    "no-privilege-escalation": ("stages", "escalations"),
    "minimal-capabilities": ("capabilities",),
    "minimal-devices": ("devices",),
}

# What each row of the docs/DEPENDENCY-STRATEGY.md lane table describes: the innermost layer's OS family,
# the architecture and C library the Python is built for, and whether the lane runs inside WSL2. None
# leaves that axis open. A lane the table lists but this map does not cannot carry a support status.
LANE_TARGETS = {
    "Linux x86-64 glibc": {"os": "linux", "arch": "x86_64", "libc": "glibc", "wsl2": None},
    "Linux aarch64 glibc": {"os": "linux", "arch": "aarch64", "libc": "glibc", "wsl2": None},
    "Windows on Arm, native (`win_arm64`)": {"os": "windows", "arch": "aarch64", "libc": None, "wsl2": False},
    "Windows on Arm with WSL2 (Ubuntu arm64)": {"os": "linux", "arch": "aarch64", "libc": "glibc", "wsl2": True},
    "Windows x64, native": {"os": "windows", "arch": "x86_64", "libc": None, "wsl2": False},
    "macOS Apple Silicon": {"os": "macos", "arch": "aarch64", "libc": None, "wsl2": False},
    "macOS Intel": {"os": "macos", "arch": "x86_64", "libc": None, "wsl2": False},
    "Linux musl (Alpine)": {"os": "linux", "arch": None, "libc": "musl", "wsl2": None},
}
# The lane table's backend lock column describes backend/locks/cp312-linux-x86_64.txt: CPython 3.12 with the GIL.
LOCK_INTERPRETER = ("cpython", "3.12", False)

ARCH_ALIASES = {
    "amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64", "i386": "x86", "i486": "x86",
    "i586": "x86", "i686": "x86", "ia32": "x86", "armv7l": "armv7", "armv8l": "armv7", "arm": "armv7",
    "loong64": "loongarch64", "powerpc64le": "ppc64le",
}
ARCH_BITS = {
    "x86_64": 64, "aarch64": 64, "riscv64": 64, "ppc64le": 64, "s390x": 64, "loongarch64": 64,
    "x86": 32, "armv7": 32,
}
# (inner, outer) architecture pairs that run without emulation: 32-bit code on a 64-bit kernel of the same family.
COMPATIBLE = frozenset({("x86", "x86_64"), ("armv7", "aarch64")})
KNOWN_OS_FAMILIES = frozenset({"linux", "windows", "macos", "freebsd"})
# The layer kind each documented technology makes.
TECHNOLOGY_KINDS = {
    **dict.fromkeys(("kvm", "qemu", "hyper-v", "vmware", "virtualbox", "xen", "parallels", "apple-virtualization",
                     "firecracker", "amazon", "google"), "vm"),
    "wsl2": "wsl2",
    **dict.fromkeys(("docker", "podman", "containerd", "cri-o", "lxc", "systemd-nspawn", "apptainer"), "container"),
}
KNOWN_TECHNOLOGIES = frozenset(TECHNOLOGY_KINDS)
KNOWN_BACKEND_APIS = frozenset({
    "cpu", "cuda", "rocm", "oneapi", "level-zero", "opencl", "vulkan-compute", "directcompute",
    "directml", "metal", "mps", "coreml", "qnn", "wasm", "webgpu", "webgl", "webnn",
})
KNOWN_ORCHESTRATION = frozenset({"none", "kubernetes", "k3s", "openshift", "nomad", "compose", "quadlet", "systemd"})
KNOWN_SERVICE_MANAGERS = frozenset({"none", "systemd", "launchd", "windows-scm", "openrc"})
NODE_PLATFORMS = {"linux": "linux", "win32": "windows", "darwin": "macos", "freebsd": "freebsd"}

# C0 and C1 controls, DEL, the Unicode line and paragraph separators and bidirectional controls, and lone surrogates.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069\ud800-\udfff]")
# No shape starts with \b: an escape written just before a value (repr's \n, a JSON Pointer's ~1, JSON's \u200e)
# ends in a word character and would hide it.
SECRET_SHAPES = (
    ("GitHub token", re.compile(r"gh[opsur]_[A-Za-z0-9]{30,}")),
    ("GitHub token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key ID", re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,}")),
    # A JSON Web Token: "eyJ", at least 8 more base64url characters and ".", twice. The run that holds the first
    # "eyJ" is matched from its start, so each run is tested once and a long run does not make the search quadratic.
    ("JSON Web Token", re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*?eyJ[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]{8,}\.")),
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
    with open(path, "rb") as handle:
        data = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"larger than {MAX_DOCUMENT_BYTES} bytes")
    text = data.decode("utf-8")
    if text.startswith("\ufeff"):
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

    def integer(text):
        real(text)
        return int(text)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=real, parse_int=integer)
    except RecursionError:
        raise ValueError("nested too deeply") from None


# ---------------------------------------------------------------------------
# Structure: a JSON Schema subset


def check_schema(schema):
    """Reject a schema that uses a keyword or value outside the supported subset."""
    if not isinstance(schema, dict):
        raise SchemaError("#: the root schema must be an object")
    defs = schema.get("$defs", {})
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
        for key in ("$defs", "$schema", "$id"):
            if key in node and where != "#":
                raise SchemaError(f"{where}: {key} is supported only at the root")
        if node.get("$schema", DIALECT) != DIALECT:
            raise SchemaError(f"{where}: only the {DIALECT} dialect is supported")
        for key in ("$id", "$comment", "title", "description"):
            if key in node and not isinstance(node[key], str):
                raise SchemaError(f"{where}: {key} must be a string")
        if "type" in node:
            types = node["type"] if isinstance(node["type"], list) else [node["type"]]
            if not types or any(not isinstance(t, str) or t not in JSON_TYPES for t in types):
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
            if NON_PORTABLE_PATTERN.search(node["pattern"]):
                raise SchemaError(f"{where}: pattern uses syntax that Python and ECMA-262 read differently")
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
    check_ref_cycles(schema, defs)


def check_ref_cycles(schema, defs):
    """Reject $ref chains that return to a definition without moving to another value, which would never end."""
    def same_value_refs(node):
        """Definitions that `node` applies to the value it is applied to."""
        stack, refs = [node], set()
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "$ref" in node:
                    refs.add(node["$ref"].rsplit("/", 1)[1])
                stack.extend(node[key] for key in SAME_VALUE_KEYWORDS if key in node)
        return refs

    edges = {name: same_value_refs(sub) for name, sub in defs.items()}
    for start in sorted(same_value_refs(schema) | set(defs)):
        path, seen = [start], set()
        while path:
            name = path.pop()
            if name in seen:
                continue
            seen.add(name)
            if start in edges.get(name, ()):
                raise SchemaError(f"#/$defs/{start}: $ref leads back to itself without moving to another value")
            path.extend(edges.get(name, ()))


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


def calendar_date(text):
    """Whether a YYYY-MM-DD string (the shape the schema checks) names a real day."""
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def calendar_time(text):
    """Whether a YYYY-MM-DDTHH:MM:SSZ string (the shape the schema checks) is a valid date and time, with seconds 00 to 59."""
    try:
        datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


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


def walk(document, extensions=True):
    """Yield (path, value) for every value in the document, without recursion; optionally skip extension contents."""
    stack = [("", document)]
    while stack:
        path, value = stack.pop()
        yield path, value
        if isinstance(value, dict):
            stack.extend((pointer(path, key), item) for key, item in value.items() if extensions or key != "extensions")
        elif isinstance(value, list):
            stack.extend((pointer(path, index), item) for index, item in enumerate(value))


def is_fact(value):
    return isinstance(value, dict) and "provenance" in value and "value" in value


def known(fact):
    """The value of a fact whose provenance is observed, inferred or declared, else None."""
    if is_fact(fact) and fact["provenance"] in KNOWN:
        return fact["value"]
    return None


def measured(fact):
    """The value of a fact whose provenance is observed or inferred, else None."""
    if is_fact(fact) and fact["provenance"] in MEASURED:
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


def translated_by(arch, enclosing):
    """Why code built for `arch` cannot run natively inside the `enclosing` layers (outermost first), or None."""
    for index, layer in enumerate(enclosing):
        if known(layer["cpu"]["execution"]) == "emulated":
            return f"layer {index} is emulated, so everything inside it is translated"
        outer = known(layer["cpu"]["arch"])
        if runs_natively_on(arch, outer) is False:
            return f"{arch} code on the {outer} of layer {index} is emulated"
    return None


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
    match = re.fullmatch(r"freebsd-.+-([A-Za-z0-9_]+)", platform)
    if match:
        return "freebsd", normalize_arch(match.group(1))
    return None, None


def soabi_target(soabi):
    """Map a CPython SOABI to (OS family, architecture); either may be None."""
    if not isinstance(soabi, str):
        return None, None
    match = re.fullmatch(r"cpython-[0-9]+t?d?-([a-z0-9_]+)-linux-[a-z0-9]+", soabi)
    if match:
        return "linux", normalize_arch(match.group(1))
    if re.fullmatch(r"cpython-[0-9]+t?d?-darwin", soabi):
        return "macos", None
    match = re.fullmatch(r"cp[0-9]+t?d?-(win32|win_amd64|win_arm64)", soabi)
    if match:
        return "windows", {"win32": "x86", "win_amd64": "x86_64", "win_arm64": "aarch64"}[match.group(1)]
    return None, None


def libc_of_fact(libc):
    """The C library a libc fact names: glibc or musl when it starts with that name in any ASCII letter case and
    no letter follows, otherwise the fact as written; None for no value."""
    if not isinstance(libc, str):
        return None
    name = re.match(r"(?ai:glibc|musl)", libc)
    # str.isalpha is true for letters only, so a digit, a superscript or a Roman numeral may follow the name.
    return name.group(0).lower() if name and not libc[name.end():name.end() + 1].isalpha() else libc


def libc_of_tag(soabi):
    """The C library a Linux ABI tag names: glibc for gnu (gnueabihf, gnux32), musl (musleabihf), or the tag's
    own word, such as android; None for a tag that names none."""
    # The word and the digits after it cannot overlap, so a long tag is searched in linear time.
    match = re.search(r"-linux-([a-z]+)(?:[0-9][a-z0-9]*)?$", soabi) if isinstance(soabi, str) else None
    if not match:
        return None
    word = match.group(1)
    return "glibc" if word.startswith("gnu") else "musl" if word.startswith("musl") else word


def libc_family(python):
    """(the C library, the path of the fact it came from), from a Python's libc fact or else its SOABI; (None, None) if neither shows it.

    A C library other than glibc or musl is returned as named, so it contradicts a glibc or musl lane.
    """
    base = "/workload/runtimes/python"
    libc = libc_of_fact(known(python["libc"]))
    if libc:
        return libc, f"{base}/libc"
    libc = libc_of_tag(known(python["soabi"]))
    return (libc, f"{base}/soabi") if libc else (None, None)


def layers_of(document):
    return document["environment"]["layers"]


# ---------------------------------------------------------------------------
# Rules that apply to every document


def check_strings(document, findings):
    for path, value in walk(document):
        texts = [value] if isinstance(value, str) else list(value) if isinstance(value, dict) else []
        for text in texts:
            if CONTROL.search(text):
                findings.error("control-character", path or "/", "contains a control character, a bidirectional formatting character or a lone surrogate")
            for label, shape in SECRET_SHAPES:
                if shape.search(text):
                    findings.error("secret-like-value", path or "/", f"looks like a {label}; records are published as CI artifacts")


def check_facts(document, findings):
    profile = document["kind"] == "profile"
    for path, value in walk(document, extensions=False):
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
    for path, value in walk(document, extensions=False):
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
        if synthetic:
            for keys, fact in facts_of(document["run"]):
                if known(fact) is not None:
                    findings.error("synthetic-marking", f"/run/{'/'.join(keys)}", "a synthetic result names no run, commit or CI system")
        if synthetic and document["generated_at"] is not None:
            findings.error("synthetic-marking", "/generated_at", "a synthetic result has no collection time")
        if not synthetic and document["generated_at"] is None:
            findings.error("synthetic-marking", "/generated_at", "a result that is not synthetic records when it was collected")
        if document["generated_at"] is not None and not calendar_time(document["generated_at"]):
            findings.error("synthetic-marking", "/generated_at", "the collection time is not a valid date and time (seconds run from 00 to 59)")


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
        technology = known(layer["technology"])
        if kind == "host" and technology is not None:
            findings.error("layer-structure", f"{base}/technology", f"the outermost host runs on hardware and names no technology, not {technology!r}")
        elif kind == "wsl2" and technology is not None and technology != "wsl2":
            findings.error("layer-structure", f"{base}/technology", f"a WSL2 layer is made by wsl2, not {technology!r}")
        elif kind is not None and technology in TECHNOLOGY_KINDS and TECHNOLOGY_KINDS[technology] != kind:
            findings.error("layer-structure", f"{base}/technology", f"{technology!r} makes a {TECHNOLOGY_KINDS[technology]} layer, not a {kind}")
        if kind in ("vm", "wsl2", "container") and layer["technology"]["provenance"] == "not-applicable":
            findings.error("layer-structure", f"{base}/technology", f"a {kind} is made by a hypervisor or engine; record it as unknown when it cannot be seen")
        if kind == "container" and "container" not in layer:
            findings.error("layer-structure", base, "a container layer records its engine mode, privileged state, devices and orchestration")
        if kind == "container" and known(layer["os"]["kernel"]) is not None:
            findings.error("layer-structure", f"{base}/os/kernel", "a container shares its kernel; record it on the layer that runs the kernel")
        if kind == "host" and known(layer["cpu"]["execution"]) == "emulated":
            findings.error("cpu-contradiction", f"{base}/cpu/execution", "the outermost host cannot be emulated")
        if index == 0:
            continue
        if known(layer["cpu"]["execution"]) == "native":
            conflict = translated_by(known(layer["cpu"]["arch"]), layers[:index])
            if conflict:
                findings.error("cpu-contradiction", f"{base}/cpu/execution", f"recorded as native, but {conflict}")
        parent = layers[index - 1]
        family, parent_family = known(layer["os"]["family"]), known(parent["os"]["family"])
        if kind == "container" and family and parent_family and family != parent_family:
            findings.error("os-contradiction", f"{base}/os/family", f"a container shares the {parent_family} kernel of the layer below it, so it cannot be {family}")
        if kind == "wsl2" and parent_family and parent_family != "windows":
            findings.error("os-contradiction", f"{base}/kind", f"WSL2 runs on Windows, not {parent_family}")
        if kind == "wsl2" and family and family != "linux":
            findings.error("os-contradiction", f"{base}/os/family", "WSL2 runs a Linux kernel")


def check_workload(document, findings):
    layers = layers_of(document)
    family = known(layers[-1]["os"]["family"])
    runtimes = document["workload"]["runtimes"]
    python = runtimes["python"]
    base = "/workload/runtimes/python"

    platform_family, platform_arch = python_platform_target(known(python["platform"]))
    if platform_family and family and platform_family != family:
        findings.error("os-contradiction", f"{base}/platform", f"a {platform_family} Python cannot run on {family} in the innermost layer")

    def platform_disagrees(build_arch):
        if platform_family == "linux":
            # On Linux the platform tag follows uname, so a 32-bit build on a 64-bit kernel is not a contradiction.
            return runs_natively_on(build_arch, platform_arch) is False and runs_natively_on(platform_arch, build_arch) is False
        # Windows and macOS tags name the build itself.
        return platform_arch in ARCH_BITS and build_arch in ARCH_BITS and platform_arch != build_arch

    python_arch = known(python["arch"])
    if platform_disagrees(python_arch):
        findings.error("abi-contradiction", f"{base}/arch", f"the platform tag says {platform_arch}, the arch fact says {python_arch}")
    bits = known(python["pointer_bits"])
    if python_arch in ARCH_BITS and bits is not None and bits != ARCH_BITS[python_arch]:
        findings.error("abi-contradiction", f"{base}/pointer_bits", f"{python_arch} has {ARCH_BITS[python_arch]}-bit pointers, not {bits}")

    for name, runtime in (("python", python), ("node", runtimes["node"])):
        if known(runtime["execution"]) == "native":
            conflict = translated_by(known(runtime["arch"]), layers)
            if conflict:
                findings.error("cpu-contradiction", f"/workload/runtimes/{name}/execution", f"recorded as native, but {conflict}")

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
                findings.error("abi-contradiction", f"{base}/cache_tag", f"CPython {major}.{minor} has cache tag cpython-{major}{minor}, not {cache_tag!r}")
            soabi = re.match(r"(?:cpython-|cp)(\d)(\d+)(t?)", known(python["soabi"]) or "")
            if soabi:
                if soabi.group(1) != major or soabi.group(2) != minor:
                    findings.error("abi-contradiction", f"{base}/soabi", f"the ABI tag is for another version than {version!r}")
                free_threaded = known(python["free_threaded_build"])
                if free_threaded is not None and free_threaded != bool(soabi.group(3)):
                    findings.error("abi-contradiction", f"{base}/free_threaded_build", "the free-threaded flag disagrees with the ABI tag's t suffix")
        # The platform part of the ABI tag is checked even when the version is unknown.
        soabi_family, soabi_arch = soabi_target(known(python["soabi"]))
        if known(python["soabi"]) is not None and soabi_family is None:
            findings.warning("unrecognized-value", f"{base}/soabi", f"the CPython ABI tag {known(python['soabi'])!r} has no form this validator reads, so its OS and architecture are not checked")
        if soabi_family and family and soabi_family != family:
            findings.error("os-contradiction", f"{base}/soabi", f"the ABI tag is for {soabi_family}, the innermost layer is {family}")
        if soabi_arch in ARCH_BITS and python_arch in ARCH_BITS and soabi_arch != python_arch:
            findings.error("abi-contradiction", f"{base}/soabi", f"the ABI tag is for {soabi_arch}, the arch fact says {python_arch}")
        # The ABI tag, like the platform tag, is compared with the innermost layer's OS family and with the arch fact. When
        # the family is unknown, or the arch fact is unknown or not a listed name, the two tags are compared with each other.
        if family is None and platform_family and soabi_family and platform_family != soabi_family:
            findings.error("os-contradiction", f"{base}/soabi", f"the ABI tag is for {soabi_family}, the platform tag is for {platform_family}")
        elif python_arch not in ARCH_BITS and platform_family == soabi_family and platform_disagrees(soabi_arch):
            findings.error("abi-contradiction", f"{base}/soabi", f"the ABI tag is for {soabi_arch}, the platform tag says {platform_arch}")
        tag_libc, fact_libc = libc_of_tag(known(python["soabi"])), libc_of_fact(known(python["libc"]))
        # Two names neither of which is glibc or musl (android and bionic) are not compared.
        if tag_libc and fact_libc and tag_libc != fact_libc and {tag_libc, fact_libc} & {"glibc", "musl"}:
            names = fact_libc if fact_libc in ("glibc", "musl") else "a C library other than glibc or musl (a fact names glibc or musl only when it starts with that name, in any ASCII letter case, with no letter after it)"
            findings.error("abi-contradiction", f"{base}/libc", f"the ABI tag is for {tag_libc}, the libc fact {known(python['libc'])!r} names {names}")
        if known(python["free_threaded_build"]) is False and known(python["gil_enabled"]) is False:
            findings.error("abi-contradiction", f"{base}/gil_enabled", "a build without free threading always holds the GIL")
    # glibc and musl are C libraries for Linux, whatever the ABI tag names.
    libc = libc_of_fact(known(python["libc"]))
    elsewhere = {family, platform_family, soabi_target(known(python["soabi"]))[0]} & {"windows", "macos", "freebsd"}
    if libc in ("glibc", "musl") and elsewhere:
        findings.error("abi-contradiction", f"{base}/libc", f"{libc} is a C library for Linux, and this Python is for {' and '.join(sorted(elsewhere))}")


def check_compute(document, findings):
    backends = document["compute"]["backends"]
    ids = [backend["id"] for backend in backends]
    for index, backend in enumerate(backends):
        if ids.count(backend["id"]) > 1:
            findings.error("backend-reference", f"/compute/backends/{index}/id", f"duplicate backend id {backend['id']!r}")
        if backend["api"] == "cpu" and backend["kind"] != "cpu":
            findings.error("backend-reference", f"/compute/backends/{index}", "the cpu API runs on the cpu backend kind")


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
        elif status in RAN and not document["synthetic"]:
            kinds = STAGE_EVIDENCE + (("tool:",) if name == "detect" else ())
            if not any(ref.startswith(kinds) for ref in stage["evidence"]):
                findings.error("stage-fields", f"{base}/evidence", f"a {status} stage of a real run cites a {' or '.join(kinds)} reference")
        if status != "passed" and not stage.get("reason"):
            findings.error("stage-fields", f"{base}/reason", f"a {status} stage says why")
        if status == "not-run" and stage.get("evidence"):
            findings.error("stage-fields", f"{base}/evidence", "a stage that did not run has no result to cite; it may be failed or blocked instead")
        if status == "not-applicable" and name not in OPTIONAL_STAGES:
            findings.error("stage-fields", f"{base}/status", f"every workload has a {name} stage; record it as not-run or blocked with a reason")
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
    if document["probe_errors"] and stages["detect"]["status"] == "passed":
        findings.error("stage-fields", "/stages/detect/status", "detect cannot pass while probe_errors lists probes that failed")
    expected = overall_of(stages)
    if document["overall"] != expected:
        findings.error("overall-mismatch", "/overall", f"the stages give {expected}, not {document['overall']}")


def check_identity(document, findings):
    identity = document["workload"]["identity"]
    family = known(layers_of(document)[-1]["os"]["family"])
    uid, superuser = known(identity["uid"]), known(identity["superuser"])
    # On a POSIX system the superuser is effective UID 0; Windows names it by its token, so there the two are not compared.
    if family in POSIX_FAMILIES and uid is not None and superuser is not None and superuser != (uid == 0):
        findings.error("identity-contradiction", "/workload/identity/superuser", f"uid {uid} {'is' if uid == 0 else 'is not'} the superuser (in its user namespace, on Linux)")
    initial, uid_map = known(identity["user_namespace"]["initial"]), known(identity["user_namespace"]["uid_map"])
    # A nested namespace can also map 0 0 4294967295, so only one direction is checked.
    if initial is True and uid_map is not None and uid_map != [[0, 0, 4294967295]]:
        findings.error("identity-contradiction", "/workload/identity/user_namespace/uid_map", "the initial user namespace maps 0 0 4294967295")


def check_run(document, findings):
    ci, revision = document["run"]["ci"], document["run"]["revision"]
    if known(ci["system"]) != "github-actions":
        return
    # The run and the commits are evidence only when measured; a declared value is a claim the record cannot back.
    for name in ("event", "repository", "run_id", "run_attempt", "run_url", "job"):
        if measured(ci[name]) is None:
            findings.error("ci-evidence", f"/run/ci/{name}", "a GitHub Actions record names its run, as observed or inferred")
    if measured(ci["run_attempt"]) == 0:
        findings.error("ci-evidence", "/run/ci/run_attempt", "GitHub numbers a run's attempts from 1")
    repository, run_id, url = measured(ci["repository"]), measured(ci["run_id"]), measured(ci["run_url"])
    if repository and run_id and url and not re.fullmatch(rf"https://[A-Za-z0-9.-]+/{re.escape(repository)}/actions/runs/{re.escape(run_id)}(/attempts/[1-9][0-9]*)?", url):
        findings.error("ci-evidence", "/run/ci/run_url", "the run URL does not point at this repository's run")
    if measured(revision["tested_sha"]) is None:
        findings.error("ci-evidence", "/run/revision/tested_sha", "a GitHub Actions record names the commit its checkout holds, as observed or inferred")
    if measured(revision["event_sha"]) is None:
        findings.error("ci-evidence", "/run/revision/event_sha", "a GitHub Actions record keeps GITHUB_SHA, as observed or inferred")
    if measured(ci["event"]) in ("pull_request", "pull_request_target"):
        for name in ("pr_head_sha", "pr_base_sha"):
            if measured(revision[name]) is None:
                findings.error("ci-evidence", f"/run/revision/{name}", "a pull request run records the head and base it compared, as observed or inferred")


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
        scope_keys = RULE_SCOPE[exception["rule"]]
        for key, values in exception["scope"].items():
            if key not in scope_keys and values:
                findings.error("policy-exception", f"{base}/scope/{key}", f"an exception to {exception['rule']} cannot be scoped by {key}")
        for key in scope_keys:
            if not exception["scope"].get(key):
                findings.error("policy-exception", f"{base}/scope", f"an exception to {exception['rule']} names the {key} it covers")
        approval = exception["approval"]
        if approval["status"] == "approved" and not (approval.get("reference") and approval.get("date")):
            findings.error("policy-exception", f"{base}/approval", "an approved exception records where and when it was approved")
        if approval.get("date") and not calendar_date(approval["date"]):
            findings.error("policy-exception", f"{base}/approval/date", f"{approval['date']!r} is not a calendar date")
        exceptions.append(exception)
    return rules, exceptions


def policy_violations(result):
    """Yield (rule, item, path, message, detail) for each thing in a result that does not comply.

    `item` maps each of the rule's RULE_SCOPE keys to this violation's value, so that an exception
    can be matched with it. It is None when the state is not measured: no exception covers that.
    Only observed and inferred facts show compliance; a declared value in a result is a statement.
    Findings that share a rule, a path and a message are reported once, with their details joined.
    """
    layers = layers_of(result)
    family = measured(layers[-1]["os"]["family"])
    identity = result["workload"]["identity"]
    superuser = measured(identity["superuser"])
    if superuser is not False:
        state = "runs as the superuser" if superuser else "is not shown to run as a non-root user"
        yield "app-non-root", {} if superuser else None, "/workload/identity/superuser", f"the workload {state}", None
    for name, label in (("real_uid", "real"), ("saved_uid", "saved")):
        fact = identity[name]
        if fact["provenance"] == "not-applicable" and family is not None and family not in POSIX_FAMILIES:
            continue
        uid = measured(fact)
        if uid == 0:
            yield "app-non-root", {}, f"/workload/identity/{name}", f"the workload can regain root: its {label} user ID is 0", None
        elif uid is None:
            yield "app-non-root", None, f"/workload/identity/{name}", f"the {label} user ID is not measured, so the workload is not shown to be unable to regain root", None

    # A layer that any fact calls a container is held to the container rules, which only measured facts pass.
    containers = [index for index, layer in enumerate(layers) if known(layer["kind"]) == "container"]
    if not containers:
        innermost = measured(layers[-1]["kind"])
        message = None
        if family in (None, "linux"):
            # WSL 2 runs each distribution in its own PID namespace inside its VM, so there the namespace tells nothing.
            if innermost != "wsl2" and measured(result["workload"]["isolation"]["pid_namespace"]["initial"]) is not True:
                path, message = "/workload/isolation/pid_namespace/initial", "no layer is a container, but the workload is not shown to run outside one"
        elif innermost not in ("host", "vm"):
            # Off Linux no namespace probe applies, so only a measured host or VM as the innermost layer rules a container out.
            path, message = f"/environment/layers/{len(layers) - 1}/kind", f"no layer is a container, but on {family} only a measured host or VM kind shows the workload runs outside one"
        if message is not None:
            yield "engine-rootless", None, path, message, None
            yield "no-privileged-container", None, path, message, None
            yield "minimal-devices", None, path, message, None
    for index in containers:
        container = layers[index].get("container", {})
        base = f"/environment/layers/{index}/container"
        mode = measured(container.get("engine_mode"))
        if mode != "rootless":
            yield "engine-rootless", {"layers": index} if mode else None, f"{base}/engine_mode", f"the container engine is {mode or 'not shown to be rootless'}", None
        privileged = measured(container.get("privileged"))
        if privileged is not False:
            yield "no-privileged-container", {"layers": index} if privileged else None, f"{base}/privileged", "the container is privileged" if privileged else "the container is not shown to be unprivileged", None
        devices = measured(container.get("devices"))
        if devices is None:
            yield "minimal-devices", None, f"{base}/devices", "the devices passed to the container are not measured", None
        for device in devices or []:
            yield "minimal-devices", {"devices": device}, f"{base}/devices", "devices are passed to the container", device
    for name in STAGES:
        stage = result["stages"][name]
        if stage["status"] not in RAN:
            continue
        # The job that ran a stage states its escalation; the collector cannot see another step's privileges.
        escalation = known(stage.get("privilege", {}).get("escalation"))
        if escalation != "none":
            item = {"stages": name, "escalations": escalation} if escalation else None
            yield "no-privilege-escalation", item, f"/stages/{name}/privilege/escalation", f"{name} {'used ' + escalation if escalation else 'is not shown to have used no escalation'}", None
    for name in ("effective", "permitted"):
        fact = result["workload"]["isolation"]["capabilities"][name]
        path = f"/workload/isolation/capabilities/{name}"
        # Linux capabilities do not exist on another measured OS family. Windows privileges are not recorded (docs/CI-MATRIX.md).
        if fact["provenance"] == "not-applicable" and family is not None and family != "linux":
            continue
        capabilities = measured(fact)
        if capabilities is None:
            yield "minimal-capabilities", None, path, f"the {name} capabilities are not measured", None
        for capability in capabilities or []:
            yield "minimal-capabilities", {"capabilities": capability}, path, f"the workload holds {name} capabilities", capability


def covers(exception, rule, item):
    """Whether an exception covers one violation: same rule, a known state, and every scope value listed."""
    return exception["rule"] == rule and item is not None and all(item[key] in exception["scope"].get(key, []) for key in RULE_SCOPE[rule])


def check_policy(result, profile, findings):
    # Errors in the profile's own policy are reported when the profile is validated.
    rules, exceptions = effective_policy(profile, Findings())
    applied, groups = {}, {}
    for rule, item, path, message, detail in policy_violations(result):
        matching = [exception for exception in exceptions if covers(exception, rule, item)]
        approved = [exception for exception in matching if exception["approval"]["status"] == "approved"]
        if approved:
            what = ", ".join(f"{key} {value}" for key, value in item.items()) or path
            for exception in approved:
                applied.setdefault(exception["id"], []).append(what)
            continue
        unapproved = ", ".join(f"{exception['id']} ({exception['approval']['status']})" for exception in matching)
        if item is None and any(exception["rule"] == rule for exception in exceptions):
            message += "; an exception cannot cover a state that is not measured"
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
    for index, exception in enumerate(exceptions):
        if exception["approval"]["status"] != "approved":
            continue
        if exception["id"] in applied:
            # An approval is a reference this validator cannot verify, so every use of one is shown for review.
            approval = exception["approval"]
            findings.warning("policy-exception-applied", f"/policy/exceptions/{index}", f"exception {exception['id']} ({exception['rule']}) covers {'; '.join(applied[exception['id']])}; approval {approval.get('reference', 'with no reference')}, {approval.get('date', 'undated')}")
        else:
            findings.warning("policy-exception-unused", "/policy/exceptions", f"exception {exception['id']} covers nothing in this result")


# ---------------------------------------------------------------------------
# Profiles, and results against their profile


def check_support(profile, strategy_lanes, findings):
    support = profile["support"]
    status, lane, synthetic = support["status"], support["lane"], profile["synthetic"]
    if status == "checked" and synthetic:
        findings.error("support-claim", "/support/status", "a synthetic profile cannot claim a checked lane")
    if lane is None:
        if status != "not-assessed":
            findings.error("support-claim", "/support/lane", f"a {status} claim names its docs/DEPENDENCY-STRATEGY.md lane")
        return
    target = LANE_TARGETS.get(lane)
    if target is None:
        findings.error("support-claim", "/support/lane", f"no lane {lane!r} is known; name a row of the docs/DEPENDENCY-STRATEGY.md lane table exactly")
        return
    matches = lane_matches(profile, target, status, findings)
    if status == "not-assessed" and not synthetic and matches:
        findings.error("support-claim", "/support/status", "a real profile whose platform and interpreter are what its docs/DEPENDENCY-STRATEGY.md lane covers copies the lane's status")
    if strategy_lanes is None:
        if status == "checked":
            findings.warning("support-claim-unverified", "/support/status", "checked claim not compared with docs/DEPENDENCY-STRATEGY.md; pass --dependency-strategy")
        return
    if lane not in strategy_lanes:
        findings.error("support-claim", "/support/lane", f"docs/DEPENDENCY-STRATEGY.md has no lane {lane!r}")
    elif status != "not-assessed" and status not in strategy_lanes[lane]:
        recorded = " or ".join(sorted(strategy_lanes[lane])) or "no status this validator reads"
        findings.error("support-claim", "/support/status", f"docs/DEPENDENCY-STRATEGY.md records {lane!r} as {recorded}, not {status}")


def lane_matches(profile, target, status, findings):
    """Compare a profile with what its lane covers; return whether every axis and the interpreter match.

    A declared value that contradicts the lane is an error whatever the status. A status other than
    not-assessed also needs each axis the lane names, and the lock's interpreter, to be declared.
    """
    layers = layers_of(profile)
    python = profile["workload"]["runtimes"]["python"]
    kinds = [known(layer["kind"]) for layer in layers]
    libc, libc_path = libc_family(python)
    axes = (
        ("os", known(layers[-1]["os"]["family"]), f"/environment/layers/{len(layers) - 1}/os/family", "OS family"),
        ("arch", known(python["arch"]), "/workload/runtimes/python/arch", "Python architecture"),
        ("libc", libc, libc_path or "/workload/runtimes/python/soabi", "C library"),
        ("wsl2", True if "wsl2" in kinds else False if None not in kinds else None, "/environment/layers", "WSL2 layer"),
    )
    matches = True
    for axis, value, path, what in axes:
        if target[axis] is None:
            continue
        if value is not None and value != target[axis]:
            findings.error("support-claim", path, f"the profile's {what} is {value!r}; lane {profile['support']['lane']!r} covers {target[axis]!r}")
            matches = False
        elif value is None:
            matches = False
            if status != "not-assessed":
                findings.error("support-claim", path, f"a {status} claim declares the {what} its lane covers ({target[axis]!r})")
    version = known(python["version"])
    minor = re.match(r"[0-9]+\.[0-9]+", version).group(0) if isinstance(version, str) and re.match(r"[0-9]+\.[0-9]+", version) else None
    interpreter = (known(python["implementation"]), minor, known(python["free_threaded_build"]))
    if interpreter != LOCK_INTERPRETER:
        matches = False
        if status != "not-assessed":
            findings.error("support-claim", "/workload/runtimes/python", f"the lane table's statuses are for the backend lock's interpreter, CPython {LOCK_INTERPRETER[1]} with the GIL; this profile declares {interpreter[0]} {interpreter[1]} (free-threaded build: {interpreter[2]}), so it records not-assessed")
    return matches


def check_accelerator(profile, findings):
    name = profile.get("accelerator_backend")
    if name is None:
        return
    backend = {backend["id"]: backend for backend in profile["compute"]["backends"]}.get(name)
    if backend is None or backend["kind"] not in ("gpu", "npu"):
        findings.error("backend-reference", "/accelerator_backend", f"{name!r} is not a gpu or npu backend in /compute/backends")


def check_evidence_sources(profile, findings):
    if profile["synthetic"]:
        return
    if not profile["evidence_sources"]:
        findings.error("evidence-source", "/evidence_sources", "a real profile names the workflow job that records it")
    for index, source in enumerate(profile["evidence_sources"]):
        workflow = ROOT / source["workflow"]
        base = f"/evidence_sources/{index}"
        if not source["workflow"].startswith(".github/workflows/") or ".." in source["workflow"] or not workflow.is_file():
            findings.error("evidence-source", f"{base}/workflow", f"no workflow file {source['workflow']!r}")
            continue
        jobs = re.search(r"^jobs:[ \t]*\n(.*?)(?=^[^\s#]|\Z)", workflow.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
        if not jobs or not re.search(rf"^  {re.escape(source['job'])}:[ \t]*$", jobs.group(1), re.MULTILINE):
            findings.error("evidence-source", f"{base}/job", f"{source['workflow']!r} has no job {source['job']!r}")


def facts_of(node, keys=()):
    """(key path, fact) for every fact in an object, outside its extensions."""
    if is_fact(node):
        yield keys, node
    elif isinstance(node, dict):
        for key, value in node.items():
            if key != "extensions":
                yield from facts_of(value, keys + (key,))


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
    ci = result["run"]["ci"]
    job = known(ci["job"])
    if not profile["synthetic"] and measured(ci["system"]) != "github-actions":
        findings.error("profile-link", "/run/ci/system", f"profile {profile['id']} is recorded by GitHub Actions jobs; this record was not shown to come from one")
    if known(ci["system"]) == "github-actions":
        # GITHUB_WORKFLOW_REF is <owner>/<repository>/<path>@<ref>; the job is compared with the workflow it ran in when both are known.
        repository, workflow_ref = known(ci["repository"]), known(ci["workflow_ref"])
        sources = [(source["workflow"], source["job"]) for source in profile["evidence_sources"]]
        if isinstance(repository, str) and isinstance(workflow_ref, str):
            # A known workflow ref is compared when the repository is known too; otherwise the job is compared alone.
            if not workflow_ref.startswith(repository + "/"):
                findings.error("profile-link", "/run/ci/workflow_ref", f"the workflow ref {workflow_ref!r} is not in repository {repository!r}")
            else:
                workflow = workflow_ref[len(repository) + 1:].partition("@")[0]
                if (workflow, job) not in sources:
                    findings.error("profile-link", "/run/ci/workflow_ref", f"job {job!r} of {workflow!r} is not an evidence source of profile {profile['id']}")
        elif job not in [source_job for _, source_job in sources]:
            findings.error("profile-link", "/run/ci/job", f"job {job!r} is not an evidence source of profile {profile['id']}")

    def compare(path, declared_fact, observed_fact):
        # Only a measured value establishes what the profile declares; a declared value in the result does not.
        declared, observed = known(declared_fact), measured(observed_fact)
        if declared is None:
            if is_fact(declared_fact) and declared_fact["provenance"] == "not-applicable" and observed is not None:
                findings.error("profile-mismatch", path, f"the profile declares this not applicable, the result has {observed!r}")
            return
        if observed is None:
            findings.error("profile-unverified", path, f"the profile declares {declared!r}, but the run did not measure it; declare only what the evidence-source job can observe")
        elif not json_equal(declared, observed):
            findings.error("profile-mismatch", path, f"the profile declares {declared!r}, the result has {observed!r}")

    declared_layers, observed_layers = layers_of(profile), layers_of(result)
    if len(declared_layers) != len(observed_layers):
        findings.error("profile-mismatch", "/environment/layers", f"the profile declares {len(declared_layers)} layers, the result has {len(observed_layers)}")
    else:
        for index, (declared, observed) in enumerate(zip(declared_layers, observed_layers)):
            for keys, fact in facts_of(declared):
                compare(f"/environment/layers/{index}/{'/'.join(keys)}", fact, dig(observed, keys))
    for keys, fact in facts_of(profile["workload"]["runtimes"]):
        compare(f"/workload/runtimes/{'/'.join(keys)}", fact, dig(result["workload"]["runtimes"], keys))
    observed_backends = {backend["id"]: (index, backend) for index, backend in enumerate(result["compute"]["backends"])}
    for backend in profile["compute"]["backends"]:
        index, other = observed_backends.get(backend["id"], (None, None))
        if other is None:
            findings.error("profile-mismatch", "/compute/backends", f"the profile's backend {backend['id']!r} is missing from the result")
        elif (other["kind"], other["api"]) != (backend["kind"], backend["api"]):
            findings.error("profile-mismatch", f"/compute/backends/{index}", f"backend {backend['id']!r} has another kind or API than the profile declares")
        else:
            for keys, fact in facts_of(backend):
                compare(f"/compute/backends/{index}/{'/'.join(keys)}", fact, dig(other, keys))
    compare("/compute/accelerator_inventory", profile["compute"]["accelerator_inventory"], result["compute"]["accelerator_inventory"])
    for name, fact in profile.get("runner", {}).items():
        compare(f"/run/ci/{name}", fact, ci[name])
    accelerator = profile.get("accelerator_backend")
    for name in profile["required_stages"]:
        stage = result["stages"][name]
        if stage["status"] != "passed":
            findings.error("required-stage", f"/stages/{name}/status", f"profile {profile['id']} requires {name} to pass; it is {stage['status']}")
        elif accelerator is not None and name in WORKLOAD_STAGES:
            compute = stage.get("compute") or {}
            if compute.get("requested") != accelerator or compute.get("effective") != accelerator:
                ran = compute.get("effective") or "no recorded backend"
                findings.error("required-stage", f"/stages/{name}/compute", f"profile {profile['id']} requires {name} to run on {accelerator}; it ran on {ran}")


STATUS_WORDS = (
    ("checked", "Checked"),
    ("statically-resolvable", "Statically resolvable"),
    ("not-supported", "Not supported|Not binary-installable|Incomplete"),
)


def read_strategy_lanes(path):
    """Map each lane in the DEPENDENCY-STRATEGY.md lane table to the set of its backend lock statuses.

    A cell names one status first, or one per condition ("macOS 15 or newer: statically
    resolvable. macOS 14 or older: not binary-installable"); a cell with neither gives an empty set.
    """
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

    def statuses(cell, depth=0):
        same = re.match(r"Same as (.+)$", cell)
        if same and depth < 3:
            return statuses(raw.get(same.group(1).strip(), ""), depth + 1)
        for name, words in STATUS_WORDS:
            if re.match(rf"(?:{words})\b", cell):
                return frozenset({name})
        return frozenset(name for name, words in STATUS_WORDS if re.search(rf": (?:{words})\b", cell, re.IGNORECASE))

    return {lane: statuses(cell) for lane, cell in raw.items()}


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
        check_accelerator(document, findings)
        check_evidence_sources(document, findings)
        effective_policy(document, findings)
        return findings
    check_stages(document, findings)
    check_identity(document, findings)
    check_run(document, findings)
    if profile is not None:
        check_against_profile(document, profile, findings)
    elif document["profile_id"] is not None:
        policy = "; the default policy applies" if evaluate_policy else ""
        findings.warning("profile-unchecked", "/profile_id", f"the result names a profile that was not given{policy}")
    if evaluate_policy:
        check_policy(document, profile, findings)
    else:
        findings.warning("policy-skipped", "/", "the privilege policy was not evaluated (--no-policy), so this verdict says nothing about it")
    return findings


def load_schema(path=DEFAULT_SCHEMA):
    schema = load_json(path)
    check_schema(schema)
    return schema


def plain(text):
    """Text for a terminal line or a summary cell: control characters and lone surrogates become spaces; a value
    shaped like a secret, and the rest of the line after it, is replaced by a note naming the shape; "##[", which
    the GitHub Actions runner reads as a workflow command anywhere in a line, becomes "## [", and "::", which
    starts a workflow command at the start of a line (as a file name can), becomes ": :".
    """
    text = CONTROL.sub(" ", str(text))
    for label, shape in SECRET_SHAPES:
        match = shape.search(text)
        if match:
            text = text[:match.start()] + f"[not shown: looks like a {label}]"
    return text.replace("##[", "## [").replace("::", ": :")


def markdown_cell(text):
    """Text for one Markdown table cell: control characters become spaces, and Markdown and HTML syntax is escaped."""
    return re.sub(r"([\\`*_~\[\]()<>!|])", r"\\\1", plain(text))


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse repeats the argument it rejects, so its lines are written as every other line is.
        usage = "".join(plain(line) + "\n" for line in self.format_usage().splitlines())
        self.exit(2, usage + plain(f"{self.prog}: error: {message}") + "\n")


def same_file(path, other):
    """Whether two paths name one existing file, however each is written."""
    try:
        return path.samefile(other)
    except (OSError, ValueError):
        return False


def main(argv=None):
    parser = ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("documents", nargs="+", type=pathlib.Path, help="profile or result JSON files")
    parser.add_argument("--schema", type=pathlib.Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", type=pathlib.Path, help="profile that every result document is checked against")
    parser.add_argument("--dependency-strategy", type=pathlib.Path, help="compare profile support claims with this file's lane table")
    parser.add_argument("--require-real", action="store_true", help="reject synthetic documents")
    parser.add_argument("--no-policy", action="store_true", help="skip the privilege policy (for records of machines it is not meant for)")
    parser.add_argument("--summary", type=pathlib.Path, help="append a Markdown summary to this file")
    args = parser.parse_args(argv)
    # The summary is appended to, so it must not be the schema, the profile, the lane table or a document.
    if args.summary and any(same_file(args.summary, path) for path in (args.schema, args.profile, args.dependency_strategy, *args.documents) if path):
        print(plain(f"error: the summary {args.summary} is the schema, the profile, the lane table or a document of this run"), file=sys.stderr)
        return 2

    try:
        schema = load_schema(args.schema)
    except (OSError, ValueError, SchemaError, RecursionError, TypeError) as exc:
        print(plain(f"error: cannot use schema {args.schema}: {exc}"), file=sys.stderr)
        return 2

    def validate(document, *rest, **options):
        # The rules across fields read the shape the default schema requires. Another schema can accept a document
        # they cannot read: that is a usage error, not a verdict. With the default schema, however its path is
        # written, it would be a bug.
        try:
            return validate_document(document, schema, *rest, **options)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            if args.schema.resolve() == DEFAULT_SCHEMA.resolve():
                raise
            print(plain(f"error: schema {args.schema} accepts a document the rules across fields cannot read ({type(exc).__name__})"), file=sys.stderr)
            return None

    strategy_lanes = None
    if args.dependency_strategy:
        try:
            strategy_lanes = read_strategy_lanes(args.dependency_strategy)
        except (OSError, ValueError) as exc:
            print(plain(f"error: cannot read lanes from {args.dependency_strategy}: {exc}"), file=sys.stderr)
            return 2
    profile = None
    if args.profile:
        try:
            profile = load_json(args.profile)
        except (OSError, ValueError) as exc:
            print(plain(f"error: cannot read profile {args.profile}: {exc}"), file=sys.stderr)
            return 2
        profile_findings = validate(profile, strategy_lanes=strategy_lanes) if isinstance(profile, dict) else None
        if not isinstance(profile, dict) or profile.get("kind") != "profile" or profile_findings is None or profile_findings.errors:
            print(plain(f"error: {args.profile} is not a valid profile"), file=sys.stderr)
            for level, code, path, message in (profile_findings.items if profile_findings else []):
                print(plain(f"  {level.upper()} {code} {path}: {message}"), file=sys.stderr)
            return 2

    invalid = 0
    lines = ["### Compatibility record validation", "", "| Document | Kind | Result | Errors | Warnings |", "|---|---|---|---|---|"]
    details = []
    for path in args.documents:
        try:
            document = load_json(path)
        except OSError as exc:
            print(plain(f"error: cannot read {path}: {exc.strerror or exc}"), file=sys.stderr)
            return 2
        except (ValueError, UnicodeDecodeError) as exc:
            findings = Findings()
            findings.error("json", "/", f"not strict JSON: {exc}")
            document = None
        else:
            findings = validate(document, profile, strategy_lanes, not args.no_policy, args.require_real)
            if findings is None:
                return 2
        errors = findings.errors
        warnings = [item for item in findings.items if item[0] == "warning"]
        verdict = "invalid" if errors else "valid"
        invalid += bool(errors)
        kind = document.get("kind") if isinstance(document, dict) else "?"
        print(plain(f"{path}: {verdict} {kind} ({len(errors)} errors, {len(warnings)} warnings)"))
        for level, code, where, message in findings.items:
            print(plain(f"  {level.upper()} {code} {where}: {message}"))
            details.append(f"- {level} `{markdown_cell(code)}` {markdown_cell(path.name)} {markdown_cell(where)}: {markdown_cell(message)}")
        lines.append(f"| {markdown_cell(path.name)} | {markdown_cell(kind)} | {verdict} | {len(errors)} | {len(warnings)} |")

    if args.summary:
        shown = details[:200]
        if len(details) > len(shown):
            shown.append(f"- {len(details) - len(shown)} more findings are in the job log")
        lines += [""] + shown + [
            "",
            "A valid record shows that the schema, the validator and the probe worked on this runner for this commit. It is not a support claim for this or any other platform; support status is recorded only in docs/DEPENDENCY-STRATEGY.md.",
            "",
        ]
        try:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
        except OSError as exc:
            print(plain(f"error: cannot write the summary {args.summary}: {exc.strerror or exc}"), file=sys.stderr)
            return 2
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
