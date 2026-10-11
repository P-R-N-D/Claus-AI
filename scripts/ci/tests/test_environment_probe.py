"""Tests for scripts/ci/collect_environment.py.

Most tests point the probe at a made-up root directory, so they describe
machines the test does not run on: a GitHub-hosted runner VM, a container in a
VM, a rootless Podman container, WSL2, an emulated process, a Kubernetes pod.
Other tests run the collector on this machine in a subprocess, with secret-like
environment variables set, and check that none of them reach the record. No
Django and no third-party package is needed. From the repository root:
    python -I scripts/ci/tests/test_environment_probe.py --verbose
"""

import ast
import contextlib
import importlib.util
import io
import itertools
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "collect_environment.py"
VALIDATOR = ROOT / "scripts" / "ci" / "validate_compatibility.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load("collect_environment", SCRIPT)
validator = load("validate_compatibility", VALIDATOR)
SCHEMA = validator.load_schema()

FULL = "000001ffffffffff"
CPUINFO_X86 = "processor\t: 0\nvendor_id\t: AuthenticAMD\nflags\t\t: fpu vme sse sse2 {hypervisor}\n\nprocessor\t: 1\n"
CPUINFO_ARM = "processor\t: 0\nBogoMIPS\t: 50.00\nFeatures\t: fp asimd\nCPU implementer\t: 0x41\n"


def status(uid=1001, gid=118, cap_eff="0000000000000000", cap_bnd=FULL, no_new_privs=0, seccomp=0, real_uid=None, saved_uid=None, cap_prm=None):
    real_uid = uid if real_uid is None else real_uid
    saved_uid = uid if saved_uid is None else saved_uid
    cap_prm = cap_eff if cap_prm is None else cap_prm
    return (
        f"Name:\tpython\nUid:\t{real_uid}\t{uid}\t{saved_uid}\t{uid}\nGid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
        f"CapInh:\t0000000000000000\nCapPrm:\t{cap_prm}\nCapEff:\t{cap_eff}\nCapBnd:\t{cap_bnd}\n"
        f"CapAmb:\t0000000000000000\nNoNewPrivs:\t{no_new_privs}\nSeccomp:\t{seccomp}\n"
    )


def mountinfo(*mounts):
    lines = ["22 1 8:1 / / rw,relatime - ext4 /dev/root rw"]
    lines += [f"{30 + index} 22 0:{30 + index} / {point} rw,nosuid - {fstype} {fstype} rw" for index, (point, fstype) in enumerate(mounts)]
    return "\n".join(lines) + "\n"


RUNNER_FILES = {
    "proc/cpuinfo": CPUINFO_X86.format(hypervisor="hypervisor"),
    "proc/self/status": status(),
    "proc/self/uid_map": "         0          0 4294967295\n",
    "proc/self/cgroup": "0::/system.slice/runner.service\n",
    "proc/self/mountinfo": mountinfo(("/sys/fs/cgroup", "cgroup2")),
    "proc/self/attr/apparmor/current": "unconfined\n",
    "proc/1/comm": "systemd\n",
    "proc/sys/kernel/osrelease": "6.17.0-1022-azure\n",
    "proc/sys/kernel/arch": "x86_64\n",
    "proc/filesystems": "nodev\tsysfs\nnodev\tcgroup2\n",
    "etc/os-release": 'NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nPRETTY_NAME="Ubuntu 24.04.5 LTS"\n',
    "sys/class/dmi/id/sys_vendor": "Microsoft Corporation\n",
    "sys/class/dmi/id/product_name": "Virtual Machine\n",
    "sys/module/apparmor/parameters/enabled": "Y\n",
    "sys/kernel/security/lsm": "lockdown,capability,landlock,yama,apparmor,ima,evm",
    "sys/fs/cgroup/cgroup.controllers": "cpu memory pids\n",
    "sys/fs/cgroup/system.slice/runner.service/cgroup.procs": "",
    "run/systemd/system/": None,
}


# The inodes of the initial user and PID namespaces (PROC_USER_INIT_INO and PROC_PID_INIT_INO in
# linux/proc_ns.h), written out so that a wrong constant in the collector fails these tests.
INITIAL_USER_NS = 4026531837
INITIAL_PID_NS = 4026531836


class FakeProbe(collector.Probe):
    """A Probe whose namespace inodes are set by the test instead of read from /proc."""

    def __init__(self, *args, user_inode=INITIAL_USER_NS, pid_inode=INITIAL_PID_NS, **kwargs):
        super().__init__(*args, **kwargs)
        self.inodes = {"user": user_inode, "pid": pid_inode}

    def namespace_inode(self, name):
        if self.inodes[name] is None:
            raise FileNotFoundError(name)
        return self.inodes[name]


def interpreter_patches(system, interpreter):
    """Make the interpreter facts the collector reads match the made-up machine, not the host that runs the tests."""
    config = collector.sysconfig.get_config_var
    version = f"{sys.version_info[0]}{sys.version_info[1]}"
    if system == "Linux":
        host_soabi = config("SOABI") or f"cpython-{version}-x86_64-linux-gnu"
        soabi = re.sub(r"^([a-z]+-[0-9]+[a-z]*)-[a-z0-9_]+-(linux-[a-z0-9]+)$", rf"\g<1>-{interpreter}-\g<2>", host_soabi)
        plat = f"linux-{interpreter}"
    elif system == "Windows":
        soabi, plat = None, {"x86_64": "win-amd64", "aarch64": "win-arm64"}[interpreter]
    elif system == "Darwin":
        soabi, plat = f"cpython-{version}-darwin", f"macosx-11.0-{'arm64' if interpreter == 'aarch64' else interpreter}"
    else:
        soabi, plat = None, f"freebsd-14.1-RELEASE-{'amd64' if interpreter == 'x86_64' else interpreter}"
    patches = [
        mock.patch.object(collector.sysconfig, "get_config_var", side_effect=lambda name: soabi if name == "SOABI" else config(name)),
        mock.patch.object(collector.sysconfig, "get_platform", return_value=plat),
        mock.patch.object(collector.sys.implementation, "_multiarch", f"{interpreter}-linux-gnu" if system == "Linux" else None, create=True),
    ]
    if system != "Linux":
        patches.append(mock.patch.object(collector.os, "confstr", side_effect=ValueError("unrecognized configuration name"), create=True))
    return patches


@contextlib.contextmanager
def without(module, name):
    """Remove an attribute, such as os.getresuid on macOS, for the duration of a test."""
    saved = getattr(module, name)
    delattr(module, name)
    try:
        yield
    finally:
        setattr(module, name, saved)


