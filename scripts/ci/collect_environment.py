"""Record the environment this process runs in as a compatibility result (docs/CI-MATRIX.md).

The collector reads the following, and nothing else:

- /proc, /sys, /etc/os-release or /usr/lib/os-release, /run/.containerenv
  and /run/systemd/container, and whether /.dockerenv, /.singularity.d and
  /run/systemd/system exist.
- The first 20 bytes of the interpreter's executable (its ELF header), when the
  build architecture is not otherwise known.
- The environment variables in ENV_ALLOWLIST.
- The pull request head and base in the GitHub Actions event file.
- The checkout's .git/HEAD, and the loose ref or packed-refs line it points to.
- The first 4 KiB that `node -p` writes, run with an empty environment (on
  Windows, SystemRoot only) and stopped after 30 seconds. node is looked for in
  the PATH directories only, so on Windows not in the current directory, also
  when PATH is unset; there this needs CPython 3.12 or later.

Files are read only when they are regular files, and are opened so that a FIFO
cannot block the probe.

It uses no network, never escalates privileges, and writes only the output file
and the optional summary. It records no host name, account name, home
directory, network address or other environment variable. It refuses --stage
values, and does not write a record, that look like a token or a private key.

On Linux it fills in the layers, identity and isolation facts. On other systems
it records the OS family, the user and group IDs on FreeBSD (os.getresuid)
and macOS (os.geteuid, os.getuid), the Python and Node runtimes and the run,
and leaves the layer kind and the isolation facts unknown, so the
least-privilege policy fails there until a probe measures them.

Each value it records is marked in one of these ways:

- observed: read on this machine. The evidence names the file, variable or call.
- inferred: derived from evidence that is named.
- declared: stated, not measured. The collector declares only the detect
  stage's escalation (none: this script never changes its privileges) and the
  escalation a --stage-escalation option gives.
- unknown or not-applicable: with the reason.

An absent or unreadable file gives an unknown value. It is not an error. An
unexpected exception in one probe is recorded in probe_errors and fails the
detect stage. The other probes' values stay in place, so a record can show
which part of detection failed.

Standard library only. Usage, from the repository root:
    python -I scripts/ci/collect_environment.py --output result.json
        [--profile-id ID] [--summary FILE] [--no-node]
        [--stage NAME=STATUS] [--stage-reason NAME=TEXT]
        [--stage-evidence NAME=REF] [--stage-escalation NAME=VALUE]

The --stage options record what other steps of the job did in the stages after
detect. Every stage they do not name is not-run.

Exit status:
- 0: the record was written and detection passed.
- 1: the record was written but a probe failed.
- 2: a usage error, or the record or the summary could not be written. The
  record is written first, and the message says which one failed. A record
  that looks like it holds a secret is not written.
"""

import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import sysconfig
import threading
import time

COLLECTOR = {"name": "collect-environment", "version": 1}
ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = "tool:collect-environment"

# The only environment variables the collector reads. Values of PRESENCE_ONLY
# variables are never recorded: PATH is used only to find node,
# GITHUB_EVENT_PATH only to open the event file, and SystemRoot only to start
# node on Windows.
ENV_ALLOWLIST = frozenset({
    "GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF",
    "GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
    "GITHUB_WORKFLOW_REF", "GITHUB_JOB", "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH",
    "ImageOS", "ImageVersion", "container", "KUBERNETES_SERVICE_HOST", "PATH",
    "_PYTHON_HOST_PLATFORM", "_PYTHON_SYSCONFIGDATA_NAME", "_PYTHON_SYSCONFIGDATA_PATH",
    "APPTAINER_CONTAINER", "SINGULARITY_CONTAINER", "SystemRoot",
})
# sysconfig reads these even under python -I; when one is set, its values are not recorded.
SYSCONFIG_OVERRIDES = ("_PYTHON_HOST_PLATFORM", "_PYTHON_SYSCONFIGDATA_NAME", "_PYTHON_SYSCONFIGDATA_PATH")
PRESENCE_ONLY = frozenset({"KUBERNETES_SERVICE_HOST", "PATH", "GITHUB_EVENT_PATH", "APPTAINER_CONTAINER", "SINGULARITY_CONTAINER", "SystemRoot", *SYSCONFIG_OVERRIDES})

STAGES = ("detect", "install", "initialize", "execute", "correctness", "security", "performance")
STATUSES = ("passed", "failed", "blocked", "not-run", "not-applicable")
ESCALATIONS = ("none", "sudo", "root-login", "setuid", "other")
SLUG = re.compile(r"[a-z0-9][a-z0-9._+-]{0,63}")
# sysconfig.get_platform(), which on FreeBSD carries the release in capitals, such as freebsd-14.1-RELEASE-amd64.
PLATFORM_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
SHA = re.compile(r"[0-9a-f]{40}")
EVIDENCE = re.compile(r"(file|env|python|cmd|event|git|tool|doc|profile|workflow|log|artifact):[!-~][ -~]{0,299}")
PRINTABLE = re.compile(r"[ -~]{1,200}")

ARCH_ALIASES = {
    "amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64", "i386": "x86", "i486": "x86",
    "i586": "x86", "i686": "x86", "ia32": "x86", "armv7l": "armv7", "armv8l": "armv7", "arm": "armv7",
    "loong64": "loongarch64", "powerpc64le": "ppc64le",
}
# (process, kernel) architecture pairs that run without emulation: 32-bit x86 code on an x86-64 kernel.
# 32-bit Arm on aarch64 is not listed: many recent cores have no AArch32, so the pair is recorded as unknown.
COMPATIBLE = frozenset({("x86", "x86_64")})
# ELF e_machine values (elf.h) for the architectures in docs/CI-MATRIX.md.
ELF_MACHINES = {3: "x86", 40: "armv7", 62: "x86_64", 183: "aarch64", 243: "riscv64", 22: "s390x", 258: "loongarch64", 21: "ppc64le"}
# Each name above is for one byte order and one ELF class. s390x is big-endian and the others
# little-endian (ppc64, aarch64_be and armeb share their e_machine with ppc64le, aarch64 and armv7);
# x86 and armv7 are 32-bit and the others 64-bit (riscv32, 31-bit s390, loongarch32, x32 and
# aarch64 ILP32 share theirs with a 64-bit name).
ELF_BIG_ENDIAN = frozenset({"s390x"})
ELF_32_BIT = frozenset({"x86", "armv7"})
KNOWN_ARCHES = frozenset(ELF_MACHINES.values())
OS_FAMILIES = {"Linux": "linux", "Windows": "windows", "Darwin": "macos", "FreeBSD": "freebsd"}
# Capability numbers from linux/capability.h.
CAPABILITIES = (
    "chown", "dac_override", "dac_read_search", "fowner", "fsetid", "kill", "setgid", "setuid",
    "setpcap", "linux_immutable", "net_bind_service", "net_broadcast", "net_admin", "net_raw",
    "ipc_lock", "ipc_owner", "sys_module", "sys_rawio", "sys_chroot", "sys_ptrace", "sys_pacct",
    "sys_admin", "sys_boot", "sys_nice", "sys_resource", "sys_time", "sys_tty_config", "mknod",
    "lease", "audit_write", "audit_control", "setfcap", "mac_override", "mac_admin", "syslog",
    "wake_alarm", "block_suspend", "audit_read", "perfmon", "bpf", "checkpoint_restore",
)
SECCOMP = {"0": "disabled", "1": "strict", "2": "filter"}
# The fixed inode of the initial user namespace (PROC_USER_INIT_INO in linux/proc_ns.h).
USER_NS_INIT_INO = 0xEFFFFFFD
# The fixed inode of the initial PID namespace (PROC_PID_INIT_INO).
PID_NS_INIT_INO = 0xEFFFFFFC
# The same characters as CONTROL in validate_compatibility.py; a test keeps plain() equal to the validator's.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069\ud800-\udfff]")
# The same shapes as SECRET_SHAPES in validate_compatibility.py; a test keeps them equal. None starts with \b, so
# a shape is still found in the JSON text after an escape such as \n or \u200e.
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
# DMI sys_vendor or product_name prefixes that name a hypervisor, as systemd-detect-virt reads them.
DMI_VENDORS = (
    ("KVM", "kvm"), ("QEMU", "qemu"), ("VMware", "vmware"), ("VMW", "vmware"),
    ("innotek GmbH", "virtualbox"), ("VirtualBox", "virtualbox"), ("Xen", "xen"),
    ("Parallels", "parallels"), ("Amazon EC2", "amazon"), ("Google Compute Engine", "google"),
    ("Apple Virtualization", "apple-virtualization"), ("Firecracker", "firecracker"),
)
CONTAINER_TECHNOLOGIES = {"docker": "docker", "podman": "podman", "lxc": "lxc", "lxc-libvirt": "lxc", "systemd-nspawn": "systemd-nspawn", "oci": None}
NODE_EXPRESSION = "JSON.stringify({v:process.versions.node,m:process.versions.modules,n:process.versions.napi||null,a:process.arch,p:process.platform,e:require('os').endianness()})"
NODE_OUTPUT_LIMIT = 4096
NODE_TIMEOUT = 30


