"""Record the environment this process runs in as a compatibility result (docs/CI-MATRIX.md).

The collector reads the following, and nothing else:

- /proc, /sys, /etc/os-release or /usr/lib/os-release, /run/.containerenv
  and /run/systemd/container, and whether /.dockerenv, /.singularity.d and
  /run/systemd/system exist.
- The first 20 bytes of the interpreter's executable (its ELF header), when the
  build architecture is not otherwise known.
- On macOS, these sysctl values: kern.hv_vmm_present, hw.model,
  kern.osproductversion, kern.osrelease, hw.optional.arm64, hw.optional.x86_64
  and sysctl.proc_translated.
- On Windows, platform.version(), the native machine IsWow64Process2 names,
  the SystemManufacturer and SystemProductName values under
  HKLM\\HARDWARE\\DESCRIPTION\\System\\BIOS, and whether this process's token
  is elevated, holds the Administrators group enabled, or is LocalSystem.
- The environment variables in ENV_ALLOWLIST.
- The pull request head and base in the GitHub Actions event file.
- The checkout's .git/HEAD, and the loose ref or packed-refs line it points to.
- The first 4 KiB that `node -p` writes, run with an empty environment (on
  Windows, SystemRoot only) and stopped after 30 seconds. node is looked for in
  the PATH directories only, so on Windows not in the current directory unless
  PATH names it, also when PATH is unset, and there only as node.exe, whatever
  PATHEXT lists.
- The JSON object files that --extension names, which go into the record's
  extensions unchanged.

Files are read only when they are regular files, and are opened so that a FIFO
cannot block the probe.

It uses no network, never escalates privileges, and writes only the output file
and the optional summary. It records no host name, account name, home
directory, network address or other environment variable. It refuses --stage
values, and does not write a record, that look like a token or a private key.

On Linux it fills in the layers, identity and isolation facts. On macOS and
Windows it records the OS, the machine's architecture, whether the system runs
in a VM, whether each runtime is emulated, and who the workload runs as (on
Windows, whether its token is an administrator's); the Linux isolation
interfaces are not-applicable there. On other systems it records the OS
family, the user and group IDs on FreeBSD (os.getresuid), the Python and Node
runtimes and the run, and leaves the layer kind and the isolation facts
unknown, so the least-privilege policy fails there until a probe measures them.

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

Inside a container the engine's own facts (rootless or not, privileged,
devices) are not visible. On the host, --amend-container RECORD
--podman-container NAME runs `podman info` and `podman container inspect NAME`,
found in the PATH directories and run with this process's environment, which
rootless Podman needs to find its storage, and puts those three facts into the
innermost layer of a record that was taken inside that running container. It
changes nothing else in the record, and writes nothing if podman fails.

Standard library only. Usage, from the repository root:
    python -I scripts/ci/collect_environment.py --output result.json
        [--profile-id ID] [--summary FILE] [--no-node]
        [--stage NAME=STATUS] [--stage-reason NAME=TEXT]
        [--stage-evidence NAME=REF] [--stage-escalation NAME=VALUE]
        [--extension x-NAME=PATH]
    python -I scripts/ci/collect_environment.py --amend-container RECORD
        --podman-container NAME

The --stage options record what other steps of the job did in the stages after
detect. Every stage they do not name is not-run.

Exit status:
- 0: the record was written and detection passed.
- 1: the record was written but a probe failed.
- 2: a usage error, or the record or the summary could not be written. The
  record is written first, and the message says which one failed. A record
  that looks like it holds a secret is not written. With --amend-container,
  any failure, and the record is left as it was.
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
# A character git allows in a ref: none of space, "~", "^", ":", "?", "*", "[" or "\", and none of the characters
# CONTROL refuses in any value, taken from CONTROL itself.
REF_CHAR = "[^ ~^:?*\\[\\\\" + CONTROL.pattern[1:]
# GITHUB_REF, and the ref .git/HEAD names: actions/checkout checks a branch out by its name, so a run from a branch
# named with any of these characters records its ref, if it is ASCII or the file system encoding is UTF-8, and its
# commit unless checkout_sha does not follow that name. Within the schema's 200 characters.
GIT_REF = rf"(?=.{{1,200}}\Z)refs/{REF_CHAR}+"
# GITHUB_WORKFLOW_REF is <owner>/<repository>/<path>@<ref>, and the validator links a run to its profile by it, so it
# is recorded for any of those refs. The whole value stays within the schema's 200 characters.
WORKFLOW_REF = rf"(?=.{{1,200}}\Z)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]{{1,150}}@refs/{REF_CHAR}+"
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
# The image file machine types IsWow64Process2 reports for the machine that runs Windows.
WINDOWS_MACHINES = {0x014C: "x86", 0x01C4: "armv7", 0x8664: "x86_64", 0xAA64: "aarch64"}
WINDOWS_BIOS_KEY = "HARDWARE\\DESCRIPTION\\System\\BIOS"
WINDOWS_BIOS_EVIDENCE = "python:winreg:HKLM\\HARDWARE\\DESCRIPTION\\System\\BIOS"
# hw.model prefixes of macOS virtual machines.
MACOS_MODELS = (("VirtualMac", "apple-virtualization"), ("VMware", "vmware"), ("Parallels", "parallels"))
# How a record names a device an engine passes in: the path under /dev, with "/" as "-" and in lower case.
DEVICE = re.compile(r"/dev/([A-Za-z0-9][A-Za-z0-9._+/-]{0,62})")
EXTENSION_NAME = re.compile(r"x-[a-z0-9][a-z0-9-]{0,62}")
EXTENSION_LIMIT = 1024 * 1024
RECORD_LIMIT = 8 * 1024 * 1024
PODMAN_TIMEOUT = 120
PODMAN_OUTPUT_LIMIT = 8 * 1024 * 1024


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

    On Windows shutil.which also looks in the current directory first, which the path does not name, and tries
    each extension PATHEXT lists, which the environment sets, so a node.com or node.bat could run in place of
    node. There each directory is looked in by itself for `name` with the .exe extension only, and an empty
    entry names no directory."""
    if os.name != "nt":
        return shutil.which(name, path=search_path)
    for directory in search_path.split(os.pathsep):
        candidate = os.path.join(directory, name + ".exe")
        if directory and os.path.isfile(candidate):
            return candidate
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