class FakeMachine:
    """A temporary root directory, an environment and a git directory for one made-up machine.

    `machine` is what uname reports; `interpreter` is the architecture the Python
    build targets, which emulation and personality changes do not alter.
    """

    def __init__(self, files=None, remove=(), env=None, machine="x86_64", release="6.17.0-1022-azure", system="Linux", interpreter="x86_64"):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name) / "root"
        content = dict(RUNNER_FILES if files is None else files)
        for name in remove:
            content.pop(name)
        for name, text in content.items():
            path = self.root / name
            if text is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        self.root.mkdir(exist_ok=True)
        self.git = pathlib.Path(self.directory.name) / "git"
        self.git.mkdir()
        (self.git / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
        self.env = {"PATH": ""} if env is None else env
        self.patches = [
            mock.patch.object(collector.platform, "machine", return_value=machine),
            mock.patch.object(collector.platform, "release", return_value=release),
            *interpreter_patches(system, interpreter),
        ]
        self.system = system

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.directory.cleanup()

    def probe(self, **kwargs):
        kwargs.setdefault("probe_node", False)
        return FakeProbe(self.root, env=self.env, git_dir=self.git, system=self.system, **kwargs)

    def result(self, **kwargs):
        return collector.build_result(self.probe(**kwargs), "linux-x64-gha")


def value(fact):
    return fact["value"] if fact["provenance"] in ("observed", "inferred", "declared") else None


def walk_provenance(node, path=""):
    """(JSON pointer, provenance) for every fact in a record."""
    if isinstance(node, dict):
        if "provenance" in node:
            yield path, node["provenance"]
            return
        for key, item in node.items():
            yield from walk_provenance(item, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk_provenance(item, f"{path}/{index}")


def unescaped_pipes(line):
    return len(re.findall(r"(?<!\\)\|", line))


def kinds(result):
    return [value(layer["kind"]) for layer in result["environment"]["layers"]]


class Case(unittest.TestCase):
    def assertValidRecord(self, result, policy=True):
        findings = validator.validate_document(result, SCHEMA, evaluate_policy=policy)
        self.assertEqual(findings.errors, [], findings.items)
        return findings

    def assertFact(self, fact, expected, provenance):
        """The value and how it was established: an inferred fact must not turn into an observed one."""
        self.assertEqual((value(fact), fact["provenance"]), (expected, provenance), fact)


class LinuxProbeTests(Case):
    def test_a_github_hosted_runner_vm(self):
        with FakeMachine() as machine:
            result = machine.result()
        self.assertEqual(result["probe_errors"], [])
        self.assertEqual(kinds(result), ["host", "vm"])
        host, vm = result["environment"]["layers"]
        self.assertEqual(host["os"]["family"]["provenance"], "unknown")
        self.assertEqual(value(vm["technology"]), "hyper-v")
        self.assertEqual(value(vm["os"]["name"]), "ubuntu")
        self.assertEqual(value(vm["os"]["version"]), "24.04")
        # Inferred: an undetected container in the initial PID namespace would give the same files.
        self.assertEqual(vm["os"]["name"], {"value": "ubuntu", "provenance": "inferred", "evidence": ["file:/etc/os-release#ID", "file:/proc/self/ns/pid#inode"]})
        self.assertEqual(vm["service_manager"]["name"]["provenance"], "inferred")
        self.assertEqual(value(vm["os"]["kernel"]), "6.17.0-1022-azure")
        self.assertEqual(value(vm["cpu"]["arch"]), "x86_64")
        self.assertEqual(vm["cpu"]["execution"]["provenance"], "unknown")
        self.assertEqual(value(vm["service_manager"]["scope"]), "system")
        identity, isolation = result["workload"]["identity"], result["workload"]["isolation"]
        self.assertEqual((value(identity["uid"]), value(identity["gid"]), value(identity["superuser"])), (1001, 118, False))
        self.assertEqual(identity["real_uid"], {"value": 1001, "provenance": "observed", "evidence": ["file:/proc/self/status#Uid"]})
        self.assertEqual(identity["saved_uid"], {"value": 1001, "provenance": "observed", "evidence": ["file:/proc/self/status#Uid"]})
        self.assertIs(value(identity["user_namespace"]["initial"]), True)
        self.assertEqual(value(isolation["capabilities"]["effective"]), [])
        self.assertEqual(isolation["capabilities"]["permitted"], {"value": [], "provenance": "observed", "evidence": ["file:/proc/self/status#CapPrm"]})
        self.assertEqual(vm["os"]["kernel"], {"value": "6.17.0-1022-azure", "provenance": "observed", "evidence": ["file:/proc/sys/kernel/osrelease"]})
        self.assertEqual(len(value(isolation["capabilities"]["bounding"])), 41)
        self.assertEqual(value(isolation["seccomp"]), "disabled")
        self.assertEqual(value(isolation["apparmor"]["profile"]), "unconfined")
        self.assertEqual(value(isolation["selinux"]["mode"]), "disabled")
        self.assertEqual(value(isolation["cgroup"]["version"]), "v2")
        self.assertIs(value(isolation["pid_namespace"]["initial"]), True)
        self.assertEqual(result["stages"]["detect"]["status"], "passed")
        self.assertEqual(result["overall"], "incomplete")
        self.assertValidRecord(result)

    def test_derived_facts_keep_their_provenance(self):
        with FakeMachine() as machine:
            result = machine.result()
        host, vm = result["environment"]["layers"]
        self.assertFact(host["kind"], "host", "inferred")
        self.assertFact(vm["kind"], "vm", "inferred")
        self.assertFact(vm["technology"], "hyper-v", "inferred")
        self.assertFact(result["workload"]["isolation"]["selinux"]["mode"], "disabled", "inferred")
        self.assertFact(result["run"]["ci"]["system"], "local", "inferred")
        python = result["workload"]["runtimes"]["python"]
        self.assertEqual(python["gil_enabled"]["provenance"], "observed" if hasattr(sys, "_is_gil_enabled") else "inferred")
        # The collector states its own escalation; it is the only declared fact it writes.
        self.assertEqual(result["stages"]["detect"]["privilege"]["escalation"], {"value": "none", "provenance": "declared", "evidence": ["tool:collect-environment"]})
        self.assertEqual([path for path, provenance in walk_provenance(result) if provenance == "declared"], ["/stages/detect/privilege/escalation"])

    def test_a_vmbus_names_hyper_v_when_dmi_does_not(self):
        files = dict(RUNNER_FILES)
        files["sys/class/dmi/id/product_name"] = "Unknown\n"
        files["sys/bus/vmbus/devices/device-1"] = ""
        with FakeMachine(files) as machine:
            self.assertEqual(value(machine.result()["environment"]["layers"][1]["technology"]), "hyper-v")

    def test_no_signal_is_not_reported_as_bare_metal(self):
        files = dict(RUNNER_FILES)
        files["proc/cpuinfo"] = CPUINFO_X86.format(hypervisor="")
        for name in ("sys/class/dmi/id/sys_vendor", "sys/class/dmi/id/product_name"):
            files.pop(name)
        with FakeMachine(files) as machine:
            result = machine.result()
        self.assertEqual(result["environment"]["layers"][0]["kind"]["provenance"], "unknown")
        self.assertIn("does not show bare metal", result["environment"]["layers"][0]["kind"]["reason"])
        self.assertValidRecord(result)

    def test_a_docker_container_in_a_vm(self):
        files = dict(RUNNER_FILES)
        files.update({".dockerenv": "", "proc/self/cgroup": "0::/\n", "etc/os-release": "ID=debian\nVERSION_ID=\"12\"\n"})
        files.pop("run/systemd/system/")
        with FakeMachine(files) as machine:
            result = machine.result()
        self.assertEqual(kinds(result), ["host", "vm", "container"])
        vm, container = result["environment"]["layers"][1:]
        self.assertEqual(vm["os"]["name"]["provenance"], "unknown")
        self.assertEqual(vm["service_manager"]["name"]["provenance"], "unknown")
        self.assertEqual(value(container["technology"]), "docker")
        self.assertEqual(value(container["os"]["name"]), "debian")
        self.assertEqual(container["os"]["kernel"]["provenance"], "not-applicable")
        self.assertEqual(container["container"]["engine_mode"]["provenance"], "unknown")
        self.assertEqual(container["container"]["privileged"]["provenance"], "unknown")
        self.assertEqual(container["container"]["orchestration"]["name"]["provenance"], "unknown")
        self.assertValidRecord(result, policy=False)
        codes = validator.validate_document(result, SCHEMA).codes()
        self.assertIn("policy-no-privileged-container", codes)
        self.assertIn("policy-minimal-devices", codes)
        # Docker does not tell a container whether it runs rootless, even in a user namespace of its own.
        with FakeMachine(files) as machine:
            result = machine.result(user_inode=4026532000)
        self.assertEqual(result["environment"]["layers"][-1]["container"]["engine_mode"]["provenance"], "unknown")

    def test_container_markers_that_disagree_name_no_engine(self):
        # An image can hold /run/.containerenv; under Docker it does not show a Podman engine or its mode.
        files = dict(RUNNER_FILES)
        files.update({".dockerenv": "", "run/.containerenv": "rootless=1\n", "proc/self/cgroup": "0::/\n"})
        with FakeMachine(files) as machine:
            container = machine.result()["environment"]["layers"][-1]
        self.assertEqual(container["technology"]["provenance"], "unknown")
        self.assertIn("name different engines (docker, podman)", container["technology"]["reason"])
        self.assertEqual(container["technology"]["evidence"], ["file:/.dockerenv", "file:/run/.containerenv"])
        self.assertEqual(container["container"]["engine_mode"]["provenance"], "unknown")
        for env, expected in (({"PATH": "", "container": "podman"}, "podman"), ({"PATH": "", "container": "lxc"}, None), ({"PATH": "", "container": "oci"}, "podman")):
            with self.subTest(env=env), FakeMachine(dict(RUNNER_FILES, **{"run/.containerenv": "rootless=0\n"}), env=env) as machine:
                container = machine.result()["environment"]["layers"][-1]
                self.assertEqual(value(container["technology"]), expected)
                self.assertEqual(value(container["container"]["engine_mode"]), "rootful" if expected else None)

    def test_a_rootless_podman_container_with_a_non_root_user(self):
        files = dict(RUNNER_FILES)
        files.update({
            "run/.containerenv": 'engine="podman-4.9.3"\nname="private-container-name"\nid="0123abcd"\nimage="registry.example/private:1"\nrootless=1\n',
            "proc/self/uid_map": "         0       1000          1\n         1     100000      65536\n",
            "proc/self/status": status(uid=1000, gid=1000, cap_bnd="00000000800405fb", no_new_privs=1, seccomp=2),
            "proc/self/cgroup": "0::/\n",
        })
        with FakeMachine(files) as machine:
            result = machine.result(user_inode=4026532000)
        container = result["environment"]["layers"][-1]
        self.assertEqual(value(container["technology"]), "podman")
        self.assertFact(container["container"]["engine_mode"], "rootless", "inferred")
        identity = result["workload"]["identity"]
        self.assertEqual((value(identity["uid"]), value(identity["superuser"])), (1000, False))
        self.assertIs(value(identity["user_namespace"]["initial"]), False)
        self.assertEqual(value(identity["user_namespace"]["uid_map"]), [[0, 1000, 1], [1, 100000, 65536]])
        self.assertEqual(value(result["workload"]["isolation"]["seccomp"]), "filter")
        self.assertIs(value(result["workload"]["isolation"]["no_new_privs"]), True)
        text = json.dumps(result)
        for private in ("private-container-name", "0123abcd", "registry.example"):
            self.assertNotIn(private, text)
        self.assertValidRecord(result, policy=False)

    def test_root_in_a_rootless_engine_is_still_root(self):
        files = dict(RUNNER_FILES)
        files.update({
            "run/.containerenv": "engine=\"podman-4.9.3\"\nrootless=1\n",
            "proc/self/uid_map": "         0       1000          1\n",
            "proc/self/status": status(uid=0, gid=0, cap_eff="00000000800405fb", cap_bnd="00000000800405fb"),
        })
        with FakeMachine(files) as machine:
            result = machine.result(user_inode=4026532000)
        self.assertEqual(value(result["environment"]["layers"][-1]["container"]["engine_mode"]), "rootless")
        self.assertIs(value(result["workload"]["identity"]["superuser"]), True)
        self.assertIn("policy-app-non-root", validator.validate_document(result, SCHEMA).codes())

    def test_wsl2(self):
        files = dict(RUNNER_FILES)
        files["proc/sys/kernel/osrelease"] = "6.6.87.2-microsoft-standard-WSL2\n"
        for name in ("sys/class/dmi/id/sys_vendor", "sys/class/dmi/id/product_name"):
            files.pop(name)
        env = {"PATH": "", "WSL_DISTRO_NAME": "PrivateDistroName", "WSL_INTEROP": "/run/WSL/1_interop"}
        with FakeMachine(files, env=env, release="6.6.87.2-microsoft-standard-WSL2") as machine:
            result = machine.result()
        self.assertEqual(kinds(result), ["host", "wsl2"])
        self.assertFact(result["environment"]["layers"][0]["os"]["family"], "windows", "inferred")
        self.assertFact(result["environment"]["layers"][1]["kind"], "wsl2", "inferred")
        self.assertNotIn("PrivateDistroName", json.dumps(result))
        self.assertValidRecord(result)
        # WSL 2 runs each distribution in its own PID namespace, so that namespace does not hide its os-release.
        with FakeMachine(files, env=env, release="6.6.87.2-microsoft-standard-WSL2") as machine:
            result = machine.result(pid_inode=4026532001)
        wsl = result["environment"]["layers"][1]
        self.assertEqual(wsl["os"]["name"], {"value": "ubuntu", "provenance": "inferred", "evidence": ["file:/etc/os-release#ID", "file:/proc/sys/kernel/osrelease"]})
        self.assertEqual(wsl["service_manager"]["name"]["provenance"], "inferred")
        self.assertValidRecord(result)
        # Older WSL 2 kernels have no WSL2 suffix; a Microsoft kernel that matches neither form stays unknown.
        for release, expected in (("4.19.128-microsoft-standard", ["host", "wsl2"]), ("6.1.0-custom-microsoft", [None])):
            with self.subTest(release=release), FakeMachine(dict(files, **{"proc/sys/kernel/osrelease": release + "\n"}), env=env, release=release) as machine:
                result = machine.result()
                self.assertEqual(kinds(result), expected)
                self.assertEqual(value(result["environment"]["layers"][-1]["os"]["kernel"]), release)

    def test_an_emulated_process(self):
        files = dict(RUNNER_FILES)
        files[".dockerenv"] = ""
        # An aarch64 interpreter under user-mode emulation, which rewrites uname but not /proc/sys/kernel/arch.
        build = ("aarch64", "python:sys.implementation._multiarch")
        config = collector.sysconfig.get_config_var
        soabi = f"cpython-{sys.version_info[0]}{sys.version_info[1]}-aarch64-linux-gnu"
        aarch64_config = lambda name: soabi if name == "SOABI" else config(name)  # noqa: E731
        with FakeMachine(files, machine="aarch64") as machine, mock.patch.object(collector.sysconfig, "get_platform", return_value="linux-aarch64"), \
                mock.patch.object(collector.sysconfig, "get_config_var", side_effect=aarch64_config), \
                mock.patch.object(FakeProbe, "build_arch", return_value=build):
            result = machine.result()
        vm, container = result["environment"]["layers"][1:]
        self.assertEqual(value(vm["cpu"]["arch"]), "x86_64")
        # The container layer names no architecture of its own; the runtime records the emulation.
        self.assertEqual(container["cpu"]["arch"]["provenance"], "unknown")
        self.assertEqual(container["cpu"]["execution"]["provenance"], "unknown")
        self.assertEqual(value(result["workload"]["runtimes"]["python"]["arch"]), "aarch64")
        self.assertEqual(value(result["workload"]["runtimes"]["python"]["execution"]), "emulated")
        self.assertValidRecord(result, policy=False)

    def test_execution_is_compared_with_the_kernel_architecture(self):
        def execution(arch, kernel_arch, cpu_family):
            fact = collector.execution_fact(arch, "python:x", (kernel_arch, cpu_family))
            return value(fact) or fact["provenance"]

        self.assertEqual(execution("x86_64", "x86_64", "x86"), "native")
        self.assertEqual(execution("x86", "x86_64", "x86"), "native")
        # Many recent aarch64 cores have no AArch32, so 32-bit Arm may run under qemu-user.
        self.assertEqual(execution("armv7", "aarch64", "arm"), "unknown")
        self.assertEqual(execution("aarch64", "x86_64", "arm"), "emulated")
        self.assertEqual(execution("x86_64", "aarch64", "arm"), "emulated")
        self.assertEqual(execution("x86", "aarch64", "arm"), "emulated")
        # Without /proc/sys/kernel/arch only a /proc/cpuinfo of another family is evidence.
        self.assertEqual(execution("x86_64", None, "arm"), "emulated")
        self.assertEqual(execution("aarch64", None, "arm"), "unknown")
        self.assertEqual(execution("x86_64", None, "x86"), "unknown")
        self.assertEqual(execution(None, "x86_64", "x86"), "unknown")
        # A name outside the documented list is not evidence of emulation.
        self.assertEqual(execution("ppc64", "ppc64le", None), "unknown")
        self.assertEqual(execution("mips64", "x86_64", "x86"), "unknown")
        self.assertEqual(execution(collector.normalize_arch("armv8l"), "aarch64", "arm"), "unknown")
        with FakeMachine({"proc/cpuinfo": CPUINFO_ARM}) as machine:
            self.assertEqual(machine.probe().cpuinfo(), ("arm", None))

    def test_an_old_kernel_without_the_arch_file(self):
        # uname and /proc/cpuinfo follow personality changes (linux32 gives i686) and qemu-user, so
        # without the arch file neither names the kernel's architecture.
        for label, machine_name, cpuinfo in (("x86_64", "x86_64", None), ("linux32 personality", "i686", None), ("qemu-user", "aarch64", CPUINFO_ARM)):
            files = dict(RUNNER_FILES, **({"proc/cpuinfo": cpuinfo} if cpuinfo else {}))
            with self.subTest(label), FakeMachine(files, remove=("proc/sys/kernel/arch",), machine=machine_name) as machine:
                result = machine.result()
                self.assertEqual(result["environment"]["layers"][1]["cpu"]["arch"]["provenance"], "unknown")
                self.assertValidRecord(result)
        with FakeMachine(remove=("proc/sys/kernel/arch",)) as machine:
            self.assertEqual(machine.result()["workload"]["runtimes"]["python"]["execution"]["provenance"], "unknown")

    def test_the_c_library_comes_from_confstr_only(self):
        with FakeMachine() as machine, mock.patch.object(collector.os, "confstr", return_value="glibc 2.39"):
            self.assertEqual(machine.result()["workload"]["runtimes"]["python"]["libc"], {"value": "glibc 2.39", "provenance": "observed", "evidence": ["python:os.confstr"]})
        # Without it (musl, macOS, Windows), the interpreter's executable is not scanned for a version.
        for effect in (ValueError("unrecognized configuration name"), AttributeError("confstr"), None):
            confstr = mock.patch.object(collector.os, "confstr", return_value=None) if effect is None else mock.patch.object(collector.os, "confstr", side_effect=effect)
            with self.subTest(effect=type(effect).__name__), FakeMachine() as machine, confstr, \
                    mock.patch.object(collector.platform, "libc_ver", side_effect=AssertionError("scans the interpreter")):
                result = machine.result()
                self.assertEqual((result["workload"]["runtimes"]["python"]["libc"]["provenance"], result["probe_errors"]), ("unknown", []))

    def test_the_interpreter_build_architecture(self):
        with FakeMachine() as machine:
            probe = machine.probe()
            arch, evidence = probe.build_arch()
            self.assertIsNotNone(arch)
            with mock.patch.object(collector.sys, "implementation", mock.Mock(spec=["name", "cache_tag"])):
                for soabi, expected in (("cpython-312-x86_64-linux-gnu", "x86_64"), ("cpython-313t-aarch64-linux-gnu", "aarch64"),
                                        ("cpython-312-arm-linux-gnueabihf", "armv7"), ("cpython-312-powerpc64le-linux-gnu", "ppc64le")):
                    with self.subTest(soabi=soabi), mock.patch.object(collector.sysconfig, "get_config_var", return_value=soabi):
                        self.assertEqual(probe.build_arch(), (expected, "python:sysconfig.SOABI"))
                with tempfile.TemporaryDirectory() as directory:
                    executable = pathlib.Path(directory) / "python"
                    executable.write_bytes(b"\x7fELF\x02\x01" + bytes(12) + (183).to_bytes(2, "little"))
                    with mock.patch.object(collector.sysconfig, "get_config_var", return_value=None), \
                            mock.patch.object(collector.sys, "executable", str(executable)):
                        self.assertEqual(probe.build_arch(), ("aarch64", "file:sys.executable#e_machine"))
                    # ppc64 and aarch64_be share e_machine with ppc64le and aarch64; the header's byte order tells them apart.
                    for machine, order, expected in ((21, 1, "ppc64le"), (21, 2, None), (183, 2, None), (22, 2, "s390x"), (22, 1, None)):
                        executable.write_bytes(b"\x7fELF\x02" + bytes([order]) + bytes(12) + machine.to_bytes(2, "little" if order == 1 else "big"))
                        with self.subTest(machine=machine, order=order), mock.patch.object(collector.sysconfig, "get_config_var", return_value=None), \
                                mock.patch.object(collector.sys, "executable", str(executable)):
                            self.assertEqual(probe.build_arch()[0], expected)
                    # The class tells a 32-bit build from the 64-bit name that shares its e_machine (x32, aarch64 ILP32, riscv32,
                    # loongarch32, 31-bit s390).
                    for machine, elf_class, order, expected in ((3, 1, 1, "x86"), (40, 1, 1, "armv7"), (3, 2, 1, None), (62, 2, 1, "x86_64"), (62, 1, 1, None),
                                                                (183, 1, 1, None), (243, 2, 1, "riscv64"), (243, 1, 1, None), (258, 2, 1, "loongarch64"),
                                                                (258, 1, 1, None), (22, 1, 2, None), (40, 1, 2, None), (40, 2, 1, None), (21, 1, 1, None)):
                        executable.write_bytes(b"\x7fELF" + bytes([elf_class, order]) + bytes(12) + machine.to_bytes(2, "little" if order == 1 else "big"))
                        with self.subTest(machine=machine, elf_class=elf_class, order=order), mock.patch.object(collector.sysconfig, "get_config_var", return_value=None), \
                                mock.patch.object(collector.sys, "executable", str(executable)):
                            self.assertEqual(probe.build_arch()[0], expected)
                    executable.write_bytes(b"#!/bin/sh\n")
                    with mock.patch.object(collector.sysconfig, "get_config_var", return_value=None), \
                            mock.patch.object(collector.sys, "executable", str(executable)):
                        self.assertEqual(probe.build_arch(), (None, None))

    def test_unexpected_text_is_not_recorded(self):
        files = dict(RUNNER_FILES, **{"proc/self/attr/apparmor/current": "unconfined\x1b]0;title\x07\n", "proc/sys/kernel/osrelease": "6.17.0\x1b[31m\n"})
        with FakeMachine(files) as machine:
            result = machine.result()
        self.assertEqual(result["environment"]["layers"][1]["os"]["kernel"]["provenance"], "unknown")
        self.assertEqual(result["workload"]["isolation"]["apparmor"]["profile"]["provenance"], "unknown")
        self.assertValidRecord(result)
        # os-release values that fail their checks are not recorded.
        for text, field in (("ID=ubuntu;reboot\nVERSION_ID=24.04\n", "name"), ("ID=ubuntu\nVERSION_ID=\"24.04\x1b[2J\"\n", "version")):
            with self.subTest(field=field), FakeMachine(dict(RUNNER_FILES, **{"etc/os-release": text})) as machine:
                fact = machine.result()["environment"]["layers"][1]["os"][field]
                self.assertEqual(fact["provenance"], "unknown")
                self.assertIn("os-release has no usable", fact["reason"])

    def test_the_kernel_release_comes_from_proc(self):
        # uname follows personality changes (UNAME26) and qemu-user, so it is used only without the file.
        with FakeMachine(release="2.6.97") as machine:
            self.assertFact(machine.result()["environment"]["layers"][1]["os"]["kernel"], "6.17.0-1022-azure", "observed")
        with FakeMachine(remove=("proc/sys/kernel/osrelease",), release="6.17.0-1022-azure") as machine:
            kernel = machine.result()["environment"]["layers"][1]["os"]["kernel"]
        self.assertEqual(kernel, {"value": "6.17.0-1022-azure", "provenance": "inferred", "evidence": ["python:platform.release"]})

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(signal, "alarm"), "needs FIFOs and SIGALRM")
    def test_a_fifo_is_not_read(self):
        # A FIFO in place of a file must not block the probe; SIGALRM ends the test run if it does.
        files = dict(RUNNER_FILES)
        files.pop("etc/os-release")
        with FakeMachine(files) as machine:
            (machine.root / "etc").mkdir()
            os.mkfifo(machine.root / "etc" / "os-release")
            os.mkfifo(machine.git / "packed-refs")
            (machine.git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            signal.alarm(20)
            try:
                result = machine.result()
                text, reason = machine.probe().read("/etc/os-release")
            finally:
                signal.alarm(0)
        self.assertEqual((text, reason), (None, "/etc/os-release is not a regular file"))
        self.assertEqual(result["probe_errors"], [])
        self.assertEqual(result["environment"]["layers"][1]["os"]["name"]["provenance"], "unknown")
        self.assertEqual(result["run"]["revision"]["tested_sha"]["provenance"], "unknown")

    def test_a_container_without_markers_is_not_read_as_the_vm(self):
        files = dict(RUNNER_FILES, **{"proc/self/cgroup": "0::/\n", "etc/os-release": "ID=debian\nVERSION_ID=\"12\"\n"})
        for label, inode, initial in (("another PID namespace", 4026532001, False), ("unreadable PID namespace", None, None)):
            with self.subTest(label), FakeMachine(files) as machine:
                result = machine.result(pid_inode=inode)
                self.assertEqual(kinds(result), ["host", "vm"])
                vm = result["environment"]["layers"][1]
                for fact in (vm["os"]["name"], vm["os"]["version"], vm["service_manager"]["name"], vm["service_manager"]["scope"]):
                    self.assertEqual(fact["provenance"], "unknown")
                self.assertNotIn("debian", json.dumps(vm))
                self.assertEqual(value(vm["cpu"]["arch"]), "x86_64")
                self.assertIs(value(result["workload"]["isolation"]["pid_namespace"]["initial"]), initial)
                self.assertValidRecord(result, policy=False)
                # The container rules cannot pass for a container the record does not show.
                findings = validator.validate_document(result, SCHEMA)
                self.assertEqual(findings.codes(), ["policy-minimal-devices", "policy-no-privileged-container"])
        # Apptainer keeps the initial PID namespace by default, so only its own markers show it.
        for marker, env in (({".singularity.d/": None}, {"PATH": ""}), ({}, {"PATH": "", "APPTAINER_CONTAINER": "/home/someone/private.sif"})):
            with self.subTest(marker=marker, env=sorted(env)), FakeMachine(dict(files, **marker), env=env) as machine:
                result = machine.result()
                self.assertEqual(kinds(result), ["host", "vm", "container"])
                self.assertEqual(value(result["environment"]["layers"][2]["technology"]), "apptainer")
                self.assertEqual(result["environment"]["layers"][1]["os"]["name"]["provenance"], "unknown")
                self.assertNotIn("private.sif", json.dumps(result))
                self.assertValidRecord(result, policy=False)

    def test_signals_that_do_not_show_a_vm(self):
        metal = dict(RUNNER_FILES, **{"proc/cpuinfo": CPUINFO_X86.format(hypervisor=""), "sys/class/dmi/id/sys_vendor": "Amazon EC2\n",
                                      "sys/class/dmi/id/product_name": "m5.metal\n"})
        with FakeMachine(metal) as machine:
            result = machine.result()
        self.assertEqual(kinds(result), [None])
        self.assertValidRecord(result)
        with FakeMachine(dict(metal, **{"proc/cpuinfo": CPUINFO_X86.format(hypervisor="hypervisor")})) as machine:
            self.assertEqual(value(machine.result()["environment"]["layers"][1]["technology"]), "amazon")
        # Arm /proc/cpuinfo has no hypervisor flag, so only the product name tells an EC2 VM from metal.
        graviton = dict(metal, **{"proc/cpuinfo": CPUINFO_ARM, "proc/sys/kernel/arch": "aarch64\n"})
        for product, expected in (("c7g.large", ["host", "vm"]), ("c7g.metal", [None]), ("m7i.metal-24xl", [None])):
            with self.subTest(product=product), FakeMachine(dict(graviton, **{"sys/class/dmi/id/product_name": product + "\n"}), machine="aarch64") as machine:
                result = machine.result()
                self.assertEqual(kinds(result), expected)
                self.assertValidRecord(result, policy=False)
        # WSL 1 has no Linux kernel, and Windows with Hyper-V shows the hypervisor flag to it.
        wsl1 = dict(RUNNER_FILES, **{"proc/sys/kernel/osrelease": "4.4.0-19041-Microsoft\n"})
        with FakeMachine(wsl1, release="4.4.0-19041-Microsoft") as machine:
            result = machine.result()
        self.assertEqual(kinds(result), [None])
        self.assertEqual(result["environment"]["layers"][0]["os"]["kernel"]["provenance"], "unknown")
        self.assertValidRecord(result)

    def test_the_service_manager(self):
        with FakeMachine(remove=("run/systemd/system/",)) as machine:
            manager = machine.result()["environment"]["layers"][1]["service_manager"]
        self.assertEqual((manager["name"]["provenance"], manager["scope"]["provenance"]), ("unknown", "unknown"))
        with FakeMachine(dict(RUNNER_FILES, **{"proc/1/comm": "bash\n"})) as machine:
            manager = machine.result()["environment"]["layers"][1]["service_manager"]
        self.assertEqual((value(manager["name"]), manager["scope"]["provenance"]), ("systemd", "unknown"))

    def test_sysconfig_overrides_are_not_recorded(self):
        env = {"PATH": "", "_PYTHON_HOST_PLATFORM": "ghp_" + "S" * 36}
        with FakeMachine(env=env) as machine, mock.patch.object(collector.sysconfig, "get_platform", return_value="ghp_" + "S" * 36):
            result = machine.result()
        python = result["workload"]["runtimes"]["python"]
        self.assertEqual((python["platform"]["provenance"], python["soabi"]["provenance"]), ("unknown", "unknown"))
        self.assertIn("_PYTHON_HOST_PLATFORM is set", python["platform"]["reason"])
        self.assertNotIn("ghp_", json.dumps(result))
        self.assertValidRecord(result)
        with FakeMachine() as machine, mock.patch.object(collector.sysconfig, "get_platform", return_value="linux x86_64; rm -rf /"):
            self.assertEqual(machine.result()["workload"]["runtimes"]["python"]["platform"]["provenance"], "unknown")

    def test_the_free_threaded_flag_is_inferred_only_before_3_13(self):
        config = collector.sysconfig.get_config_var
        without_flag = lambda name: None if name == "Py_GIL_DISABLED" else config(name)  # noqa: E731
        with FakeMachine() as machine:
            probe = machine.probe()
            with mock.patch.object(collector.sysconfig, "get_config_var", side_effect=without_flag):
                with mock.patch.object(collector.sys, "version_info", (3, 12, 15)):
                    self.assertEqual(probe.python((None, None))["free_threaded_build"]["provenance"], "inferred")
                with mock.patch.object(collector.sys, "version_info", (3, 13, 0)):
                    self.assertEqual(probe.python((None, None))["free_threaded_build"]["provenance"], "unknown")

    def test_a_kubernetes_pod(self):
        files = dict(RUNNER_FILES)
        files["proc/self/cgroup"] = "0::/kubepods.slice/kubepods-burstable.slice/cri-containerd-0123.scope\n"
        env = {"PATH": "", "KUBERNETES_SERVICE_HOST": "10.96.0.1"}
        with FakeMachine(files, env=env) as machine:
            result = machine.result()
        container = result["environment"]["layers"][-1]
        self.assertEqual(value(container["kind"]), "container")
        self.assertFact(container["container"]["orchestration"]["name"], "kubernetes", "inferred")
        text = json.dumps(result)
        self.assertNotIn("10.96.0.1", text)
        self.assertNotIn("cri-containerd-0123", text)
        self.assertValidRecord(result, policy=False)

    def test_systemd_inside_a_container(self):
        files = dict(RUNNER_FILES)
        files.update({"run/.containerenv": "rootless=0\n", "run/systemd/container": "podman\n"})
        with FakeMachine(files) as machine:
            result = machine.result()
        container = result["environment"]["layers"][-1]
        self.assertEqual(value(container["service_manager"]["name"]), "systemd")
        self.assertEqual(value(container["container"]["engine_mode"]), "rootful")
        self.assertEqual(result["environment"]["layers"][1]["service_manager"]["name"]["provenance"], "unknown")


class IsolationTests(Case):
    def isolation(self, files, **kwargs):
        with FakeMachine(files, **kwargs) as machine:
            return machine.probe().isolation()

    def test_cgroup_version_comes_from_the_mount(self):
        for mounts, expected in (
            ((("/sys/fs/cgroup", "cgroup2"),), "v2"),
            ((("/sys/fs/cgroup", "tmpfs"), ("/sys/fs/cgroup/unified", "cgroup2"), ("/sys/fs/cgroup/memory", "cgroup")), "hybrid"),
            ((("/sys/fs/cgroup", "tmpfs"), ("/sys/fs/cgroup/memory", "cgroup")), "v1"),
            ((), None),
            ((("/sys/fs/cgroup", "tmpfs"),), None),
        ):
            files = dict(RUNNER_FILES, **{"proc/self/mountinfo": mountinfo(*mounts)})
            with self.subTest(mounts=mounts):
                self.assertEqual(value(self.isolation(files)["cgroup"]["version"]), expected)

    def test_delegation_is_read_only_for_a_process_without_privilege(self):
        files = dict(RUNNER_FILES)
        files["sys/fs/cgroup/system.slice/runner.service/cgroup.subtree_control"] = ""
        with mock.patch.object(collector.os, "access", return_value=True):
            self.assertIs(value(self.isolation(files)["cgroup"]["delegated"]), True)
        with mock.patch.object(collector.os, "access", return_value=False):
            self.assertIs(value(self.isolation(files)["cgroup"]["delegated"]), False)
        # Write access to each of the directory, cgroup.procs, cgroup.threads and cgroup.subtree_control is needed; without
        # cgroup.subtree_control no controller can be enabled below. A file that only exists is not enough.
        threaded = dict(files, **{"sys/fs/cgroup/system.slice/runner.service/cgroup.threads": ""})
        for withheld in ("runner.service", "cgroup.procs", "cgroup.threads", "cgroup.subtree_control"):
            writable = lambda path, mode: not (str(path).endswith(withheld) and mode == collector.os.W_OK)
            with self.subTest(withheld=withheld), mock.patch.object(collector.os, "access", side_effect=writable):
                self.assertIs(value(self.isolation(threaded)["cgroup"]["delegated"]), False)
        # A kernel before 4.14 has no cgroup.threads, so the other three decide. The evidence names each object checked.
        own = "file:/sys/fs/cgroup/<own-cgroup>"
        checked = [f"{own}#writable", f"{own}/cgroup.procs#writable", f"{own}/cgroup.subtree_control#writable"]
        with mock.patch.object(collector.os, "access", side_effect=lambda path, mode: not str(path).endswith("cgroup.threads")):
            self.assertEqual(self.isolation(files)["cgroup"]["delegated"], {"value": True, "provenance": "observed", "evidence": checked})
        with mock.patch.object(collector.os, "access", return_value=True):
            self.assertEqual(self.isolation(threaded)["cgroup"]["delegated"]["evidence"], checked + [f"{own}/cgroup.threads#writable"])
        # A cgroup.threads that cannot be looked up is not taken as absent, and the probe does not fail.
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            if str(path).endswith("cgroup.threads"):
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(collector.os, "stat", side_effect=stat), mock.patch.object(collector.os, "access", side_effect=lambda path, mode: not str(path).endswith("cgroup.threads")):
            self.assertEqual(self.isolation(files)["cgroup"]["delegated"], {"value": False, "provenance": "observed", "evidence": checked + [f"{own}/cgroup.threads#writable"]})
        # access(2) checks the real user ID, so root in any of the IDs, or a capability, says nothing about delegation.
        for label, text in (("root", status(uid=0, cap_eff=FULL)), ("real root", status(real_uid=0)), ("saved root", status(saved_uid=0)),
                            ("permitted capability", status(cap_prm="0000000000000001"))):
            with self.subTest(label), mock.patch.object(collector.os, "access", return_value=True):
                self.assertEqual(self.isolation(dict(files, **{"proc/self/status": text}))["cgroup"]["delegated"]["provenance"], "unknown")
        # /sys/fs/cgroup/../../../etc is the made-up root's etc, which exists; the '..' check alone refuses it.
        escape = dict(files, **{"proc/self/cgroup": "0::/../../../etc\n"})
        with mock.patch.object(collector.os, "access", return_value=True):
            self.assertEqual(self.isolation(escape)["cgroup"]["delegated"]["provenance"], "unknown")

    def test_security_modules(self):
        selinux = dict(RUNNER_FILES)
        selinux.update({
            "sys/fs/selinux/enforce": "1",
            "sys/kernel/security/lsm": "lockdown,capability,yama,selinux,bpf",
            "proc/self/attr/current": "system_u:system_r:container_t:s0\x00",
            "sys/module/apparmor/parameters/enabled": "N\n",
        })
        isolation = self.isolation(selinux)
        self.assertEqual(value(isolation["selinux"]["mode"]), "enforcing")
        self.assertEqual(value(isolation["selinux"]["context"]), "system_u:system_r:container_t:s0")
        self.assertIs(value(isolation["apparmor"]["enabled"]), False)
        shared = dict(selinux, **{"sys/kernel/security/lsm": "capability,apparmor,selinux"})
        self.assertEqual(self.isolation(shared)["selinux"]["context"]["provenance"], "unknown")
        compiled_not_mounted = dict(RUNNER_FILES, **{"proc/filesystems": "nodev\tselinuxfs\n"})
        compiled_not_mounted.pop("sys/kernel/security/lsm")
        self.assertEqual(self.isolation(compiled_not_mounted)["selinux"]["mode"]["provenance"], "unknown")
        not_compiled = dict(compiled_not_mounted, **{"proc/filesystems": "nodev\tsysfs\n"})
        self.assertEqual(value(self.isolation(not_compiled)["selinux"]["mode"]), "disabled")
        absent = dict(RUNNER_FILES, **{"sys/module/loop/parameters/max_part": "0\n"})
        absent.pop("sys/module/apparmor/parameters/enabled")
        self.assertEqual((value(self.isolation(absent)["apparmor"]["enabled"]), self.isolation(absent)["apparmor"]["enabled"]["provenance"]), (False, "inferred"))
        # A file that is there but cannot be read is not evidence that AppArmor is off.
        unreadable = dict(RUNNER_FILES, **{"sys/module/apparmor/parameters/enabled/": None})
        unreadable.pop("sys/module/apparmor/parameters/enabled")
        apparmor = self.isolation(unreadable)["apparmor"]
        self.assertEqual((apparmor["enabled"]["provenance"], apparmor["profile"]["provenance"]), ("unknown", "unknown"))
        no_label = dict(RUNNER_FILES)
        no_label.pop("proc/self/attr/apparmor/current")
        no_label["proc/self/attr/current"] = "unconfined\n"
        self.assertEqual(self.isolation(no_label)["apparmor"]["profile"]["provenance"], "unknown")

    def test_capabilities(self):
        self.assertEqual(collector.decode_capabilities("0000000000000000"), [])
        self.assertEqual(len(collector.decode_capabilities(FULL)), 41)
        self.assertEqual(collector.decode_capabilities("0000000000003000"), ["cap_net_admin", "cap_net_raw"])
        self.assertEqual(collector.decode_capabilities("0000020000000001"), ["cap_chown", "cap_41"])
        files = dict(RUNNER_FILES, **{"proc/self/status": status(cap_eff="0000000000002000")})
        self.assertEqual(value(self.isolation(files)["capabilities"]["effective"]), ["cap_net_raw"])
        # A permitted capability can be raised again, so the policy reads the permitted set too.
        files = dict(RUNNER_FILES, **{"proc/self/status": status(cap_prm="0000000000002000")})
        capabilities = self.isolation(files)["capabilities"]
        self.assertEqual((value(capabilities["effective"]), value(capabilities["permitted"])), ([], ["cap_net_raw"]))
        with FakeMachine(files) as machine:
            self.assertIn("policy-minimal-capabilities", validator.validate_document(machine.result(), SCHEMA).codes())

    def test_a_process_that_can_regain_root(self):
        for label, text in (("real", status(real_uid=0)), ("saved", status(saved_uid=0))):
            with self.subTest(label), FakeMachine(dict(RUNNER_FILES, **{"proc/self/status": text})) as machine:
                result = machine.result()
                identity = result["workload"]["identity"]
                self.assertIs(value(identity["superuser"]), False)
                self.assertEqual(value(identity[f"{label}_uid"]), 0)
                findings = validator.validate_document(result, SCHEMA)
                self.assertIn(("policy-app-non-root", f"/workload/identity/{label}_uid"), [(code, path) for _, code, path, _ in findings.errors])

    def test_missing_files_give_unknown_values_without_errors(self):
        with FakeMachine({}) as machine:
            result = machine.result(user_inode=None)
        self.assertEqual(result["probe_errors"], [])
        self.assertEqual(result["stages"]["detect"]["status"], "passed")
        identity, isolation = result["workload"]["identity"], result["workload"]["isolation"]
        self.assertEqual(identity["uid"]["provenance"], "unknown")
        self.assertEqual(identity["user_namespace"]["initial"]["provenance"], "unknown")
        self.assertEqual(isolation["seccomp"]["provenance"], "unknown")
        self.assertEqual(isolation["cgroup"]["version"]["provenance"], "unknown")
        self.assertValidRecord(result, policy=False)
        self.assertIn("policy-app-non-root", validator.validate_document(result, SCHEMA).codes())

    def test_a_probe_failure_is_kept_apart_from_the_other_values(self):
        files = dict(RUNNER_FILES, **{"proc/self/uid_map": "0 0\n"})
        with FakeMachine(files) as machine:
            result = machine.result()
        self.assertEqual(result["probe_errors"], [{"probe": "identity", "error": "ValueError raised by the identity probe"}])
        self.assertEqual(result["stages"]["detect"]["status"], "failed")
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(result["workload"]["identity"]["uid"]["provenance"], "unknown")
        self.assertEqual(value(result["workload"]["isolation"]["seccomp"]), "disabled")
        self.assertEqual(kinds(result), ["host", "vm"])
        self.assertValidRecord(result, policy=False)


class OtherSystemTests(Case):
    # Off Linux the collector does not measure the layer kind, elevation or capabilities, so these rules fail.
    UNMEASURED = ["policy-app-non-root", "policy-minimal-capabilities", "policy-minimal-devices", "policy-no-privileged-container"]

    def test_a_system_other_than_linux(self):
        with FakeMachine(system="Windows", interpreter="aarch64") as machine:
            result = machine.result()
        layers = result["environment"]["layers"]
        self.assertEqual(len(layers), 1)
        self.assertEqual(value(layers[0]["os"]["family"]), "windows")
        self.assertEqual(layers[0]["kind"]["provenance"], "unknown")
        self.assertEqual(result["workload"]["isolation"]["seccomp"]["provenance"], "unknown")
        identity = result["workload"]["identity"]
        self.assertEqual((identity["superuser"]["provenance"], identity["real_uid"]["provenance"], identity["saved_uid"]["provenance"]), ("unknown", "not-applicable", "not-applicable"))
        self.assertEqual(result["probe_errors"], [])
        python = result["workload"]["runtimes"]["python"]
        self.assertFact(python["arch"], "aarch64", "observed")
        self.assertEqual(python["libc"]["provenance"], "unknown")
        self.assertValidRecord(result, policy=False)
        findings = validator.validate_document(result, SCHEMA)
        self.assertEqual(sorted({code for code in findings.codes() if code[len("policy-"):] in validator.RULE_SCOPE}), self.UNMEASURED)
        self.assertIn("policy-engine-rootless", findings.codes("warning"))

    def test_user_ids_off_linux(self):
        with FakeMachine(system="FreeBSD") as machine:
            result = machine.result()
        identity = result["workload"]["identity"]
        self.assertEqual([identity[name]["evidence"] for name in ("uid", "real_uid", "saved_uid")], [["python:os.getresuid"]] * 3)
        self.assertFact(result["workload"]["runtimes"]["python"]["platform"], "freebsd-14.1-RELEASE-amd64", "observed")
        # Under Cygwin or MSYS2 user ID 0 does not name the Windows administrator, so nothing is read.
        with FakeMachine(system="CYGWIN_NT-10.0-19045") as machine:
            identity = machine.result()["workload"]["identity"]
        self.assertEqual({identity[name]["provenance"] for name in ("superuser", "uid", "real_uid", "saved_uid")}, {"unknown"})
        # macOS has no getresuid: execve(2) saved the effective user ID, so the saved one is inferred.
        with without(collector.os, "getresuid"), mock.patch.object(collector.os, "geteuid", return_value=501), \
                mock.patch.object(collector.os, "getuid", return_value=501), FakeMachine(system="Darwin", interpreter="aarch64") as machine:
            result = machine.result()
        identity = result["workload"]["identity"]
        self.assertEqual(identity["uid"], {"value": 501, "provenance": "observed", "evidence": ["python:os.geteuid"]})
        self.assertEqual(identity["real_uid"], {"value": 501, "provenance": "observed", "evidence": ["python:os.getuid"]})
        self.assertEqual(identity["saved_uid"], {"value": 501, "provenance": "inferred", "evidence": ["python:os.geteuid"]})
        self.assertFact(identity["superuser"], False, "observed")
        self.assertEqual(identity["user_namespace"]["initial"]["provenance"], "unknown")
        self.assertValidRecord(result, policy=False)
        codes = validator.validate_document(result, SCHEMA).codes()
        self.assertNotIn("policy-app-non-root", codes)
        self.assertIn("policy-minimal-capabilities", codes)


class RunEvidenceTests(Case):
    def run_env(self, directory, event, payload):
        path = pathlib.Path(directory) / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return {
            "PATH": "", "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": event, "GITHUB_EVENT_PATH": str(path),
            "GITHUB_SHA": "a" * 40, "GITHUB_REF": "refs/pull/18/merge", "GITHUB_REPOSITORY": "P-R-N-D/Claus-AI",
            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_WORKFLOW_REF": "P-R-N-D/Claus-AI/.github/workflows/ci.yml@refs/pull/18/merge", "GITHUB_JOB": "compatibility",
            "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "ImageOS": "ubuntu24", "ImageVersion": "20261004.327.1",
            "GITHUB_TOKEN": "ghs_" + "S" * 36, "ACTIONS_RUNTIME_TOKEN": "runtime-token-sentinel",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.example/sentinel", "GITHUB_ACTOR": "private-actor",
            "GITHUB_WORKSPACE": "/home/private-runner/work", "HOME": "/home/private-runner",
        }

    def test_a_pull_request_run(self):
        payload = {
            "pull_request": {"head": {"sha": "b" * 40, "ref": "private-branch", "user": {"login": "private-login"}},
                             "base": {"sha": "c" * 40}, "title": "private title", "body": "private body"},
            "sender": {"login": "private-sender"},
        }
        with tempfile.TemporaryDirectory() as directory:
            env = self.run_env(directory, "pull_request", payload)
            with FakeMachine(env=env) as machine:
                result = machine.result()
        run = result["run"]
        self.assertEqual(value(run["ci"]["system"]), "github-actions")
        self.assertFact(run["ci"]["system"], "github-actions", "observed")
        self.assertFact(run["ci"]["run_url"], "https://github.com/P-R-N-D/Claus-AI/actions/runs/123456", "inferred")
        self.assertEqual(value(run["ci"]["run_attempt"]), 2)
        self.assertEqual(value(run["revision"]["event_sha"]), "a" * 40)
        self.assertEqual(value(run["revision"]["pr_head_sha"]), "b" * 40)
        self.assertEqual(value(run["revision"]["pr_base_sha"]), "c" * 40)
        self.assertEqual(value(run["revision"]["tested_sha"]), "0123456789abcdef0123456789abcdef01234567")
        text = json.dumps(result)
        for private in ("ghs_", "runtime-token-sentinel", "token.example", "private-", str(pathlib.Path(directory))):
            self.assertNotIn(private, text)
        profile = validator.load_json(ROOT / "tests" / "compatibility" / "profiles" / "linux-x64-gha.json")
        codes = validator.validate_document(result, SCHEMA, profile).codes()
        self.assertNotIn("ci-evidence", codes)
        self.assertNotIn("profile-link", codes)

    def test_other_events_and_bad_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            push = self.run_env(directory, "push", {"after": "d" * 40, "pusher": {"email": "private@example.com"}})
            with FakeMachine(env=push) as machine:
                revision = machine.result()["run"]["revision"]
            self.assertEqual(revision["pr_head_sha"]["provenance"], "not-applicable")
            for label, payload in (("no pull request", {}), ("bad sha", {"pull_request": {"head": {"sha": "B" * 40}, "base": {"sha": "short"}}})):
                with self.subTest(label), FakeMachine(env=self.run_env(directory, "pull_request", payload)) as machine:
                    result = machine.result()
                self.assertEqual(result["run"]["revision"]["pr_head_sha"]["provenance"], "unknown")
                self.assertEqual(result["probe_errors"], [])
                self.assertIn("ci-evidence", validator.validate_document(result, SCHEMA).codes())
            garbled = self.run_env(directory, "pull_request", {})
            pathlib.Path(garbled["GITHUB_EVENT_PATH"]).write_text("{not json", encoding="utf-8")
            with FakeMachine(env=garbled) as machine:
                result = machine.result()
            self.assertIn("could not be read (JSONDecodeError)", result["run"]["revision"]["pr_head_sha"]["reason"])
            self.assertEqual(value(result["run"]["ci"]["event"]), "pull_request")
            self.assertEqual(result["probe_errors"], [])
            missing = dict(self.run_env(directory, "pull_request", {}), GITHUB_EVENT_PATH=str(pathlib.Path(directory) / "missing.json"))
            with FakeMachine(env=missing) as machine:
                result = machine.result()
            self.assertIn("could not be read", result["run"]["revision"]["pr_head_sha"]["reason"])
            self.assertEqual(result["probe_errors"], [])
            target = {"pull_request": {"head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}}}
            with FakeMachine(env=self.run_env(directory, "pull_request_target", target)) as machine:
                revision = machine.result()["run"]["revision"]
            self.assertEqual((value(revision["pr_head_sha"]), value(revision["pr_base_sha"])), ("b" * 40, "c" * 40))
            no_path = {key: item for key, item in self.run_env(directory, "pull_request", {}).items() if key != "GITHUB_EVENT_PATH"}
            with FakeMachine(env=no_path) as machine:
                result = machine.result()
            self.assertEqual(result["run"]["revision"]["pr_head_sha"], {"value": None, "provenance": "unknown", "reason": "GITHUB_EVENT_PATH is not set"})
            self.assertIn("ci-evidence", validator.validate_document(result, SCHEMA).codes())
            odd = dict(push, GITHUB_REPOSITORY="owner/repo|x", RUNNER_ENVIRONMENT="custom", GITHUB_SHA="not-a-sha")
            with FakeMachine(env=odd) as machine:
                ci = machine.result()["run"]
            self.assertEqual(ci["ci"]["repository"]["provenance"], "unknown")
            self.assertEqual(ci["ci"]["runner_environment"]["provenance"], "unknown")
            self.assertEqual(ci["ci"]["run_url"]["provenance"], "unknown")
            self.assertEqual(ci["revision"]["event_sha"]["provenance"], "unknown")

    def test_outside_github_actions(self):
        with FakeMachine(env={"PATH": "", "GITHUB_SHA": "a" * 40}) as machine:
            run = machine.result()["run"]
        self.assertEqual(value(run["ci"]["system"]), "local")
        self.assertEqual(run["revision"]["event_sha"]["provenance"], "not-applicable")

    def test_checkout_commit(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        with FakeMachine() as machine:
            # A ref that leaves .git must not be followed, even to a file that holds a commit ID.
            (pathlib.Path(machine.directory.name) / "outside").write_text(sha + "\n", encoding="utf-8")
            for head, files, expected in (
                ("ref: refs/heads/main\n", {"refs/heads/main": sha + "\n"}, sha),
                ("ref: refs/heads/main\n", {"packed-refs": f"# pack-refs\n{sha} refs/heads/main\n"}, sha),
                ("ref: refs/heads/../../../etc/passwd\n", {}, None),
                ("ref: refs/../../outside\n", {}, None),
                ("garbage\n", {}, None),
            ):
                (machine.git / "HEAD").write_text(head, encoding="utf-8")
                for name, text in files.items():
                    path = machine.git / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                with self.subTest(head=head):
                    self.assertEqual(value(machine.probe().checkout_sha()), expected)
                for name in files:
                    (machine.git / name).unlink()

    def test_only_allowlisted_variables_are_read(self):
        probe = collector.Probe(env={"GITHUB_TOKEN": "x"})
        with self.assertRaises(collector.ProbeError):
            probe.env("GITHUB_TOKEN")
        for name in collector.PRESENCE_ONLY:
            with self.subTest(name), self.assertRaises(collector.ProbeError):
                probe.env_value(name, ".*")
        # WSL 2 is recognised from the kernel release; the WSL variables are not read.
        for name in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
            with self.subTest(name), self.assertRaises(collector.ProbeError):
                collector.Probe(env={name: "x"}).env(name)


class NodeTests(Case):
    def fake_node(self, directory, output, status=0, before="", after=""):
        script = pathlib.Path(directory) / "node"
        leak = pathlib.Path(directory) / "leak"
        script.write_text(
            "#!/bin/sh\n"
            f'if [ -n "${{SENTINEL_SECRET+x}}" ] || [ -n "${{HOME+x}}" ]; then echo leaked > "{leak}"; fi\n'
            f"{before}printf '%s' '{output}'\n{after}exit {status}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return leak

    def node(self, output, status=0, reference=("x86_64", "x86"), before="", after=""):
        with tempfile.TemporaryDirectory() as directory, FakeMachine(env={"PATH": directory}) as machine:
            leak = self.fake_node(directory, output, status, before, after)
            with mock.patch.dict(os.environ, {"SENTINEL_SECRET": "x", "HOME": "/home/private"}):
                probe = machine.probe(probe_node=True)
                facts = probe.guard("node", lambda: probe.node(reference), collector.node_fallback)
            self.assertFalse(leak.exists(), "node received the parent environment")
            return facts, probe.errors

    @unittest.skipUnless(os.name == "posix" and pathlib.Path("/bin/sh").exists(), "needs /bin/sh")
    def test_node_runs_with_an_empty_environment(self):
        facts, errors = self.node('{"v":"24.21.0","m":"137","n":"10","a":"x64","p":"linux"}')
        self.assertEqual(errors, [])
        self.assertEqual((value(facts["version"]), value(facts["modules_abi"]), value(facts["arch"]), value(facts["execution"])), ("24.21.0", "137", "x86_64", "native"))

    @unittest.skipUnless(os.name == "posix" and pathlib.Path("/bin/sh").exists(), "needs /bin/sh")
    def test_node_on_little_endian_power(self):
        # Node reports ppc64 for both byte orders; the name follows Node's own byte order, not this Python's.
        for endianness, arch, byteorder in (("LE", "ppc64le", "big"), ("BE", "ppc64", "little"), (None, "ppc64", "little")):
            output = json.dumps({"v": "24.21.0", "m": "137", "n": "10", "a": "ppc64", "p": "linux", "e": endianness})
            with self.subTest(endianness=endianness), mock.patch.object(collector.sys, "byteorder", byteorder):
                facts, _ = self.node(output, reference=("ppc64le", None))
                self.assertEqual((value(facts["arch"]), facts["arch"]["provenance"]), (arch, "observed"))
                self.assertEqual(value(facts["execution"]), "native" if arch == "ppc64le" else None)

    @unittest.skipUnless(os.name == "posix" and shutil.which("node"), "needs node on PATH")
    def test_the_expression_runs_on_a_real_node(self):
        # Run as the probe runs it, with an empty environment: every key the probe reads is there.
        output = subprocess.run([shutil.which("node"), "-p", collector.NODE_EXPRESSION], env={}, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30, check=True).stdout
        values = json.loads(output)
        self.assertEqual(set(values), {"v", "m", "n", "a", "p", "e"})
        self.assertIn(values["e"], ("LE", "BE"))

    @unittest.skipUnless(os.name == "posix" and pathlib.Path("/bin/sh").exists(), "needs /bin/sh")
    def test_node_failures(self):
        facts, errors = self.node("", status=3)
        self.assertEqual((facts["version"]["provenance"], errors), ("unknown", []))
        facts, errors = self.node("not json")
        self.assertEqual(errors, [{"probe": "node", "error": "JSONDecodeError raised by the node probe"}])
        facts, errors = self.node('{"v":"24.21.0; rm -rf /","m":"x","a":"x64","p":"linux"}')
        self.assertEqual((facts["version"]["provenance"], facts["modules_abi"]["provenance"]), ("unknown", "unknown"))
        for napi, platform_name, arch in (("10; x", "linux", "x64"), ("10", "Linux OS", "x64"), ("10", "linux", "x64 && id")):
            facts, _ = self.node(f'{{"v":"24.21.0","m":"137","n":"{napi}","a":"{arch}","p":"{platform_name}"}}')
            with self.subTest(napi=napi, platform=platform_name, arch=arch):
                self.assertIn("unknown", (facts["napi_version"]["provenance"], facts["platform"]["provenance"], facts["arch"]["provenance"]))

    @unittest.skipUnless(os.name == "posix" and pathlib.Path("/bin/sh").exists(), "needs /bin/sh")
    def test_node_output_and_time_are_bounded(self):
        # Only one byte past the limit is read; node is then stopped, even when it ignores the closed pipe and keeps running.
        started = time.monotonic()
        facts, errors = self.node("x" * (collector.NODE_OUTPUT_LIMIT + 1), before="trap '' PIPE\n", after="sleep 30\n")
        self.assertEqual((facts["version"]["reason"], errors), (f"node wrote more than {collector.NODE_OUTPUT_LIMIT} bytes", []))
        self.assertLess(time.monotonic() - started, 20)
        # The shell's own child holds stdout open, so stopping only the shell would leave the read waiting.
        started = time.monotonic()
        with mock.patch.object(collector, "NODE_TIMEOUT", 0.5):
            facts, errors = self.node('{"v":"24.21.0"}', before="sleep 30\n")
        self.assertEqual((facts["version"]["reason"], errors), ("node did not finish within 0.5 seconds", []))
        self.assertLess(time.monotonic() - started, 20)

    @unittest.skipUnless(os.name == "posix" and pathlib.Path("/bin/sh").exists(), "needs /bin/sh")
    def test_a_late_timer_does_not_signal_a_reaped_node(self):
        # A timer that fires after node was reaped must not signal its process ID, which may already be in use again.
        timers = []

        class Timer:
            def __init__(self, interval, function):
                self.function = function
                timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

        with mock.patch.object(collector.threading, "Timer", Timer), mock.patch.object(collector.os, "killpg") as killpg:
            facts, errors = self.node('{"v":"24.21.0","m":"137","n":"10","a":"x64","p":"linux"}')
            self.assertEqual((value(facts["version"]), errors), ("24.21.0", []))
            timers.pop().function()
        killpg.assert_not_called()

        # The same after the read fails and node is stopped and reaped on the way out.
        class Popen(subprocess.Popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                pipe = self.stdout

                class Broken:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        pipe.close()

                    def read(self, size):
                        raise OSError("read failed")

                self.stdout = Broken()

        with mock.patch.object(collector.threading, "Timer", Timer), mock.patch.object(collector.subprocess, "Popen", Popen):
            facts, errors = self.node('{"v":"24.21.0"}', after="sleep 30\n")
            self.assertEqual(errors, [{"probe": "node", "error": "OSError raised by the node probe"}])
            with mock.patch.object(collector.os, "killpg") as killpg:
                timers.pop().function()
        killpg.assert_not_called()

    def test_node_absent_or_skipped(self):
        with tempfile.TemporaryDirectory() as directory, FakeMachine(env={"PATH": directory}) as machine:
            self.assertEqual(machine.probe(probe_node=True).node(("x86_64", "x86"))["version"]["reason"], "node is not on PATH")
            self.assertEqual(machine.probe(probe_node=False).node(("x86_64", "x86"))["version"]["reason"], "not probed (--no-node)")


class CommandTests(Case):
    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                code = collector.main([str(arg) for arg in argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_stage_options(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            for argv in (
                ["--stage", "detect=passed"],
                ["--stage", "deploy=passed"],
                ["--stage", "install=ok"],
                ["--stage-reason", "install=x"],
                ["--stage", "install=failed", "--stage-reason", "install=line\nbreak"],
                ["--stage", "install=passed", "--stage-evidence", "install=https://example.com"],
                ["--stage", "install=passed", "--stage-escalation", "install=root"],
                ["--profile-id", "Bad Id"],
            ):
                with self.subTest(argv=argv):
                    self.assertEqual(self.run_main("--output", output, "--no-node", *argv)[0], 2)
            for option, value, label in (("--stage-reason", "install=token ghp_" + "A" * 36, "GitHub token"),
                                         ("--stage-evidence", "install=log:AKIA" + "ABCDEFGHIJKLMNOP", "AWS access key ID")):
                with self.subTest(option=option):
                    code, text = self.run_main("--output", output, "--no-node", "--stage", "install=failed", option, value)
                    self.assertEqual(code, 2)
                    self.assertIn(f"{option} install: looks like a {label}", text)
                    self.assertNotIn(value.split("=", 1)[1], text)
                    self.assertFalse(output.exists())
            code, _ = self.run_main("--output", output, "--no-node", "--profile-id", "linux-x64-gha",
                                    "--stage", "install=passed", "--stage-evidence", "install=log:uv pip sync",
                                    "--stage-escalation", "install=none", "--stage", "performance=not-applicable",
                                    "--stage-reason", "performance=not measured on shared runners")
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn(code, (0, 1))
            self.assertEqual(result["stages"]["install"], {"status": "passed", "evidence": ["log:uv pip sync"], "privilege": {"escalation": {"value": "none", "provenance": "declared"}}})
            self.assertEqual(result["stages"]["initialize"]["status"], "not-run")
            self.assertEqual(result["stages"]["performance"]["status"], "not-applicable")

    def test_a_record_that_looks_like_it_holds_a_secret_is_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            with mock.patch.object(collector.platform, "python_version", return_value="3.12.15-ghp_" + "A" * 36):
                code, text = self.run_main("--output", output, "--no-node")
            self.assertEqual(code, 2)
            self.assertIn("looks like a GitHub token; it was not written", text)
            self.assertNotIn("ghp_", text)
            self.assertFalse(output.exists())

    def test_the_secret_shapes_match_the_validator(self):
        self.assertEqual([(label, shape.pattern) for label, shape in collector.SECRET_SHAPES],
                         [(label, shape.pattern) for label, shape in validator.SECRET_SHAPES])

    # One sample per shape, built from parts so the repository holds no token-shaped literal.
    SECRET_SAMPLES = (
        *(("gh" + kind + "_" + "A" * 36) for kind in "opsur"),
        "github" + "_pat_" + "A" * 22,
        "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "AS" + "IA" + "ABCDEFGHIJKLMNOP",
        "-----BEGIN " + "RSA PRIVATE KEY-----",
        *(("xo" + "x" + kind + "-" + "A" * 12) for kind in "abposr"),
        "ey" + "J" + "A" * 10 + ".ey" + "J" + "B" * 10 + ".",
    )

    def test_every_secret_shape_is_refused(self):
        for label, shape in collector.SECRET_SHAPES:
            with self.subTest(shape=shape.pattern):
                self.assertTrue(any(shape.search(sample) for sample in self.SECRET_SAMPLES), f"no sample for {label}")
        record = validator.load_json(ROOT / "tests" / "compatibility" / "fixtures" / "linux-x64-vm.result.json")
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            for sample in self.SECRET_SAMPLES:
                with self.subTest(sample=sample[:6]):
                    output.unlink(missing_ok=True)
                    code, text = self.run_main("--output", output, "--no-node", "--stage", "install=failed", "--stage-reason", f"install=see {sample}")
                    self.assertEqual(code, 2)
                    self.assertNotIn(sample, text)
                    self.assertFalse(output.exists())
                    document = json.loads(json.dumps(record))
                    document["stages"]["install"]["reason"] = f"see {sample}"
                    self.assertIn("secret-like-value", validator.validate_document(document, SCHEMA).codes())
            # The check reads the JSON text, where a character before the value may be an escape ending in a letter.
            for prefix in ("\n", "\u200e"):
                for sample in self.SECRET_SAMPLES:
                    with self.subTest(prefix=prefix, sample=sample[:6]):
                        output.unlink(missing_ok=True)
                        document = json.loads(json.dumps(record))
                        document["stages"]["install"]["reason"] = prefix + sample
                        with mock.patch.object(collector, "build_result", return_value=document):
                            code, text = self.run_main("--output", output, "--no-node")
                        self.assertEqual(code, 2)
                        self.assertNotIn(sample, text)
                        self.assertFalse(output.exists())

    def test_a_probe_failure_exits_1_after_writing_the_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            with mock.patch.object(collector.Probe, "identity", side_effect=ValueError("x")):
                code, text = self.run_main("--output", output, "--no-node")
            self.assertEqual(code, 1)
            self.assertIn("probe error: identity", text)
            result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["stages"]["detect"]["status"], "failed")
        self.assertEqual(result["probe_errors"][0]["probe"], "identity")

    def test_the_output_name_cannot_forge_output_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("::warning title=forged::x.json", "x##[warning]y.json", "gh" + "p_" + "A" * 36 + ".json"):
                with self.subTest(name=name[:6]):
                    code, text = self.run_main("--output", pathlib.Path(directory) / name, "--no-node")
                    self.assertIn("not shown: looks like a GitHub token" if "A" * 36 in name else ": detect ", text)
                    self.assertFalse([line for line in text.splitlines() if line.lstrip().startswith("::") or "##[" in line], text)
                    self.assertNotIn("A" * 36, text)
        # The collector writes its lines as the validator does.
        self.assertEqual(collector.CONTROL.pattern, validator.CONTROL.pattern)
        for sample in ("::a##[b", "a\nb\u2028c", *self.SECRET_SAMPLES, *(repr("\n" + sample) for sample in self.SECRET_SAMPLES)):
            with self.subTest(sample=sample[:6]):
                self.assertEqual(collector.plain(sample), validator.plain(sample))

    def test_an_unwritable_output_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_main("--output", pathlib.Path(directory) / "missing" / "r.json", "--no-node")[0], 2)

    def test_an_unwritable_summary_is_reported_as_the_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            code, text = self.run_main("--output", output, "--summary", pathlib.Path(directory) / "missing" / "summary.md", "--no-node")
            self.assertEqual(code, 2)
            self.assertIn("the record was written, but the summary could not be", text)
            self.assertTrue(output.is_file())
            code, text = self.run_main("--output", output, "--no-node")
        self.assertNotIn(directory, text)

    def test_the_summary_escapes_table_cells(self):
        self.assertEqual(collector.markdown_cell("a|b`c\nd"), "a\\|b\\`c d")
        self.assertEqual(collector.markdown_cell("[x](https://example.invalid) <b>\\"), "\\[x\\]\\(https://example.invalid\\) \\<b\\>\\\\")
        # A VERSION_ID with Markdown in it passes the printable check, so the summary must escape it.
        hostile = 'ID=ubuntu\nVERSION_ID="24.04|**x**](https://example.invalid)`<b>"\n'
        with FakeMachine(dict(RUNNER_FILES, **{"etc/os-release": hostile})) as machine:
            result = machine.result()
        self.assertEqual(value(result["environment"]["layers"][1]["os"]["version"]), "24.04|**x**](https://example.invalid)`<b>")
        rows = [line for line in collector.summary_lines(result) if line.startswith("| ")]
        self.assertTrue(any("24.04" in row for row in rows))
        for row in rows:
            with self.subTest(row=row[:30]):
                self.assertEqual(unescaped_pipes(row), 3)
                self.assertIsNone(re.search(r"(?<!\\)\]\(|(?<!\\)[`<*]", row.replace("\\\\", "")))


class ThisMachineTests(Case):
    SENTINELS = {
        "GITHUB_TOKEN": "ghs_" + "Q" * 36,
        "ACTIONS_RUNTIME_TOKEN": "sentinel-runtime-token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "sentinel-id-token",
        "ACTIONS_RESULTS_URL": "https://sentinel-results.example/",
        "AWS_SECRET_ACCESS_KEY": "sentinel-aws-secret",
        "DATABASE_URL": "postgres://sentinel-user:sentinel-password@db.example/x",
        "GITHUB_ACTOR": "sentinel-actor",
        "HOME": "/home/sentinel-home",
        "USER": "sentinel-user-name",
        "KUBERNETES_SERVICE_HOST": "10.254.254.254",
        "WSL_DISTRO_NAME": "SentinelDistro",
        "container": "sentinel-engine",
        "_PYTHON_HOST_PLATFORM": "ghp_" + "S" * 36,
    }

    def collect(self, extra_env):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "result.json"
            env = {"PATH": os.environ.get("PATH", os.defpath), **extra_env}
            completed = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--output", str(output), "--no-node"],
                env=env, cwd=directory, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertIn(completed.returncode, (0, 1), completed.stderr)
            return output.read_text(encoding="utf-8"), completed

    def test_no_secret_or_identifier_reaches_the_record(self):
        text, completed = self.collect(self.SENTINELS)
        for name, secret in self.SENTINELS.items():
            with self.subTest(name):
                self.assertNotIn(secret, text)
                self.assertNotIn(secret, completed.stdout + completed.stderr)
        hostname = socket.gethostname()
        if len(hostname) >= 4:
            self.assertNotIn(hostname, text)
        home = os.path.expanduser("~")
        if home not in ("/", "~") and len(home) > 5:
            self.assertNotIn(home, text)

    def test_this_machine_gives_a_valid_record(self):
        text, completed = self.collect({})
        result = json.loads(text)
        self.assertIs(result["synthetic"], False)
        self.assertEqual(completed.returncode, 0 if not result["probe_errors"] else 1)
        self.assertValidRecord(result, policy=False)


class SharedTableTests(unittest.TestCase):
    """The collector and the validator each keep a copy of these tables; the copies must agree."""

    def test_architecture_names(self):
        self.assertEqual(collector.ARCH_ALIASES, validator.ARCH_ALIASES)
        # The names the ELF header can give, and which of them are 32-bit, match the validator's pointer widths.
        self.assertEqual(collector.KNOWN_ARCHES, set(validator.ARCH_BITS))
        self.assertEqual(collector.ELF_32_BIT, {arch for arch, bits in validator.ARCH_BITS.items() if bits == 32})

    def test_platform_tags(self):
        for plat in ("linux-x86_64", "linux-aarch64", "linux-armv7l", "linux-i686", "win32", "win-amd64", "win-arm64", "win-arm32",
                     "macosx-11.0-arm64", "macosx-10.9-x86_64", "macosx-10.9-universal2", "freebsd-14.1-RELEASE-amd64",
                     "freebsd-14.1-RELEASE-arm64", "cygwin-3.5.4-x86_64", "linux"):
            with self.subTest(plat=plat):
                self.assertEqual(collector.split_platform(plat), validator.python_platform_target(plat))
        for plat, target in (("freebsd-14.1-RELEASE-amd64", ("freebsd", "x86_64")), ("freebsd-14.1-RELEASE-p6-arm64", ("freebsd", "aarch64")),
                             ("macosx-11.0-arm64", ("macos", "aarch64")), ("win-arm64", ("windows", "aarch64")), ("linux-x86_64", ("linux", "x86_64"))):
            with self.subTest(plat=plat):
                self.assertEqual(collector.split_platform(plat), target)

    def test_layer_technologies(self):
        # Every technology the collector can write is on the kind of layer the validator binds it to.
        for technology in {name for _, name in collector.DMI_VENDORS} | {"hyper-v"}:
            self.assertEqual(validator.TECHNOLOGY_KINDS.get(technology), "vm", technology)
        for technology in {name for name in collector.CONTAINER_TECHNOLOGIES.values() if name} | {"docker", "podman", "apptainer"}:
            self.assertEqual(validator.TECHNOLOGY_KINDS.get(technology), "container", technology)
        self.assertEqual(validator.TECHNOLOGY_KINDS.get("wsl2"), "wsl2")

    def test_stages_and_the_overall_verdict(self):
        self.assertEqual(collector.STAGES, validator.STAGES)
        for statuses in itertools.product(collector.STATUSES, repeat=len(collector.STAGES)):
            stages = {name: {"status": status} for name, status in zip(collector.STAGES, statuses)}
            self.assertEqual(collector.overall_of(stages), validator.overall_of(stages), statuses)


class SourceTests(unittest.TestCase):
    """The collector's own code may not reach for host names, account names, the network or the whole environment."""

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def names(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute):
                parts = [node.attr]
                inner = node.value
                while isinstance(inner, ast.Attribute):
                    parts.append(inner.attr)
                    inner = inner.value
                if isinstance(inner, ast.Name):
                    parts.append(inner.id)
                    yield node, ".".join(reversed(parts))

    def test_no_forbidden_modules(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        allowed = {"argparse", "datetime", "json", "os", "pathlib", "platform", "re", "shutil", "signal", "stat", "struct", "subprocess", "sys", "sysconfig", "threading", "time"}
        self.assertEqual(imported - allowed, set())

    def test_no_forbidden_calls(self):
        forbidden = {"platform.node", "platform.uname", "os.uname", "os.getlogin", "os.environ.items", "os.environ.keys",
                     "os.environ.values", "os.environ.copy", "os.system", "os.popen", "os.putenv", "os.setuid", "os.seteuid"}
        used = {name for _, name in self.names()}
        self.assertEqual(used & forbidden, set())
        environ = [node for node, name in self.names() if name == "os.environ"]
        self.assertEqual(len(environ), 1, "os.environ is read only through Probe.env")

    def test_one_subprocess_without_a_shell(self):
        calls = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
        self.assertEqual([call.func.attr for call in calls], ["Popen"])
        keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
        self.assertNotIn("shell", keywords)
        self.assertIsInstance(keywords["env"], ast.Dict)
        self.assertEqual(keywords["env"].keys, [])
        # stderr is discarded, and stdout is read through the bounded reader in Probe.node.
        self.assertEqual(ast.unparse(keywords["stderr"]), "subprocess.DEVNULL")

    def test_files_are_opened_for_reading_except_the_outputs(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open" and len(node.args) > 1:
                mode = node.args[1].value if isinstance(node.args[1], ast.Constant) else None
                with self.subTest(line=node.lineno):
                    self.assertIn(mode, ("rb", "a"))
        main = next(node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        writers = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("write_text", "write_bytes", "mkdir", "unlink", "rmdir", "chmod")]
        self.assertEqual([node.lineno for node in writers], [node.lineno for node in ast.walk(main) if node in writers])


if __name__ == "__main__":
    unittest.main()