def observed(value, *evidence):
    return {"value": value, "provenance": "observed", "evidence": list(evidence)}


def inferred(value, *evidence):
    return {"value": value, "provenance": "inferred", "evidence": list(evidence)}


def declared(value, *evidence):
    fact = {"value": value, "provenance": "declared"}
    if evidence:
        fact["evidence"] = list(evidence)
    return fact


def unknown(reason, *evidence):
    fact = {"value": None, "provenance": "unknown", "reason": reason}
    if evidence:
        fact["evidence"] = list(evidence)
    return fact


def as_inferred(fact, *evidence):
    """An observed fact restated as inferred, with more evidence; other facts unchanged."""
    if fact["provenance"] != "observed":
        return fact
    return inferred(fact["value"], *fact["evidence"], *evidence)


def not_applicable(reason):
    return {"value": None, "provenance": "not-applicable", "reason": reason}


def normalize_arch(name):
    name = (name or "").strip().lower()
    return ARCH_ALIASES.get(name, name) if SLUG.fullmatch(name or "-") else None


def arch_family(arch):
    if arch in ("x86_64", "x86"):
        return "x86"
    if arch in ("aarch64", "armv7"):
        return "arm"
    return None


def execution_fact(arch, arch_evidence, reference):
    """native or emulated, relative to the kernel that runs the process.

    `reference` is (kernel architecture, /proc/cpuinfo format). /proc/sys/kernel/arch
    (Linux 6.1 and later) is read from the kernel, so user-mode emulation such as
    qemu-user, which rewrites uname, does not change it. Without it, only a
    /proc/cpuinfo of another architecture family shows emulation: qemu-user
    synthesises an Arm /proc/cpuinfo, so a match does not show native execution.
    """
    if arch is None:
        return unknown("the architecture is not known")
    kernel_arch, cpu_family = reference
    arch_evidence = [arch_evidence] if isinstance(arch_evidence, str) else list(arch_evidence)
    if kernel_arch is not None:
        if arch != kernel_arch and not {arch, kernel_arch} <= KNOWN_ARCHES:
            return unknown(f"{arch} on a {kernel_arch} kernel is outside the documented architectures")
        if (arch, kernel_arch) == ("armv7", "aarch64"):
            return unknown("32-bit Arm runs natively only on aarch64 cores with AArch32, which this probe does not read; many recent cores have none")
        native = arch == kernel_arch or (arch, kernel_arch) in COMPATIBLE
        return inferred("native" if native else "emulated", *arch_evidence, "file:/proc/sys/kernel/arch")
    family = arch_family(arch)
    if family is not None and cpu_family is not None and family != cpu_family:
        return inferred("emulated", *arch_evidence, "file:/proc/cpuinfo#format")
    return unknown("/proc/sys/kernel/arch is not available, and /proc/cpuinfo alone cannot rule out user-mode emulation")


class ProbeError(Exception):
    pass


class NotRegularFile(OSError):
    pass


def find_on_path(name, search_path):
    """The program `name` in the directories of `search_path`, or None.

    On Windows shutil.which also looks in the current directory first, which the path does not name, so
    there each directory is looked in by itself; an empty entry names no directory. That needs CPython 3.12 or
    later on Windows: an older shutil.which adds no PATHEXT extension to a name with a directory, so it finds
    no node.exe and the node facts are unknown."""
    if os.name != "nt":
        return shutil.which(name, path=search_path)
    for directory in search_path.split(os.pathsep):
        found = shutil.which(os.path.join(directory, name)) if directory else None
        if found:
            return found
    return None