class NativeQueries:
    """Reads that need an operating system's own interface, through ctypes and winreg.

    On macOS, sysctlbyname(3); on Windows, the process token, IsWow64Process2 and two
    BIOS values in the registry. Each method returns None when the interface is
    missing or the call fails, so the fact it feeds stays unknown. Nothing here
    writes, starts a process or changes privileges. Tests replace this class.
    """

    def sysctl(self, name, integer):
        """A sysctl value: an int when `integer`, else a string of at most 255 bytes."""
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.sysctlbyname
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        function.restype = ctypes.c_int
        size = ctypes.c_size_t(0)
        if function(name.encode("ascii"), None, ctypes.byref(size), None, 0) != 0 or not 0 < size.value <= 256:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if function(name.encode("ascii"), buffer, ctypes.byref(size), None, 0) != 0:
            return None
        raw = buffer.raw[:size.value]
        if integer:
            return int.from_bytes(raw, sys.byteorder) if len(raw) in (4, 8) else None
        text = raw.split(b"\0", 1)[0]
        return text.decode("ascii") if text.isascii() else None

    def windows_native_machine(self):
        """The image file machine type of the machine that runs Windows, from IsWow64Process2 (Windows 10 1709 and later)."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = getattr(kernel32, "IsWow64Process2", None)
        if function is None:
            return None
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
        function.restype = wintypes.BOOL
        process, native = wintypes.USHORT(), wintypes.USHORT()
        if not function(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return None
        return native.value

    def windows_token(self):
        """Whether this process's token is elevated, holds the Administrators group enabled, or is LocalSystem.

        Each value is True, False, or None when it could not be read. CheckTokenMembership counts an
        enabled group only, so the deny-only Administrators group of a UAC-filtered token is not a membership.
        """
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.CreateWellKnownSid.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        advapi32.CreateWellKnownSid.restype = wintypes.BOOL
        advapi32.CheckTokenMembership.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL)]
        advapi32.CheckTokenMembership.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        advapi32.EqualSid.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return None

        def well_known(kind):
            size = wintypes.DWORD(68)  # SECURITY_MAX_SID_SIZE
            sid = ctypes.create_string_buffer(68)
            return sid if advapi32.CreateWellKnownSid(kind, None, sid, ctypes.byref(size)) else None

        try:
            elevation, size = wintypes.DWORD(), wintypes.DWORD()
            # TokenElevation (20) gives TOKEN_ELEVATION, whose only member is TokenIsElevated.
            elevated = bool(elevation.value) if advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)) else None
            administrators, member = well_known(26), wintypes.BOOL()  # WinBuiltinAdministratorsSid
            # A NULL token checks this thread's effective token, which is the process token here.
            in_administrators = bool(member.value) if administrators is not None and advapi32.CheckTokenMembership(None, administrators, ctypes.byref(member)) else None
            local_system, system_sid = None, well_known(22)  # WinLocalSystemSid
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser (1): asks for the size
            if system_sid is not None and 0 < size.value <= 1024:
                buffer = ctypes.create_string_buffer(size.value)
                if advapi32.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                    # TOKEN_USER starts with SID_AND_ATTRIBUTES, which starts with the pointer to the user's SID.
                    local_system = bool(advapi32.EqualSid(ctypes.c_void_p.from_buffer(buffer).value, system_sid))
        finally:
            kernel32.CloseHandle(token)
        return {"elevated": elevated, "administrators": in_administrators, "local_system": local_system}

    def windows_bios(self):
        """SystemManufacturer and SystemProductName under HKLM\\HARDWARE\\DESCRIPTION\\System\\BIOS, which Windows fills from SMBIOS."""
        import winreg

        values = {}
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_BIOS_KEY) as key:
                for name in ("SystemManufacturer", "SystemProductName"):
                    try:
                        value, kind = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if kind == winreg.REG_SZ and isinstance(value, str):
                        values[name] = value[:256]
        except OSError:
            return None
        return values


class Probe:
    """Reads one system. `root`, `env`, `git_dir` and `native` exist so tests can point it at a made-up one."""

    def __init__(self, root="/", env=None, git_dir=None, system=None, probe_node=True, native=None):
        self.root = pathlib.Path(root)
        self._env = os.environ if env is None else env
        self.git_dir = pathlib.Path(git_dir) if git_dir else ROOT / ".git"
        self.system = system or platform.system()
        self.probe_node = probe_node
        self.native = native or NativeQueries()
        self._hardware = None
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
        if self.system == "Darwin":
            return self.with_host(self.macos_layer())
        if self.system == "Windows":
            return self.with_host(self.windows_layer())
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

    # -- macOS and Windows -------------------------------------------------

    def hardware_arch(self):
        """(architecture of the machine, evidence) on macOS and Windows, or (None, None).

        Both are read from the system, not from this process, so an emulated process
        still names the machine: on macOS hw.optional.arm64 is 1 on Apple silicon,
        also to a process that Rosetta 2 translates; on Windows IsWow64Process2
        names the native machine, also to an x64 process emulated on Arm.
        """
        if self._hardware is None:
            arch, evidence = None, None
            if self.system == "Darwin":
                if self.native.sysctl("hw.optional.arm64", True) == 1:
                    arch, evidence = "aarch64", "python:sysctlbyname(hw.optional.arm64)"
                elif self.native.sysctl("hw.optional.x86_64", True) == 1:
                    arch, evidence = "x86_64", "python:sysctlbyname(hw.optional.x86_64)"
            elif self.system == "Windows":
                arch = WINDOWS_MACHINES.get(self.native.windows_native_machine())
                evidence = "python:IsWow64Process2#nativeMachine" if arch else None
            self._hardware = (arch, evidence)
        return self._hardware

    def hardware_execution(self, arch, arch_evidence):
        """native or emulated, comparing a program's architecture with the machine's (macOS and Windows)."""
        hardware, evidence = self.hardware_arch()
        if arch is None:
            return unknown("the architecture is not known")
        if hardware is None:
            return unknown("the machine's architecture could not be read")
        arch_evidence = [arch_evidence] if isinstance(arch_evidence, str) else list(arch_evidence)
        if arch == hardware or (arch, hardware) in COMPATIBLE:
            return inferred("native", *arch_evidence, evidence)
        if {arch, hardware} <= KNOWN_ARCHES:
            # Neither system runs another architecture's code except through an emulator (Rosetta 2, or Windows on Arm's x64 emulation).
            return inferred("emulated", *arch_evidence, evidence)
        return unknown(f"{arch} on a {hardware} machine is outside the documented architectures")

    def with_host(self, layer):
        """The layer, after an inferred physical host when the layer is a VM, as on Linux."""
        layer = order_layer(layer)
        if known_kind(layer) != "vm":
            return [layer]
        host_reason = "a guest cannot see the physical host"
        return [order_layer({
            "kind": inferred("host", *layer["kind"]["evidence"]),
            "technology": not_applicable("the outermost host runs on hardware"),
            "os": {"family": unknown(host_reason), "name": unknown(host_reason), "version": unknown(host_reason), "kernel": unknown(host_reason)},
            "cpu": {"arch": unknown(host_reason), "execution": unknown(host_reason)},
            "service_manager": {"name": unknown(host_reason), "scope": unknown(host_reason)},
        }), layer]

    def machine_cpu(self):
        arch, evidence = self.hardware_arch()
        return {
            "arch": observed(arch, evidence) if arch else unknown("the machine's architecture could not be read"),
            "execution": unknown("whether the layer below emulates this CPU is not visible from inside it"),
        }

    def macos_layer(self):
        vmm = self.native.sysctl("kern.hv_vmm_present", True)
        model = self.native.sysctl("hw.model", False)
        layer = {}
        if vmm == 1:
            layer["kind"] = inferred("vm", "python:sysctlbyname(kern.hv_vmm_present)")
            named = [technology for prefix, technology in MACOS_MODELS if isinstance(model, str) and model.startswith(prefix)]
            layer["technology"] = inferred(named[0], "python:sysctlbyname(hw.model)") if named else unknown("hw.model names no known hypervisor")
        else:
            reason = "kern.hv_vmm_present is 0: no hypervisor signal; that does not show bare metal" if vmm == 0 else "kern.hv_vmm_present cannot be read"
            layer["kind"] = unknown(reason)
            layer["technology"] = unknown(reason)
        version = self.native.sysctl("kern.osproductversion", False)
        release = self.native.sysctl("kern.osrelease", False)
        layer["os"] = {
            "family": observed("macos", "python:platform.system"),
            "name": observed("macos", "python:platform.system"),
            "version": observed(version, "python:sysctlbyname(kern.osproductversion)") if isinstance(version, str) and re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,4}){0,2}", version) else unknown("kern.osproductversion gives no usable version"),
            "kernel": observed(release, "python:sysctlbyname(kern.osrelease)") if isinstance(release, str) and PRINTABLE.fullmatch(release) else unknown("kern.osrelease gives no usable release"),
        }
        layer["cpu"] = self.machine_cpu()
        reason = "the service manager is probed on Linux only"
        layer["service_manager"] = {"name": unknown(reason), "scope": unknown(reason)}
        return layer

    def windows_layer(self):
        bios = self.native.windows_bios()
        layer = {}
        vendor, product = (bios or {}).get("SystemManufacturer", ""), (bios or {}).get("SystemProductName", "")
        named = None
        for name, text in (("SystemManufacturer", vendor), ("SystemProductName", product)):
            for prefix, technology in DMI_VENDORS:
                if named is None and text.startswith(prefix):
                    named = technology, f"{WINDOWS_BIOS_EVIDENCE}#{name}"
        if named is None and (vendor, product) == ("Microsoft Corporation", "Virtual Machine"):
            # Hyper-V, Azure among it, as systemd-detect-virt reads the same SMBIOS values.
            named = "hyper-v", f"{WINDOWS_BIOS_EVIDENCE}#SystemProductName"
        if named:
            layer["kind"] = inferred("vm", named[1])
            layer["technology"] = inferred(named[0], named[1])
        else:
            reason = "the BIOS values cannot be read" if bios is None else "the BIOS values name no known hypervisor; that does not show bare metal"
            layer["kind"] = unknown(reason)
            layer["technology"] = unknown(reason)
        version = platform.version()
        version_fact = observed(version, "python:platform.version") if re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}", version or "") else unknown("platform.version() gives no usable version")
        layer["os"] = {
            "family": observed("windows", "python:platform.system"),
            "name": observed("windows", "python:platform.system"),
            "version": version_fact,
            # The NT kernel carries the build of the system it ships with.
            "kernel": json.loads(json.dumps(version_fact)),
        }
        layer["cpu"] = self.machine_cpu()
        reason = "the service manager is probed on Linux only"
        layer["service_manager"] = {"name": unknown(reason), "scope": unknown(reason)}
        return layer

    def windows_superuser(self):
        """Whether the process holds an elevated token, the enabled Administrators group, or runs as LocalSystem."""
        token = self.native.windows_token()
        if token is None:
            return unknown("the process token could not be opened")
        evidence = {
            "elevated": "python:GetTokenInformation(TokenElevation)",
            "administrators": "python:CheckTokenMembership(BUILTIN\\Administrators)",
            "local_system": "python:GetTokenInformation(TokenUser)",
        }
        held = [evidence[name] for name in evidence if token.get(name) is True]
        if held:
            return observed(True, *held)
        if all(token.get(name) is False for name in evidence):
            return observed(False, *evidence.values())
        return unknown("part of the process token could not be read, and what was read shows no administrator")

    def macos_execution(self, arch, arch_evidence):
        """sysctl.proc_translated is 1 in a process Rosetta 2 translates and 0 in a native one; Intel Macs, which have no Rosetta, lack it."""
        translated = self.native.sysctl("sysctl.proc_translated", True)
        arch_evidence = [arch_evidence] if isinstance(arch_evidence, str) else list(arch_evidence)
        if translated in (0, 1) and arch is not None:
            return inferred("emulated" if translated else "native", *arch_evidence, "python:sysctlbyname(sysctl.proc_translated)")
        return self.hardware_execution(arch, arch_evidence)

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
            # The superuser on Windows holds an elevated token, the Administrators group, or is LocalSystem (docs/CI-MATRIX.md).
            reason = "Windows has no POSIX user or group IDs"
            namespace = "user namespaces are a Linux interface"
            return {
                "superuser": self.windows_superuser(),
                "uid": not_applicable(reason), "real_uid": not_applicable(reason), "saved_uid": not_applicable(reason), "gid": not_applicable(reason),
                "user_namespace": {"initial": not_applicable(namespace), "uid_map": not_applicable(namespace)},
            }
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
        if self.system in ("Darwin", "Windows"):
            return isolation_not_applicable(f"a Linux interface; not applicable on {'macOS' if self.system == 'Darwin' else 'Windows'}")
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
        # The kernel writes Y or N and a newline; anything else, such as a file that was replaced or a longer
        # value cut at 16 bytes, says nothing.
        if text not in ("Y\n", "N\n"):
            reason = "/sys/module/apparmor/parameters/enabled is not Y or N and a newline, as the kernel writes it"
            return {"enabled": unknown(reason), "profile": unknown(reason)}
        if text == "N\n":
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
            "execution": self.execution(arch, arch_evidence, reference, python=True),
        }

    def execution(self, arch, arch_evidence, reference, python=False):
        if self.system == "Linux":
            return execution_fact(arch, arch_evidence, reference)
        if self.system == "Darwin":
            # Rosetta 2 tells a process whether it translates that process; node runs in another one.
            return self.macos_execution(arch, arch_evidence) if python else self.hardware_execution(arch, arch_evidence)
        if self.system == "Windows":
            return self.hardware_execution(arch, arch_evidence)
        return unknown("compared on Linux, macOS and Windows only")

    def node(self, reference):
        def all_unknown(reason):
            return {name: unknown(reason) for name in ("version", "modules_abi", "napi_version", "platform", "arch", "execution")}

        if not self.probe_node:
            return all_unknown("not probed (--no-node)")
        # An unset PATH searches the default directories, but not the current directory that Windows lists first
        # in them; an empty one finds nothing.
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
            "execution": self.execution(arch, arch_evidence, reference),
        }

    def run(self):
        system = self.env("GITHUB_ACTIONS") == "true"
        na = "not running in GitHub Actions"

        def gha(name, pattern):
            return self.env_value(name, pattern) if system else not_applicable(na)

        def gha_ref(name, pattern):
            # Python decodes the environment in the file system encoding, and git names refs in UTF-8: under another
            # encoding a ref that is not ASCII would be recorded as other characters.
            fact = gha(name, pattern)
            if fact["value"] is not None and not fact["value"].isascii() and sys.getfilesystemencoding() != "utf-8":
                return unknown(f"{name} is not ASCII, and the file system encoding is not UTF-8", f"env:{name}")
            return fact

        ci = {
            "system": observed("github-actions", "env:GITHUB_ACTIONS") if system else inferred("local", "env:GITHUB_ACTIONS"),
            "event": gha("GITHUB_EVENT_NAME", r"[a-z_]{1,64}"),
            "repository": gha("GITHUB_REPOSITORY", r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}"),
            "ref": gha_ref("GITHUB_REF", GIT_REF),
            "run_id": gha("GITHUB_RUN_ID", r"[0-9]{1,20}"),
            "run_attempt": gha("GITHUB_RUN_ATTEMPT", r"[0-9]{1,10}"),
            "run_url": not_applicable(na),
            "workflow_ref": gha_ref("GITHUB_WORKFLOW_REF", WORKFLOW_REF),
            "job": gha("GITHUB_JOB", r"[A-Za-z0-9_-]{1,100}"),
            "runner_environment": gha("RUNNER_ENVIRONMENT", r"github-hosted|self-hosted"),
            "runner_os": gha("RUNNER_OS", r"[A-Za-z0-9]{1,40}"),
            "runner_arch": gha("RUNNER_ARCH", r"[A-Za-z0-9]{1,40}"),
            "image_os": gha("ImageOS", r"[A-Za-z0-9._-]{1,40}"),
            "image_version": gha("ImageVersion", r"[A-Za-z0-9._-]{1,40}"),
        }
        if ci["run_attempt"]["value"] is not None:
            ci["run_attempt"]["value"] = int(ci["run_attempt"]["value"])
            # Up to the schema's largest integer, as the validator reads the attempt of a run URL.
            if ci["run_attempt"]["value"] > 4294967295:
                ci["run_attempt"] = unknown("GITHUB_RUN_ATTEMPT does not have the expected form", "env:GITHUB_RUN_ATTEMPT")
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
        # Only ASCII blanks: a ref may end in another character str.strip() removes, such as U+3000.
        head = head.strip(" \t\r\n")
        if SHA.fullmatch(head):
            return observed(head, "git:.git/HEAD")
        ref = head[len("ref: "):] if head.startswith("ref: ") else None
        # The ref is read as a path under .git, so it is followed only as git would name a ref: no ".." or "@{", and
        # no part that is empty, starts with ".", or ends with ".lock" or "." (which Windows drops from a file name).
        if ref is None or not re.fullmatch(GIT_REF, ref) or ".." in ref or "@{" in ref or any(not part or part.startswith(".") or part.endswith((".lock", ".")) for part in ref.split("/")):
            return unknown(".git/HEAD has an unexpected form")
        # Git names a ref's file in UTF-8, and the path is written in the file system encoding: under another encoding
        # a name that is not ASCII could open another branch's file, and packed-refs alone could hold an older commit.
        if not ref.isascii() and sys.getfilesystemencoding() != "utf-8":
            return unknown("a ref that is not ASCII is followed only when the file system encoding is UTF-8")
        # packed-refs is read only when there is no loose ref: git writes an update as a loose ref, so a loose ref that
        # cannot be read, or holds no commit ID (such as a symbolic ref), may be newer than the packed one.
        loose, reason = self.read_git(ref, absent_ok=True)
        if reason is not None:
            return unknown(reason)
        if loose is not None:
            if not SHA.fullmatch(loose.strip()):
                return unknown(f".git/{ref} does not hold a commit ID")
            # An evidence reference is printable ASCII, so a loose ref with another name is cited by .git/HEAD, which
            # names it.
            return observed(loose.strip(), f"git:.git/{ref}" if ref.isascii() else "git:.git/HEAD")
        packed, _ = self.read_git("packed-refs")
        # Lines end at "\n" only: str.splitlines() also ends one at characters a ref name may hold, such as U+2028.
        for line in (packed or "").split("\n"):
            sha, _, name = line.removesuffix("\r").partition(" ")
            if name == ref and SHA.fullmatch(sha):
                return observed(sha, "git:.git/packed-refs")
        return unknown(f"{ref} is not resolved in .git")

    def read_git(self, name, absent_ok=False):
        """(text, None), or (None, reason); with absent_ok, (None, None) when the file does not exist."""
        try:
            if not self.git_dir.is_dir():
                return None, ".git is not a directory (no checkout, or a worktree)"
            # A byte that is not UTF-8 becomes a lone surrogate, which no ref the collector follows holds; "replace"
            # would turn it into U+FFFD, the name of another branch.
            return read_regular(self.git_dir / name, 1 << 20).decode("utf-8", "surrogateescape"), None
        except (OSError, ValueError) as exc:
            if absent_ok and isinstance(exc, (FileNotFoundError, NotADirectoryError)):
                return None, None
            # ValueError: a name the file system encoding cannot write, which checkout_sha does not follow.
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


def isolation_not_applicable(reason):
    return {
        "no_new_privs": not_applicable(reason),
        "capabilities": {"effective": not_applicable(reason), "permitted": not_applicable(reason), "bounding": not_applicable(reason)},
        "seccomp": not_applicable(reason),
        "apparmor": {"enabled": not_applicable(reason), "profile": not_applicable(reason)},
        "selinux": {"mode": not_applicable(reason), "context": not_applicable(reason)},
        "cgroup": {"version": not_applicable(reason), "delegated": not_applicable(reason)},
        "pid_namespace": {"initial": not_applicable(reason)},
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


def read_extensions(values):
    """{x-name: JSON object} from --extension x-NAME=PATH options; raise ValueError on bad input."""
    extensions = {}
    for value in values or []:
        name, sep, path = value.partition("=")
        if not sep or not EXTENSION_NAME.fullmatch(name) or not path:
            raise ValueError("--extension takes x-NAME=PATH, with NAME in lower case")
        if name in extensions:
            raise ValueError(f"--extension {name} is given twice")
        try:
            data = read_regular(path, EXTENSION_LIMIT + 1)
        except OSError as exc:
            raise ValueError(f"--extension {name}: cannot read the file ({type(exc).__name__})") from None
        if len(data) > EXTENSION_LIMIT:
            raise ValueError(f"--extension {name}: the file is larger than {EXTENSION_LIMIT} bytes")
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError(f"--extension {name}: not a UTF-8 JSON document") from None
        if not isinstance(document, dict):
            raise ValueError(f"--extension {name}: not a JSON object")
        extensions[name] = document
    return extensions


def run_podman(executable, args):
    """The JSON a podman command prints; raise ProbeError when it fails, times out, or prints too much."""
    try:
        process = subprocess.run([executable, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=PODMAN_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"podman {args[0]} could not be run ({type(exc).__name__})") from None
    if process.returncode != 0:
        raise ProbeError(f"podman {' '.join(args[:2])} exited with status {process.returncode}")
    if len(process.stdout) > PODMAN_OUTPUT_LIMIT:
        raise ProbeError(f"podman {args[0]} printed more than {PODMAN_OUTPUT_LIMIT} bytes")
    try:
        return json.loads(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProbeError(f"podman {args[0]} did not print JSON") from None


def device_names(devices):
    """Record names for HostConfig.Devices entries, or None when one cannot be named."""
    names = []
    for device in devices:
        path = device.get("PathInContainer") if isinstance(device, dict) else None
        match = DEVICE.fullmatch(path) if isinstance(path, str) else None
        name = match.group(1).replace("/", "-").lower() if match else None
        if name is None or not SLUG.fullmatch(name):
            return None
        names.append(name)
    return sorted(set(names))


def engine_facts(info, inspect, container):
    """The engine-side container facts from `podman info` and `podman container inspect` output; raise ProbeError."""
    rootless = info.get("host", {}).get("security", {}).get("rootless") if isinstance(info, dict) else None
    if not isinstance(rootless, bool):
        raise ProbeError("podman info has no boolean host.security.rootless")
    if not isinstance(inspect, list) or len(inspect) != 1 or not isinstance(inspect[0], dict):
        raise ProbeError(f"podman container inspect does not describe exactly one container named {container}")
    entry = inspect[0]
    if entry.get("Name") != container:
        raise ProbeError(f"podman container inspect names another container than {container}")
    if not isinstance(entry.get("State"), dict) or entry["State"].get("Running") is not True:
        raise ProbeError(f"container {container} is not running, so it may not be the one the record was taken in")
    host = entry.get("HostConfig")
    if not isinstance(host, dict) or not isinstance(host.get("Privileged"), bool):
        raise ProbeError("podman container inspect has no boolean HostConfig.Privileged")
    devices = host.get("Devices")
    if devices is not None and not isinstance(devices, list):
        raise ProbeError("podman container inspect has a HostConfig.Devices that is not a list")
    names = device_names(devices or [])
    inspect_ref = "cmd:podman container inspect"
    return {
        "engine_mode": observed("rootless" if rootless else "rootful", "cmd:podman info#host.security.rootless"),
        "privileged": observed(host["Privileged"], f"{inspect_ref}#HostConfig.Privileged"),
        "devices": observed(names, f"{inspect_ref}#HostConfig.Devices") if names is not None else unknown("a device path does not have the expected form", f"{inspect_ref}#HostConfig.Devices"),
    }


def amend_container(record, info, inspect, container):
    """Put the engine-side facts into the innermost layer of a record taken inside that container."""
    if not isinstance(record, dict) or record.get("kind") != "result" or record.get("collector") != COLLECTOR:
        raise ProbeError("the file is not a result this collector wrote")
    layers = record.get("environment", {}).get("layers")
    if not isinstance(layers, list) or not layers or not isinstance(layers[-1], dict) or known_kind(layers[-1]) != "container" or not isinstance(layers[-1].get("container"), dict):
        raise ProbeError("the record's innermost layer is not a container")
    layers[-1]["container"].update(engine_facts(info, inspect, container))
    return record


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
    return re.sub(r"([\\`*_~\[\]()<>!|&])", r"\\\1", re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value)))


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
    parser.add_argument("--output", type=pathlib.Path, help="where to write the result JSON")
    parser.add_argument("--extension", action="append", metavar="x-NAME=PATH", help="add a JSON object file to the record's extensions")
    parser.add_argument("--amend-container", type=pathlib.Path, metavar="RECORD", help="on the host, add Podman's facts about --podman-container to a record taken inside it, and rewrite it")
    parser.add_argument("--podman-container", metavar="NAME", help="the running container that --amend-container describes")
    parser.add_argument("--profile-id", help="id of the profile this lane declares")
    parser.add_argument("--summary", type=pathlib.Path, help="append a Markdown summary to this file")
    parser.add_argument("--no-node", action="store_true", help="do not run node")
    parser.add_argument("--stage", action="append", metavar="NAME=STATUS")
    parser.add_argument("--stage-reason", action="append", metavar="NAME=TEXT")
    parser.add_argument("--stage-evidence", action="append", metavar="NAME=REF")
    parser.add_argument("--stage-escalation", action="append", metavar="NAME=VALUE")
    args = parser.parse_args(argv)
    if args.amend_container is not None:
        return amend_main(parser, args)
    if args.output is None:
        parser.error("--output is required")
    if args.podman_container is not None:
        parser.error("--podman-container goes with --amend-container")
    if args.profile_id is not None and not SLUG.fullmatch(args.profile_id):
        parser.error("--profile-id must be a lower-case identifier")
    try:
        stage_args = parse_stage_args(args)
        extensions = read_extensions(args.extension)
    except ValueError as exc:
        parser.error(str(exc))

    result = build_result(Probe(probe_node=not args.no_node), args.profile_id, stage_args)
    if extensions:
        result["extensions"] = extensions
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


def amend_main(parser, args):
    others = [name for name in ("output", "profile_id", "summary", "stage", "stage_reason", "stage_evidence", "stage_escalation", "extension") if getattr(args, name)]
    if others or args.no_node:
        parser.error("--amend-container takes --podman-container only")
    if args.podman_container is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.podman_container):
        parser.error("--amend-container needs --podman-container NAME, a container name")
    # podman is found as node is, in the PATH directories only, and PATH is read through Probe.env as node's is.
    search_path = Probe(probe_node=False).env("PATH") or os.pathsep.join(entry for entry in os.defpath.split(os.pathsep) if entry != os.curdir)
    executable = find_on_path("podman", search_path)
    try:
        if executable is None:
            raise ProbeError("podman is not on PATH")
        record = json.loads(read_regular(args.amend_container, RECORD_LIMIT).decode("utf-8"))
        info = run_podman(executable, ["info", "--format", "json"])
        inspect = run_podman(executable, ["container", "inspect", "--format", "json", args.podman_container])
        record = amend_container(record, info, inspect, args.podman_container)
    except (OSError, UnicodeDecodeError, ValueError, ProbeError) as exc:
        message = str(exc) if isinstance(exc, ProbeError) else f"cannot read the record ({type(exc).__name__})"
        print(plain(f"error: {message}; the record was not changed"), file=sys.stderr)
        return 2
    text = json.dumps(record, indent=2, ensure_ascii=True) + "\n"
    if secret_shape(text):
        print(f"error: the record would hold a value that looks like a {secret_shape(text)}; it was not changed", file=sys.stderr)
        return 2
    try:
        args.amend_container.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write the record: {type(exc).__name__}", file=sys.stderr)
        return 2
    container = record["environment"]["layers"][-1]["container"]
    print(plain(f"{args.amend_container.name}: engine {describe(container['engine_mode'])}, privileged {describe(container['privileged'])}, devices {describe(container['devices'])}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