def read_regular(path, limit):
    """Read at most `limit` bytes of a regular file.

    O_NONBLOCK keeps a FIFO from blocking the open, and anything that is not a
    regular file (a FIFO, socket, device or directory) is refused before it is
    read. The /proc and /sys files the probe reads are regular files.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_BINARY", 0)
    with open(os.open(path, flags), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise NotRegularFile(f"{path} is not a regular file")
        return handle.read(limit)


class Probe:
    """Reads one system. `root`, `env` and `git_dir` exist so tests can point it at a made-up one."""

    def __init__(self, root="/", env=None, git_dir=None, system=None, probe_node=True):
        self.root = pathlib.Path(root)
        self._env = os.environ if env is None else env
        self.git_dir = pathlib.Path(git_dir) if git_dir else ROOT / ".git"
        self.system = system or platform.system()
        self.probe_node = probe_node
        self.errors = []

    # -- inputs ------------------------------------------------------------

    def env(self, name):
        if name not in ENV_ALLOWLIST:
            raise ProbeError(f"{name} is not in ENV_ALLOWLIST")
        return self._env.get(name)

    def env_value(self, name, pattern):
        """An allowlisted variable as a fact, if its value has the expected form."""
        if name in PRESENCE_ONLY:
            raise ProbeError(f"{name} may only be tested for presence")
        value = self.env(name)
        if value is None or value == "":
            return unknown(f"{name} is not set")
        if not re.fullmatch(pattern, value):
            return unknown(f"{name} does not have the expected form", f"env:{name}")
        return observed(value, f"env:{name}")

    def path(self, absolute):
        return self.root / absolute.lstrip("/")

    def read(self, absolute, limit=65536):
        """Return (text, None), or (None, reason) when the file is absent or unreadable."""
        try:
            data = read_regular(self.path(absolute), limit)
        except FileNotFoundError:
            return None, f"{absolute} is absent"
        except PermissionError:
            return None, f"{absolute} is not readable by this user"
        except NotRegularFile:
            return None, f"{absolute} is not a regular file"
        except OSError as exc:
            return None, f"{absolute} could not be read ({type(exc).__name__})"
        return data.decode("utf-8", "replace"), None

    def exists(self, absolute):
        return self.path(absolute).exists()

    def missing(self, absolute):
        """True only when the file is absent, not when it is there but cannot be read."""
        try:
            os.stat(self.path(absolute))
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def guard(self, name, function, fallback):
        """Run one probe; record an unexpected exception and use the fallback instead."""
        try:
            return function()
        except Exception as exc:  # noqa: BLE001 - every probe failure is recorded, not raised
            self.errors.append({"probe": name, "error": f"{type(exc).__name__} raised by the {name} probe"})
            return fallback(f"the {name} probe failed ({type(exc).__name__})")

    # -- Linux signals -----------------------------------------------------

    def cpuinfo(self):
        """Return (family, hypervisor flag) from the first block of /proc/cpuinfo; None when unreadable."""
        text, _ = self.read("/proc/cpuinfo", 262144)
        if text is None:
            return None, None
        keys = {}
        for line in text.split("\n\n", 1)[0].splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                keys.setdefault(key.strip(), value.strip())
        if "CPU implementer" in keys:
            return "arm", None
        if "flags" in keys and keys.get("vendor_id") != "IBM/S390":
            return "x86", "hypervisor" in keys["flags"].split()
        return None, None

    def kernel_arch(self):
        text, _ = self.read("/proc/sys/kernel/arch", 64)
        return normalize_arch(text) if text is not None else None

    def cpu_reference(self):
        """(kernel architecture, /proc/cpuinfo format) that execution facts are compared with."""
        return self.kernel_arch(), self.cpuinfo()[0]

    def os_release(self):
        for name in ("/etc/os-release", "/usr/lib/os-release"):
            text, _ = self.read(name)
            if text is not None:
                values = {}
                for line in text.splitlines():
                    key, sep, value = line.partition("=")
                    if sep and re.fullmatch(r"[A-Z_]+", key):
                        values[key] = value.strip().strip("'\"")
                return values, f"file:{name}"
        return None, None

    def dmi_technology(self):
        for name in ("sys_vendor", "product_name"):
            text, _ = self.read(f"/sys/class/dmi/id/{name}", 256)
            if text is None:
                continue
            text = text.strip()
            for prefix, technology in DMI_VENDORS:
                if text.startswith(prefix):
                    return technology, f"file:/sys/class/dmi/id/{name}"
            if name == "product_name" and text == "Virtual Machine":
                vendor, _ = self.read("/sys/class/dmi/id/sys_vendor", 256)
                if vendor is not None and vendor.strip() == "Microsoft Corporation":
                    return "hyper-v", "file:/sys/class/dmi/id/sys_vendor"
        # Hyper-V guests, Azure among them, have a VMBus even when DMI names no hypervisor.
        vmbus = self.path("/sys/bus/vmbus/devices")
        if vmbus.is_dir() and any(vmbus.iterdir()):
            return "hyper-v", "file:/sys/bus/vmbus/devices"
        return None, None

    def wsl(self):
        """wsl2, wsl1, or microsoft for another Microsoft kernel; None for any other kernel."""
        text, _ = self.read("/proc/sys/kernel/osrelease", 256)
        if text is None or "microsoft" not in text.lower():
            return None
        text = text.strip()
        # WSL 2 kernels end in -microsoft-standard or -microsoft-standard-WSL2; WSL 1 reports the
        # Windows build, such as 4.4.0-19041-Microsoft.
        if "wsl2" in text.lower() or "microsoft-standard" in text.lower():
            return "wsl2"
        return "wsl1" if text.endswith("-Microsoft") else "microsoft"

    def container_signals(self):
        """Return (the engines the markers name, evidence list, /run/.containerenv values).

        Every marker is a file or variable inside the container, which the image
        can also supply, so the caller names an engine only when the markers agree.
        """
        evidence, named, containerenv = [], set(), {}
        if self.exists("/.dockerenv"):
            evidence.append("file:/.dockerenv")
            named.add("docker")
        text, _ = self.read("/run/.containerenv", 4096)
        if text is not None:
            evidence.append("file:/run/.containerenv")
            named.add("podman")
            for line in text.splitlines():
                key, _, value = line.partition("=")
                if key in ("engine", "rootless"):
                    containerenv[key] = value.strip().strip('"')
        # Apptainer (formerly Singularity) shares the host's PID namespace by default.
        for source, present in (("file:/.singularity.d", self.exists("/.singularity.d")),
                                ("env:APPTAINER_CONTAINER", self.env("APPTAINER_CONTAINER") is not None),
                                ("env:SINGULARITY_CONTAINER", self.env("SINGULARITY_CONTAINER") is not None)):
            if present:
                evidence.append(source)
                named.add("apptainer")
        for source, value in (("env:container", self.env("container")), ("file:/run/systemd/container", (self.read("/run/systemd/container", 256)[0] or "").strip())):
            if value:
                evidence.append(source)
                if CONTAINER_TECHNOLOGIES.get(value):
                    named.add(CONTAINER_TECHNOLOGIES[value])
        cgroup, _ = self.read("/proc/self/cgroup", 16384)
        if cgroup and re.search(r"/(docker|libpod|kubepods|lxc)[/.-]", cgroup):
            evidence.append("file:/proc/self/cgroup#path")
        if self.env("KUBERNETES_SERVICE_HOST") is not None:
            evidence.append("env:KUBERNETES_SERVICE_HOST")
        return named, evidence, containerenv

    def status_fields(self):
        text, reason = self.read("/proc/self/status")
        if text is None:
            return {}, reason
        fields = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        return fields, None

    def lsm_list(self):
        """The active Linux security modules, or None when securityfs does not list them."""
        text, _ = self.read("/sys/kernel/security/lsm", 1024)
        if text is None:
            return None
        return [name for name in text.strip().split(",") if name]

    def mount_types(self):
        """{mount point: file system type} from /proc/self/mountinfo; the last mount on a point wins."""
        text, reason = self.read("/proc/self/mountinfo", 1 << 20)
        if text is None:
            return None, reason
        mounts = {}
        for line in text.splitlines():
            left, sep, right = line.partition(" - ")
            fields, tail = left.split(), right.split()
            if sep and len(fields) >= 5 and tail:
                mounts[fields[4]] = tail[0]
        return mounts, None

    def namespace_inode(self, name):
        return os.stat(self.path(f"/proc/self/ns/{name}")).st_ino

    # -- sections ----------------------------------------------------------

    def layers(self):
        if self.system != "Linux":
            family = OS_FAMILIES.get(self.system)
            reason = f"this probe reads Linux interfaces only; this system is {self.system or 'unidentified'}"
            return [{
                "kind": unknown(reason),
                "technology": unknown(reason),
                "os": {
                    "family": observed(family, "python:platform.system") if family else unknown(reason),
                    "name": unknown(reason),
                    "version": unknown(reason),
                    "kernel": unknown(reason),
                },
                "cpu": {"arch": unknown(reason), "execution": unknown(reason)},
                "service_manager": {"name": unknown(reason), "scope": unknown(reason)},
            }]

        _, hypervisor = self.cpuinfo()
        kernel_arch = self.kernel_arch()
        kernel_fact = self.kernel_release()

        container_names, container_evidence, containerenv = self.container_signals()
        in_container = bool(container_evidence)
        wsl = self.wsl()
        dmi_technology, dmi_evidence = self.dmi_technology()
        if dmi_technology == "amazon" and not hypervisor:
            # EC2 metal instances name Amazon EC2 in DMI but run no hypervisor; their product name is
            # <type>.metal or <type>.metal-<size>. Arm /proc/cpuinfo has no hypervisor flag to tell.
            product, _ = self.read("/sys/class/dmi/id/product_name", 256)
            if product is not None and re.search(r"\.metal(-[0-9a-z]+)?$", product.strip()):
                dmi_technology, dmi_evidence = None, None
        try:
            initial_pid_namespace = self.namespace_inode("pid") == PID_NS_INIT_INO
        except OSError:
            initial_pid_namespace = None

        # The layer that runs the kernel.
        kernel_layer = {"os": {"family": observed("linux", "python:platform.system"), "kernel": kernel_fact}}
        if wsl == "wsl1":
            reason = "WSL 1 runs Linux programs on Windows without a VM"
            kernel_layer["kind"] = unknown(reason)
            kernel_layer["technology"] = unknown(reason)
            kernel_layer["os"]["kernel"] = unknown("WSL 1 runs no Linux kernel; Windows reports this release string")
        elif wsl == "microsoft":
            reason = "a Microsoft kernel that is not a known WSL release"
            kernel_layer["kind"] = unknown(reason)
            kernel_layer["technology"] = unknown(reason)
        elif wsl == "wsl2":
            kernel_layer["kind"] = inferred("wsl2", "file:/proc/sys/kernel/osrelease")
            kernel_layer["technology"] = inferred("wsl2", "file:/proc/sys/kernel/osrelease")
        elif hypervisor or dmi_technology:
            vm_evidence = [e for e in (("file:/proc/cpuinfo#flags:hypervisor" if hypervisor else None), dmi_evidence) if e]
            kernel_layer["kind"] = inferred("vm", *vm_evidence)
            kernel_layer["technology"] = inferred(dmi_technology, dmi_evidence) if dmi_technology else unknown("a hypervisor is present but neither DMI nor a VMBus names it")
        else:
            reason = "no VM or WSL signal was found; that does not show bare metal"
            kernel_layer["kind"] = unknown(reason)
            kernel_layer["technology"] = unknown(reason)

        hidden = "not visible from inside the container"
        if in_container:
            kernel_layer["os"]["name"] = unknown(f"the distribution of the system that runs the container is {hidden}")
            kernel_layer["os"]["version"] = unknown(f"the version of the system that runs the container is {hidden}")
            kernel_layer["service_manager"] = {"name": unknown(f"the service manager outside the container is {hidden}"), "scope": unknown(hidden)}
        elif initial_pid_namespace is not True and wsl != "wsl2":
            # A container that leaves none of the signals above still has its own PID namespace,
            # and its os-release and PID 1 are not the kernel layer's.
            why = "this process is not in the initial PID namespace, or that cannot be read, so os-release and PID 1 may belong to a container or sandbox"
            kernel_layer["os"]["name"] = unknown(why)
            kernel_layer["os"]["version"] = unknown(why)
            kernel_layer["service_manager"] = {"name": unknown(why), "scope": unknown(why)}
        else:
            # Taken to be the kernel layer's because no container signal was found and the PID
            # namespace is the initial one, or under WSL 2 the distribution's own, which WSL 2 gives
            # every distribution. A chroot, or a container that shares that namespace and leaves no
            # signal, would be read here too, so these facts are inferred, not observed.
            pid_evidence = "file:/proc/self/ns/pid#inode" if initial_pid_namespace else "file:/proc/sys/kernel/osrelease"
            kernel_layer["os"].update({key: as_inferred(fact, pid_evidence) for key, fact in self.distribution().items()})
            kernel_layer["service_manager"] = {key: as_inferred(fact, pid_evidence) for key, fact in self.service_manager().items()}
        if kernel_arch is not None:
            layer_arch = observed(kernel_arch, "file:/proc/sys/kernel/arch")
        else:
            # uname and /proc/cpuinfo both follow personality changes and qemu-user, so neither names the kernel's architecture.
            layer_arch = unknown("without /proc/sys/kernel/arch, uname cannot be told apart from an emulated or personality-changed machine name")
        kernel_layer["cpu"] = {
            "arch": layer_arch,
            "execution": unknown("whether the layer below emulates this CPU is not visible from inside it"),
        }
        kernel_layer = order_layer(kernel_layer)

        layers = []
        if known_kind(kernel_layer) in ("vm", "wsl2"):
            host_reason = "a guest cannot see the physical host"
            host_family = inferred("windows", "file:/proc/sys/kernel/osrelease") if wsl == "wsl2" else unknown(host_reason)
            layers.append(order_layer({
                "kind": inferred("host", *kernel_layer["kind"]["evidence"]),
                "technology": not_applicable("the outermost host runs on hardware"),
                "os": {"family": host_family, "name": unknown(host_reason), "version": unknown(host_reason), "kernel": unknown(host_reason)},
                "cpu": {"arch": unknown(host_reason), "execution": unknown(host_reason)},
                "service_manager": {"name": unknown(host_reason), "scope": unknown(host_reason)},
            }))
        layers.append(kernel_layer)

        if in_container:
            container = {"kind": inferred("container", *container_evidence)}
            if len(container_names) == 1:
                container["technology"] = inferred(next(iter(container_names)), *container_evidence)
            elif container_names:
                container["technology"] = unknown(f"the container signals name different engines ({', '.join(sorted(container_names))})", *container_evidence)
            else:
                container["technology"] = unknown("the container signals do not name the engine", *container_evidence)
            container["os"] = dict(self.distribution(), family=observed("linux", "python:platform.system"), kernel=not_applicable("a container shares the kernel of the layer below"))
            # The container runs on the kernel layer's CPU, and its programs need not all be built for
            # one architecture, so each runtime records its own architecture and whether it is emulated.
            per_runtime = "a container's programs can be built for different architectures; each runtime records its own"
            container["cpu"] = {"arch": unknown(per_runtime), "execution": unknown(per_runtime)}
            container["service_manager"] = self.service_manager()
            rootless = containerenv.get("rootless")
            if container_names != {"podman"}:
                engine_mode = unknown("only Podman tells a container whether it runs rootless, and the markers do not name Podman alone")
            elif rootless in ("0", "1"):
                # The file is inside the container; Podman writes it, but an image could also hold one.
                engine_mode = inferred("rootless" if rootless == "1" else "rootful", "file:/run/.containerenv#rootless")
            else:
                engine_mode = unknown("/run/.containerenv does not say whether the engine runs rootless")
            orchestration_name = inferred("kubernetes", "env:KUBERNETES_SERVICE_HOST") if self.env("KUBERNETES_SERVICE_HOST") is not None else unknown("no orchestrator signal; that does not show there is none")
            container["container"] = {
                "engine_mode": engine_mode,
                "privileged": unknown("no reliable signal from inside the container"),
                "devices": unknown("the devices an engine passes in are not recorded inside the container"),
                "orchestration": {
                    "name": orchestration_name,
                    "version": unknown("not visible from inside the container"),
                    "scope": unknown("not visible from inside the container"),
                },
            }
            layers.append(order_layer(container))
        return layers

    def kernel_release(self):
        """The release the kernel reports in /proc; uname, which personality changes and qemu-user can rewrite, only as a fallback."""
        text, _ = self.read("/proc/sys/kernel/osrelease", 256)
        if text is not None:
            release = text.strip()
            if PRINTABLE.fullmatch(release):
                return observed(release, "file:/proc/sys/kernel/osrelease")
            return unknown("the kernel release is empty or has unexpected characters", "file:/proc/sys/kernel/osrelease")
        release = platform.release()
        if PRINTABLE.fullmatch(release or ""):
            return inferred(release, "python:platform.release")
        return unknown("the kernel release is empty or has unexpected characters")

    def distribution(self):
        values, evidence = self.os_release()
        if values is None:
            return {"name": unknown("no os-release file"), "version": unknown("no os-release file")}
        name = values.get("ID", "").lower()
        version = values.get("VERSION_ID", "")
        return {
            "name": observed(name, f"{evidence}#ID") if SLUG.fullmatch(name) else unknown("os-release has no usable ID", evidence),
            "version": observed(version, f"{evidence}#VERSION_ID") if PRINTABLE.fullmatch(version) else unknown("os-release has no usable VERSION_ID", evidence),
        }

    def service_manager(self):
        if self.exists("/run/systemd/system"):
            pid1, _ = self.read("/proc/1/comm", 64)
            scope = observed("system", "file:/proc/1/comm") if pid1 is not None and pid1.strip() == "systemd" else unknown("PID 1 of this namespace is not systemd or cannot be read")
            return {"name": observed("systemd", "file:/run/systemd/system"), "scope": scope}
        reason = "systemd is not running as the service manager here; other managers are not probed"
        return {"name": unknown(reason, "file:/run/systemd/system"), "scope": unknown(reason)}

    def identity(self):
        # The real and saved user IDs matter too: a process whose real or saved ID is 0 can set its effective ID back to 0.
        if self.system == "Linux":
            fields, reason = self.status_fields()
            if "Uid" not in fields or "Gid" not in fields:
                return identity_fallback(reason or "/proc/self/status has no Uid or Gid line")
            # Uid: real, effective, saved set-user-ID, file system.
            real_uid, uid, saved_uid = (int(part) for part in fields["Uid"].split()[:3])
            gid = int(fields["Gid"].split()[1])
            source = "file:/proc/self/status#Uid"
            ids = {"uid": observed(uid, source), "real_uid": observed(real_uid, source), "saved_uid": observed(saved_uid, source)}
            gid_fact = observed(gid, "file:/proc/self/status#Gid")
        elif self.system == "Windows":
            # Whether a Windows process holds an elevated token is not probed, so superuser stays unknown.
            identity = identity_fallback("this probe reads POSIX user IDs only")
            identity["real_uid"] = not_applicable("Windows has no POSIX user IDs")
            identity["saved_uid"] = not_applicable("Windows has no POSIX user IDs")
            return identity
        elif self.system == "FreeBSD" and hasattr(os, "getresuid"):
            real_uid, uid, saved_uid = os.getresuid()
            source = "python:os.getresuid"
            ids = {"uid": observed(uid, source), "real_uid": observed(real_uid, source), "saved_uid": observed(saved_uid, source)}
            gid_fact = observed(os.getegid(), "python:os.getegid")
        elif self.system == "Darwin" and hasattr(os, "geteuid"):
            # macOS has no getresuid. execve(2) saves the effective user ID, and this script never changes it.
            uid = os.geteuid()
            source = "python:os.geteuid"
            ids = {"uid": observed(uid, source), "real_uid": observed(os.getuid(), "python:os.getuid"), "saved_uid": inferred(uid, source)}
            gid_fact = observed(os.getegid(), "python:os.getegid")
        else:
            # Elsewhere, such as Cygwin or MSYS2 on Windows, user ID 0 does not name the administrator.
            return identity_fallback("this probe reads user IDs on Linux, FreeBSD, and macOS only")
        identity = {"superuser": observed(uid == 0, source), **ids, "gid": gid_fact}
        if self.system != "Linux":
            reason = "user namespaces are a Linux interface"
            identity["user_namespace"] = {"initial": unknown(reason), "uid_map": unknown(reason)}
            return identity
        text, reason = self.read("/proc/self/uid_map")
        if text is None:
            uid_map = unknown(reason)
        else:
            rows = [[int(part) for part in line.split()] for line in text.splitlines() if line.strip()]
            if not rows or any(len(row) != 3 or not all(0 <= part <= 4294967295 for part in row) for row in rows):
                raise ValueError("unexpected /proc/self/uid_map format")
            uid_map = observed(rows, "file:/proc/self/uid_map")
        # A full identity map does not show the initial namespace: a nested one can have it too.
        try:
            initial = observed(self.namespace_inode("user") == USER_NS_INIT_INO, "file:/proc/self/ns/user#inode")
        except OSError as exc:
            initial = unknown(f"/proc/self/ns/user cannot be read ({type(exc).__name__})")
        identity["user_namespace"] = {"initial": initial, "uid_map": uid_map}
        return identity

    def isolation(self):
        if self.system != "Linux":
            return isolation_fallback("this probe reads Linux interfaces only")
        fields, reason = self.status_fields()
        status = "file:/proc/self/status"

        def field(name, convert):
            if name not in fields:
                return unknown(reason or f"/proc/self/status has no {name} line")
            return observed(convert(fields[name]), f"{status}#{name}")

        out = {
            "no_new_privs": field("NoNewPrivs", lambda v: {"0": False, "1": True}[v]),
            "capabilities": {"effective": field("CapEff", decode_capabilities), "permitted": field("CapPrm", decode_capabilities), "bounding": field("CapBnd", decode_capabilities)},
            "seccomp": field("Seccomp", lambda v: SECCOMP[v]),
            "apparmor": self.apparmor(),
            "selinux": self.selinux(),
            "cgroup": self.cgroup(fields),
        }
        # Containers get their own PID namespace; the initial one shows the process is not in one.
        try:
            initial = observed(self.namespace_inode("pid") == PID_NS_INIT_INO, "file:/proc/self/ns/pid#inode")
        except OSError as exc:
            initial = unknown(f"/proc/self/ns/pid cannot be read ({type(exc).__name__})")
        out["pid_namespace"] = {"initial": initial}
        return out

    def apparmor(self):
        if not self.exists("/sys/module"):
            reason = "/sys/module is not available"
            return {"enabled": unknown(reason), "profile": unknown(reason)}
        text, reason = self.read("/sys/module/apparmor/parameters/enabled", 16)
        if text is None:
            if self.missing("/sys/module/apparmor/parameters/enabled"):
                return {"enabled": inferred(False, "file:/sys/module/apparmor/parameters/enabled#absent"), "profile": not_applicable("AppArmor is not in this kernel")}
            return {"enabled": unknown(reason), "profile": unknown(reason)}
        # The kernel writes Y or N; anything else, such as a file that was replaced, says nothing.
        if text.strip() not in ("Y", "N"):
            reason = "/sys/module/apparmor/parameters/enabled holds neither Y nor N"
            return {"enabled": unknown(reason), "profile": unknown(reason)}
        if text.strip() == "N":
            return {"enabled": observed(False, "file:/sys/module/apparmor/parameters/enabled"), "profile": not_applicable("AppArmor is not enabled")}
        # /proc/self/attr/current belongs to whichever major LSM comes first, so only the
        # AppArmor-specific file (Linux 5.8 and later) is read.
        label, _ = self.read("/proc/self/attr/apparmor/current", 512)
        if label is not None and PRINTABLE.fullmatch(label.strip("\n\x00 ")):
            return {"enabled": observed(True, "file:/sys/module/apparmor/parameters/enabled"), "profile": observed(label.strip("\n\x00 "), "file:/proc/self/attr/apparmor/current")}
        return {"enabled": observed(True, "file:/sys/module/apparmor/parameters/enabled"), "profile": unknown("the AppArmor label of this process cannot be read")}

    def selinux(self):
        lsms = self.lsm_list()
        text, _ = self.read("/sys/fs/selinux/enforce", 16)
        if text is not None and text.strip() in ("0", "1"):
            mode = observed("enforcing" if text.strip() == "1" else "permissive", "file:/sys/fs/selinux/enforce")
            # /proc/self/attr/current is SELinux's only when no other major LSM is listed before it.
            if lsms is not None and "selinux" in lsms and not {"apparmor", "smack", "tomoyo"} & set(lsms[:lsms.index("selinux")]):
                label, _ = self.read("/proc/self/attr/current", 512)
                if label is not None and PRINTABLE.fullmatch(label.strip("\n\x00 ")):
                    return {"mode": mode, "context": observed(label.strip("\n\x00 "), "file:/proc/self/attr/current", "file:/sys/kernel/security/lsm")}
            return {"mode": mode, "context": unknown("the SELinux context of this process cannot be read or attributed")}
        if lsms is not None and "selinux" not in lsms:
            return {"mode": inferred("disabled", "file:/sys/kernel/security/lsm"), "context": not_applicable("SELinux is not an active security module")}
        if lsms is not None:
            why = "SELinux is an active security module but selinuxfs is not mounted here"
            return {"mode": unknown(why), "context": unknown(why)}
        filesystems, reason = self.read("/proc/filesystems", 16384)
        if filesystems is not None and not re.search(r"\bselinuxfs\b", filesystems):
            return {"mode": inferred("disabled", "file:/proc/filesystems#selinuxfs-absent"), "context": not_applicable("SELinux is not in this kernel")}
        why = "the kernel supports SELinux but neither securityfs nor selinuxfs is mounted here" if filesystems is not None else reason
        return {"mode": unknown(why), "context": unknown(why)}

    def cgroup(self, status):
        """The cgroup version from the file system mounted at /sys/fs/cgroup, and delegation for v2."""
        mounts, reason = self.mount_types()
        if mounts is None:
            return {"version": unknown(reason), "delegated": unknown(reason)}
        evidence = "file:/proc/self/mountinfo#/sys/fs/cgroup"
        top = mounts.get("/sys/fs/cgroup")
        if top == "cgroup2":
            version = observed("v2", evidence)
        elif top == "tmpfs" and any(fstype == "cgroup" for point, fstype in mounts.items() if point.startswith("/sys/fs/cgroup/")):
            hybrid = any(fstype == "cgroup2" for point, fstype in mounts.items() if point.startswith("/sys/fs/cgroup/"))
            version = observed("hybrid" if hybrid else "v1", evidence)
        else:
            why = "nothing is mounted at /sys/fs/cgroup" if top is None else "/sys/fs/cgroup has an unexpected file system"
            return {"version": unknown(why, evidence), "delegated": unknown(why)}
        if version["value"] != "v2":
            return {"version": version, "delegated": unknown("delegation is read for cgroup v2 only")}
        # access(2) checks the real user ID, so a real, effective or saved ID of 0, or any capability, leaves delegation unknown.
        uid = status.get("Uid", "").split()
        if len(uid) < 4 or "0" in uid or any(status.get(name, "0").strip("0") for name in ("CapEff", "CapPrm")):
            return {"version": version, "delegated": unknown("write access says nothing about delegation for a process with root or capabilities")}
        text, reason = self.read("/proc/self/cgroup", 16384)
        unified = [line[3:] for line in (text or "").splitlines() if line.startswith("0::")]
        if not unified:
            return {"version": version, "delegated": unknown(reason or "/proc/self/cgroup has no cgroup v2 line")}
        relative = unified[0].lstrip("/")
        directory = self.path("/sys/fs/cgroup") / relative
        if ".." in relative.split("/") or not directory.is_dir():
            return {"version": version, "delegated": unknown("this process's cgroup directory is not visible here")}
        # access(2) reports permission bits only; nothing is written to find out. The kernel's delegation model grants write
        # access to the directory and its cgroup.procs, cgroup.threads (Linux 4.14 and later) and cgroup.subtree_control;
        # without the last a process cannot enable controllers for its children, so that is not delegation.
        names = ["cgroup.procs", "cgroup.subtree_control"]
        if not self.missing(f"/sys/fs/cgroup/{relative}/cgroup.threads"):
            names.append("cgroup.threads")
        delegated = os.access(directory, os.W_OK) and all(os.access(directory / name, os.W_OK) for name in names)
        evidence = ["file:/sys/fs/cgroup/<own-cgroup>#writable"] + [f"file:/sys/fs/cgroup/<own-cgroup>/{name}#writable" for name in names]
        return {"version": version, "delegated": observed(delegated, *evidence)}

    def sysconfig_overrides(self):
        return [name for name in SYSCONFIG_OVERRIDES if self.env(name) is not None]

    def build_arch(self):
        """The architecture the interpreter was built for, with its evidence.

        On Linux sysconfig.get_platform() follows uname, which emulation and
        personality changes rewrite, so the build triplet is read instead.
        """
        multiarch = getattr(sys.implementation, "_multiarch", None)
        if isinstance(multiarch, str) and "-" in multiarch:
            return normalize_arch(multiarch.split("-", 1)[0]), "python:sys.implementation._multiarch"
        soabi = None if self.sysconfig_overrides() else sysconfig.get_config_var("SOABI")
        match = re.fullmatch(r"[a-z]+-[0-9]+[a-z]*-([a-z0-9_]+)-linux-[a-z0-9]+", soabi or "")
        if match:
            return normalize_arch(match.group(1)), "python:sysconfig.SOABI"
        try:
            with open(os.path.realpath(sys.executable or "/nonexistent"), "rb") as handle:
                header = handle.read(20)
        except OSError:
            return None, None
        if len(header) == 20 and header[:4] == b"\x7fELF" and header[5] in (1, 2):
            machine = int.from_bytes(header[18:20], "little" if header[5] == 1 else "big")
            name = ELF_MACHINES.get(machine)
            if name and header[4] == (1 if name in ELF_32_BIT else 2) and header[5] == (2 if name in ELF_BIG_ENDIAN else 1):
                return name, "file:sys.executable#e_machine"
        return None, None

    def python(self, reference):
        version = platform.python_version()
        overrides = self.sysconfig_overrides()
        if overrides:
            why = f"{', '.join(overrides)} is set, which changes what sysconfig reports"
            plat_fact, soabi, raw_gil_disabled = unknown(why), None, None
        else:
            plat = sysconfig.get_platform()
            plat_fact = observed(plat, "python:sysconfig.get_platform") if isinstance(plat, str) and PLATFORM_TAG.fullmatch(plat) else unknown("sysconfig.get_platform() gave an unexpected value")
            soabi = sysconfig.get_config_var("SOABI")
            raw_gil_disabled = sysconfig.get_config_var("Py_GIL_DISABLED")
        if self.system == "Linux":
            arch, arch_evidence = self.build_arch()
        else:
            arch, arch_evidence = split_platform(plat_fact["value"] or "")[1], "python:sysconfig.get_platform"
        if raw_gil_disabled in (0, 1):
            free_threaded = observed(bool(raw_gil_disabled), "python:sysconfig.Py_GIL_DISABLED")
        elif raw_gil_disabled is None and sys.version_info < (3, 13):
            free_threaded = inferred(False, "python:sys.version_info")
        else:
            free_threaded = unknown("sysconfig gives no usable Py_GIL_DISABLED")
        if hasattr(sys, "_is_gil_enabled"):
            gil = observed(sys._is_gil_enabled(), "python:sys._is_gil_enabled")
        elif free_threaded["value"] is False:
            gil = inferred(True, *free_threaded["evidence"])
        else:
            gil = unknown("sys._is_gil_enabled is missing")
        try:
            # Only glibc reports its version here. platform.libc_ver() would fall back to scanning
            # the interpreter's executable, which gives the version it was linked against.
            libc = os.confstr("CS_GNU_LIBC_VERSION")
        except (AttributeError, ValueError, OSError):
            libc = None
        return {
            "implementation": observed(sys.implementation.name, "python:sys.implementation.name"),
            "version": observed(version, "python:platform.python_version"),
            "cache_tag": observed(sys.implementation.cache_tag, "python:sys.implementation.cache_tag") if sys.implementation.cache_tag else unknown("no cache tag"),
            "soabi": observed(soabi, "python:sysconfig.SOABI") if isinstance(soabi, str) and SLUG.fullmatch(soabi) else unknown(why if overrides else "sysconfig has no usable SOABI"),
            "platform": plat_fact,
            "arch": observed(arch, arch_evidence) if arch else unknown("the interpreter's build architecture was not found"),
            "pointer_bits": observed(struct.calcsize("P") * 8, "python:struct.calcsize"),
            "free_threaded_build": free_threaded,
            "gil_enabled": gil,
            "libc": observed(libc, "python:os.confstr") if isinstance(libc, str) and re.fullmatch(r"glibc [0-9]{1,4}\.[0-9]{1,4}(\.[0-9]{1,4})?", libc) else unknown("the C library reports no version through os.confstr; only glibc does"),
            "execution": execution_fact(arch, arch_evidence, reference) if self.system == "Linux" else unknown("compared on Linux only"),
        }

    def node(self, reference):
        def all_unknown(reason):
            return {name: unknown(reason) for name in ("version", "modules_abi", "napi_version", "platform", "arch", "execution")}

        if not self.probe_node:
            return all_unknown("not probed (--no-node)")
        # An unset PATH searches the default directories, but not the current directory that Windows lists first
        # in them; an empty one, as shutil.which reads it, finds nothing.
        search_path = self.env("PATH")
        if search_path is None:
            search_path = os.pathsep.join(entry for entry in os.defpath.split(os.pathsep) if os.name != "nt" or entry != os.curdir)
        executable = find_on_path("node", search_path)
        if executable is None:
            return all_unknown("node is not on PATH")
        # Windows needs SystemRoot to start a program that loads side-by-side assemblies; nothing else is passed.
        system_root = self.env("SystemRoot") if os.name == "nt" else None
        try:
            # On POSIX node gets a session, and so a process group, of its own; Windows ignores the option.
            process = subprocess.Popen(
                [executable, "-p", NODE_EXPRESSION], env={"SystemRoot": system_root} if system_root else {}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            return all_unknown(f"node could not be run ({type(exc).__name__})")

        def stop():
            # The whole group, so that a child holding stdout open cannot keep the read below waiting.
            # A descendant that starts a session of its own can, until it exits or the job times out.
            try:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass

        # Read at most one byte more than the limit, and stop node if it runs too long or writes too much.
        # The timer stops node only while this thread reads. This thread ends that under the lock, so a
        # timer that already fired has sent its signal by then, and only this thread reaps node: no signal
        # can reach a process ID that was reaped and perhaps reused.
        expired, lock = threading.Event(), threading.Lock()
        deadline = time.monotonic() + NODE_TIMEOUT
        reading = True

        def expire():
            with lock:
                if reading:
                    expired.set()
                    stop()

        timer = threading.Timer(NODE_TIMEOUT, expire)
        timer.start()
        try:
            with process.stdout:
                output = process.stdout.read(NODE_OUTPUT_LIMIT + 1)
                if len(output) > NODE_OUTPUT_LIMIT:
                    stop()
            with lock:
                reading = False
            try:
                status = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                expired.set()
                stop()
                status = process.wait()
        finally:
            with lock:
                reading = False
            timer.cancel()
            if process.poll() is None:
                stop()
                process.wait()
        if expired.is_set():
            return all_unknown(f"node did not finish within {NODE_TIMEOUT} seconds")
        if len(output) > NODE_OUTPUT_LIMIT:
            return all_unknown(f"node wrote more than {NODE_OUTPUT_LIMIT} bytes")
        if status != 0:
            return all_unknown(f"node exited with status {status}")
        values = json.loads(output.decode("utf-8"))
        evidence = "cmd:node -p process.versions,process.arch,process.platform,os.endianness()"

        def checked(key, pattern):
            value = values.get(key)
            return observed(str(value), evidence) if value is not None and re.fullmatch(pattern, str(value)) else unknown(f"node reported no usable {key}")

        arch = normalize_arch(values.get("a"))
        if arch == "ppc64" and values.get("e") == "LE":
            # Node reports ppc64 for both byte orders; its own os.endianness() tells them apart.
            arch = "ppc64le"
        arch_evidence = [evidence]
        arch_fact = observed(arch, evidence) if arch else unknown("node reported no usable architecture")
        platform_name = values.get("p")
        return {
            "version": checked("v", r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}"),
            "modules_abi": checked("m", r"[0-9]{1,5}"),
            "napi_version": checked("n", r"[0-9]{1,5}"),
            "platform": observed(platform_name, evidence) if isinstance(platform_name, str) and SLUG.fullmatch(platform_name) else unknown("node reported no usable platform"),
            "arch": arch_fact,
            "execution": execution_fact(arch, arch_evidence, reference) if self.system == "Linux" else unknown("compared on Linux only"),
        }

    def run(self):
        system = self.env("GITHUB_ACTIONS") == "true"
        na = "not running in GitHub Actions"

        def gha(name, pattern):
            return self.env_value(name, pattern) if system else not_applicable(na)

        ci = {
            "system": observed("github-actions", "env:GITHUB_ACTIONS") if system else inferred("local", "env:GITHUB_ACTIONS"),
            "event": gha("GITHUB_EVENT_NAME", r"[a-z_]{1,64}"),
            "repository": gha("GITHUB_REPOSITORY", r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}"),
            "ref": gha("GITHUB_REF", r"refs/[A-Za-z0-9._/+-]{1,190}"),
            "run_id": gha("GITHUB_RUN_ID", r"[0-9]{1,20}"),
            "run_attempt": gha("GITHUB_RUN_ATTEMPT", r"[0-9]{1,4}"),
            "run_url": not_applicable(na),
            "workflow_ref": gha("GITHUB_WORKFLOW_REF", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]{1,150}@refs/[A-Za-z0-9._/+-]{1,150}"),
            "job": gha("GITHUB_JOB", r"[A-Za-z0-9_-]{1,100}"),
            "runner_environment": gha("RUNNER_ENVIRONMENT", r"github-hosted|self-hosted"),
            "runner_os": gha("RUNNER_OS", r"[A-Za-z0-9]{1,40}"),
            "runner_arch": gha("RUNNER_ARCH", r"[A-Za-z0-9]{1,40}"),
            "image_os": gha("ImageOS", r"[A-Za-z0-9._-]{1,40}"),
            "image_version": gha("ImageVersion", r"[A-Za-z0-9._-]{1,40}"),
        }
        if ci["run_attempt"]["value"] is not None:
            ci["run_attempt"]["value"] = int(ci["run_attempt"]["value"])
        if system:
            # The documented form; the attempt is in run_attempt.
            server = self.env_value("GITHUB_SERVER_URL", r"https://[A-Za-z0-9.-]{1,100}")
            parts = (server["value"], ci["repository"]["value"], ci["run_id"]["value"])
            if all(part is not None for part in parts):
                ci["run_url"] = inferred(f"{parts[0]}/{parts[1]}/actions/runs/{parts[2]}", "env:GITHUB_SERVER_URL", "env:GITHUB_REPOSITORY", "env:GITHUB_RUN_ID")
            else:
                ci["run_url"] = unknown("a run URL needs GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_RUN_ID")
        revision = {
            "tested_sha": self.checkout_sha(),
            "event_sha": gha("GITHUB_SHA", r"[0-9a-f]{40}"),
        }
        revision.update(self.pull_request(ci["event"]["value"]) if system else {"pr_head_sha": not_applicable(na), "pr_base_sha": not_applicable(na)})
        return {"ci": ci, "revision": revision}

    def pull_request(self, event):
        if event is None:
            return {"pr_head_sha": unknown("the event is not known"), "pr_base_sha": unknown("the event is not known")}
        if event not in ("pull_request", "pull_request_target"):
            reason = f"the {event} event has no pull request"
            return {"pr_head_sha": not_applicable(reason), "pr_base_sha": not_applicable(reason)}
        path = self.env("GITHUB_EVENT_PATH")
        if not path:
            return {"pr_head_sha": unknown("GITHUB_EVENT_PATH is not set"), "pr_base_sha": unknown("GITHUB_EVENT_PATH is not set")}
        try:
            payload = json.loads(read_regular(path, 8 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError) as exc:
            reason = f"the event file could not be read ({type(exc).__name__})"
            return {"pr_head_sha": unknown(reason), "pr_base_sha": unknown(reason)}
        # Only the two commit IDs are taken: the payload also holds logins, e-mail addresses and titles.
        pull = payload.get("pull_request") if isinstance(payload, dict) else None
        out = {}
        for side in ("head", "base"):
            sha = pull.get(side, {}).get("sha") if isinstance(pull, dict) and isinstance(pull.get(side), dict) else None
            key = f"pr_{side}_sha"
            out[key] = observed(sha, f"event:pull_request.{side}.sha") if isinstance(sha, str) and SHA.fullmatch(sha) else unknown(f"the event payload has no pull_request.{side}.sha")
        return out

    def checkout_sha(self):
        head, reason = self.read_git("HEAD")
        if head is None:
            return unknown(reason)
        head = head.strip()
        if SHA.fullmatch(head):
            return observed(head, "git:.git/HEAD")
        ref = head[len("ref: "):] if head.startswith("ref: ") else None
        if ref is None or not re.fullmatch(r"refs/[A-Za-z0-9._/+-]{1,190}", ref) or ".." in ref:
            return unknown(".git/HEAD has an unexpected form")
        loose, _ = self.read_git(ref)
        if loose is not None and SHA.fullmatch(loose.strip()):
            return observed(loose.strip(), f"git:.git/{ref}")
        packed, _ = self.read_git("packed-refs")
        for line in (packed or "").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref and SHA.fullmatch(sha):
                return observed(sha, "git:.git/packed-refs")
        return unknown(f"{ref} is not resolved in .git")

    def read_git(self, name):
        try:
            if not self.git_dir.is_dir():
                return None, ".git is not a directory (no checkout, or a worktree)"
            return read_regular(self.git_dir / name, 1 << 20).decode("utf-8", "replace"), None
        except OSError as exc:
            return None, f".git/{name} could not be read ({type(exc).__name__})"

    def compute(self):
        return {
            "backends": [{
                "id": "cpu",
                "kind": "cpu",
                "api": "cpu",
                "available": observed(True, TOOL),
                "device": unknown("the CPU model is not recorded"),
                "version": not_applicable("a CPU has no backend version"),
                "features": unknown("CPU features are not recorded"),
            }],
            "accelerator_inventory": unknown("this collector does not look for GPUs or NPUs; a missing entry does not show there is none"),
        }

    def collect(self):
        """Return the result document without stages; see build_result()."""
        reason_layers = lambda reason: [layer_fallback(reason)]  # noqa: E731
        reference = self.guard("cpu", lambda: self.cpu_reference() if self.system == "Linux" else (None, None), lambda reason: (None, None))
        return {
            "run": self.guard("run", self.run, run_fallback),
            "environment": {"layers": self.guard("layers", self.layers, reason_layers)},
            "workload": {
                "identity": self.guard("identity", self.identity, identity_fallback),
                "isolation": self.guard("isolation", self.isolation, isolation_fallback),
                "runtimes": {
                    "python": self.guard("python", lambda: self.python(reference), python_fallback),
                    "node": self.guard("node", lambda: self.node(reference), node_fallback),
                },
            },
            "compute": self.compute(),
        }


def known_kind(layer):
    fact = layer.get("kind", {})
    return fact.get("value") if fact.get("provenance") in ("observed", "inferred", "declared") else None


LAYER_ORDER = ("label", "kind", "technology", "os", "cpu", "service_manager", "container", "extensions")


def order_layer(layer):
    layer["os"] = {key: layer["os"][key] for key in ("family", "name", "version", "kernel")}
    return {key: layer[key] for key in LAYER_ORDER if key in layer}


def split_platform(plat):
    """(OS family, architecture) from sysconfig.get_platform(), as python_platform_target() in validate_compatibility.py reads it."""
    if plat.startswith("linux-"):
        return "linux", normalize_arch(plat[len("linux-"):])
    windows = {"win32": "x86", "win-amd64": "x86_64", "win-arm64": "aarch64", "win-arm32": "armv7"}
    if plat in windows:
        return "windows", windows[plat]
    match = re.fullmatch(r"macosx-[0-9.]+-([A-Za-z0-9_]+)", plat)
    if match:
        return "macos", None if match.group(1) in ("universal2", "intel", "fat", "fat3", "fat64") else normalize_arch(match.group(1))
    match = re.fullmatch(r"freebsd-.+-([A-Za-z0-9_]+)", plat)
    if match:
        return "freebsd", normalize_arch(match.group(1))
    return None, None


def decode_capabilities(mask):
    bits = int(mask, 16)
    names = [f"cap_{name}" for index, name in enumerate(CAPABILITIES) if bits >> index & 1]
    names += [f"cap_{index}" for index in range(len(CAPABILITIES), 64) if bits >> index & 1]
    return names


def layer_fallback(reason):
    return {
        "kind": unknown(reason), "technology": unknown(reason),
        "os": {"family": unknown(reason), "name": unknown(reason), "version": unknown(reason), "kernel": unknown(reason)},
        "cpu": {"arch": unknown(reason), "execution": unknown(reason)},
        "service_manager": {"name": unknown(reason), "scope": unknown(reason)},
    }


def identity_fallback(reason):
    return {
        "superuser": unknown(reason), "uid": unknown(reason), "real_uid": unknown(reason), "saved_uid": unknown(reason),
        "gid": unknown(reason), "user_namespace": {"initial": unknown(reason), "uid_map": unknown(reason)},
    }


def isolation_fallback(reason):
    return {
        "no_new_privs": unknown(reason),
        "capabilities": {"effective": unknown(reason), "permitted": unknown(reason), "bounding": unknown(reason)},
        "seccomp": unknown(reason),
        "apparmor": {"enabled": unknown(reason), "profile": unknown(reason)},
        "selinux": {"mode": unknown(reason), "context": unknown(reason)},
        "cgroup": {"version": unknown(reason), "delegated": unknown(reason)},
        "pid_namespace": {"initial": unknown(reason)},
    }


def python_fallback(reason):
    names = ("implementation", "version", "cache_tag", "soabi", "platform", "arch", "pointer_bits", "free_threaded_build", "gil_enabled", "libc", "execution")
    return {name: unknown(reason) for name in names}


def node_fallback(reason):
    return {name: unknown(reason) for name in ("version", "modules_abi", "napi_version", "platform", "arch", "execution")}


def run_fallback(reason):
    names = ("system", "event", "repository", "ref", "run_id", "run_attempt", "run_url", "workflow_ref", "job", "runner_environment", "runner_os", "runner_arch", "image_os", "image_version")
    return {
        "ci": {name: unknown(reason) for name in names},
        "revision": {name: unknown(reason) for name in ("tested_sha", "event_sha", "pr_head_sha", "pr_base_sha")},
    }


def overall_of(stages):
    statuses = [stages[name]["status"] for name in STAGES]
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses and all(status in ("passed", "not-applicable") for status in statuses):
        return "passed"
    return "incomplete"


def build_result(probe, profile_id=None, stage_args=None, now=None):
    """Collect, then add the stages: detect from the probes, the others from stage_args."""
    sections = probe.collect()
    if probe.errors:
        names = ", ".join(error["probe"] for error in probe.errors)
        detect = {"status": "failed", "reason": f"probe(s) raised unexpected errors: {names}; see probe_errors", "evidence": [f"{TOOL}#probe_errors"]}
    else:
        detect = {"status": "passed", "evidence": [TOOL]}
    detect["privilege"] = {"escalation": declared("none", TOOL)}
    stages = {"detect": detect}
    for name in STAGES[1:]:
        stages[name] = {"status": "not-run", "reason": "not run by the job that wrote this record"}
    for name, stage in (stage_args or {}).items():
        stages[name] = stage
    now = now or datetime.datetime.now(datetime.timezone.utc)
    result = {
        "schema_version": 1,
        "kind": "result",
        "synthetic": False,
        "profile_id": profile_id,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector": dict(COLLECTOR),
        "run": sections["run"],
        "environment": sections["environment"],
        "workload": sections["workload"],
        "compute": sections["compute"],
        "stages": stages,
        "overall": overall_of(stages),
        "probe_errors": probe.errors,
    }
    return result


def secret_shape(text):
    """The label of the first secret shape that `text` matches, or None."""
    for label, shape in SECRET_SHAPES:
        if shape.search(text):
            return label
    return None


def parse_stage_args(args):
    """Turn the --stage* options into stage objects; raise ValueError on bad input."""
    def pairs(values, option):
        for value in values or []:
            name, sep, rest = value.partition("=")
            if not sep or name not in STAGES[1:]:
                raise ValueError(f"{option} takes NAME=VALUE with NAME one of {', '.join(STAGES[1:])}")
            yield name, rest

    stages = {}
    for name, status in pairs(args.stage, "--stage"):
        if status not in STATUSES:
            raise ValueError(f"--stage {name}: status must be one of {', '.join(STATUSES)}")
        stages[name] = {"status": status}
    for option, values in (("--stage-reason", args.stage_reason), ("--stage-evidence", args.stage_evidence), ("--stage-escalation", args.stage_escalation)):
        for name, value in pairs(values, option):
            if name not in stages:
                raise ValueError(f"{option} {name}: give --stage {name}=STATUS first")
            if secret_shape(value):
                raise ValueError(f"{option} {name}: looks like a {secret_shape(value)}; records are published as CI artifacts")
            if option == "--stage-reason":
                if not re.fullmatch(r"[ -~]{1,500}", value):
                    raise ValueError(f"--stage-reason {name}: printable ASCII, 1 to 500 characters")
                stages[name]["reason"] = value
            elif option == "--stage-evidence":
                if not EVIDENCE.fullmatch(value):
                    raise ValueError(f"--stage-evidence {name}: KIND:LOCATION with a known kind")
                stages[name].setdefault("evidence", []).append(value)
            else:
                if value not in ESCALATIONS:
                    raise ValueError(f"--stage-escalation {name}: one of {', '.join(ESCALATIONS)}")
                stages[name]["privilege"] = {"escalation": declared(value)}
    return stages


def plain(text):
    """Text for a terminal line, written as the validator's plain() writes it, so that no value (the output file's
    name, for one) can start a GitHub Actions workflow command or show a value shaped like a secret.
    """
    text = CONTROL.sub(" ", str(text))
    for label, shape in SECRET_SHAPES:
        match = shape.search(text)
        if match:
            text = text[:match.start()] + f"[not shown: looks like a {label}]"
    return text.replace("##[", "## [").replace("::", ": :")


def markdown_cell(value):
    """Text for one Markdown table cell: control characters become spaces, and Markdown and HTML syntax is escaped."""
    return re.sub(r"([\\`*_~\[\]()<>!|])", r"\\\1", re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value)))


def describe(fact):
    if fact.get("provenance") in ("unknown", "not-applicable"):
        return fact["provenance"]
    value = fact["value"]
    if isinstance(value, list):
        value = f"{len(value)} item(s)"
    return f"{value} ({fact['provenance']})"


def summary_lines(result):
    ci, revision = result["run"]["ci"], result["run"]["revision"]
    rows = [
        ("Profile", result["profile_id"] or "none"),
        ("Event", describe(ci["event"])),
        ("Run", describe(ci["run_url"])),
        ("Tested commit (checkout)", describe(revision["tested_sha"])),
        ("GITHUB_SHA", describe(revision["event_sha"])),
        ("Pull request head / base", f"{describe(revision['pr_head_sha'])} / {describe(revision['pr_base_sha'])}"),
    ]
    for index, layer in enumerate(result["environment"]["layers"]):
        os_facts = layer["os"]
        rows.append((f"Layer {index}", f"{describe(layer['kind'])}; {describe(layer['technology'])}; {describe(os_facts['family'])} {describe(os_facts['name'])} {describe(os_facts['version'])}; kernel {describe(os_facts['kernel'])}; {describe(layer['cpu']['arch'])}, {describe(layer['cpu']['execution'])}"))
    identity, isolation = result["workload"]["identity"], result["workload"]["isolation"]
    rows += [
        ("Workload user", f"uid {describe(identity['uid'])}, real {describe(identity['real_uid'])}, saved {describe(identity['saved_uid'])}, superuser {describe(identity['superuser'])}, initial user namespace {describe(identity['user_namespace']['initial'])}"),
        ("Isolation", f"no_new_privs {describe(isolation['no_new_privs'])}, seccomp {describe(isolation['seccomp'])}, effective capabilities {describe(isolation['capabilities']['effective'])}, permitted capabilities {describe(isolation['capabilities']['permitted'])}, AppArmor {describe(isolation['apparmor']['enabled'])}, SELinux {describe(isolation['selinux']['mode'])}, cgroup {describe(isolation['cgroup']['version'])}"),
    ]
    for name, runtime in result["workload"]["runtimes"].items():
        rows.append((name.capitalize(), ", ".join(f"{key} {describe(fact)}" for key, fact in runtime.items())))
    rows.append(("Stages", ", ".join(f"{name} {stage['status']}" for name, stage in result["stages"].items())))
    rows.append(("Overall", result["overall"]))
    rows.append(("Probe errors", "; ".join(f"{error['probe']}: {error['error']}" for error in result["probe_errors"]) or "none"))
    lines = ["### Compatibility record", "", "| Field | Value |", "|---|---|"]
    lines += [f"| {markdown_cell(name)} | {markdown_cell(value)} |" for name, value in rows]
    lines += ["", "Recorded by scripts/ci/collect_environment.py. Overall is incomplete while stages after detect are not run; see docs/CI-MATRIX.md.", ""]
    return lines


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
    parser.add_argument("--output", required=True, type=pathlib.Path, help="where to write the result JSON")
    parser.add_argument("--profile-id", help="id of the profile this lane declares")
    parser.add_argument("--summary", type=pathlib.Path, help="append a Markdown summary to this file")
    parser.add_argument("--no-node", action="store_true", help="do not run node")
    parser.add_argument("--stage", action="append", metavar="NAME=STATUS")
    parser.add_argument("--stage-reason", action="append", metavar="NAME=TEXT")
    parser.add_argument("--stage-evidence", action="append", metavar="NAME=REF")
    parser.add_argument("--stage-escalation", action="append", metavar="NAME=VALUE")
    args = parser.parse_args(argv)
    if args.profile_id is not None and not SLUG.fullmatch(args.profile_id):
        parser.error("--profile-id must be a lower-case identifier")
    try:
        stage_args = parse_stage_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    result = build_result(Probe(probe_node=not args.no_node), args.profile_id, stage_args)
    text = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
    if secret_shape(text):
        print(f"error: the record holds a value that looks like a {secret_shape(text)}; it was not written", file=sys.stderr)
        return 2
    try:
        args.output.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write the record: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.summary:
        # Checked once the record exists, so that a link or another spelling of its name is found too.
        if same_file(args.summary, args.output):
            print("error: the record was written; --summary names the same file, so no summary was appended to it", file=sys.stderr)
            return 2
        try:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write("\n".join(summary_lines(result)))
        except OSError as exc:
            print(f"error: the record was written, but the summary could not be: {type(exc).__name__}", file=sys.stderr)
            return 2
    detect = result["stages"]["detect"]["status"]
    print(plain(f"{args.output.name}: detect {detect}, overall {result['overall']}, {len(result['probe_errors'])} probe error(s)"))
    for error in result["probe_errors"]:
        print(plain(f"  probe error: {error['probe']}: {error['error']}"))
    return 0 if detect == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
