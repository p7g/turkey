"""How many run permits a program gets when `TURKEY_WORKERS` is unset.

On Linux, one per CPU in the affinity mask, lowered to the cgroup v2
`cpu.max` limit, rounded up and raised to 2 but never above the CPUs, where
that is lower (`Turkey.Cores`). `TURKEY_TEST_CGROUP_ROOT` names a directory
read in place of `/` for the cgroup's files, so these give a program any
limit without a cgroup of its own. On Darwin, one per performance core, and
the hook changes nothing.
"""

import os
import shutil
import subprocess
import sys

import pytest

from tests import bootc, toolchain

PROGRAM = """
import System.Parallel as Parallel

fun main() { print(Parallel.workers()) }
"""

HOOK = "TURKEY_TEST_CGROUP_ROOT"

linux = pytest.mark.skipif(sys.platform != "linux", reason="cgroups and affinity are Linux's")


@pytest.fixture(scope="module")
def program(tmp_path_factory):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    directory = tmp_path_factory.mktemp("cores")
    entry = directory / "main.gob"
    entry.write_text(PROGRAM)
    binary = directory / "workers"
    bootc.link(entry, binary)
    return binary


def workers(program, *prefix, **env):
    """The permits `program` runs with, `TURKEY_WORKERS` unset unless `env`
    sets it."""
    environment = {k: v for k, v in os.environ.items() if k != "TURKEY_WORKERS"}
    environment.update(env)
    result = subprocess.run([*prefix, *toolchain.command(program)], env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return int(result.stdout)


def cgroup(tmp_path, cpu_max, *, path="/app.slice/app", mount_root="/"):
    """A root holding a cgroup2 mount at `/sys/fs/cgroup`, and a process in
    the cgroup at `path` whose `cpu.max` is `cpu_max`, or has none if it is
    None."""
    root = tmp_path / "root"
    (root / "proc/self").mkdir(parents=True)
    (root / "proc/self/cgroup").write_text(f"0::{path}\n")
    (root / "proc/self/mountinfo").write_text(
        "22 1 0:21 / /proc rw,nosuid shared:12 - proc proc rw\n"
        f"35 25 0:30 {mount_root} /sys/fs/cgroup rw,nosuid,nodev shared:9"
        " - cgroup2 cgroup2 rw,nsdelegate\n")
    below = path[len(mount_root):] if mount_root != "/" else path
    directory = root / "sys/fs/cgroup" / below.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    if cpu_max is not None:
        (directory / "cpu.max").write_text(cpu_max + "\n")
    return str(root)


def cpus():
    return len(os.sched_getaffinity(0))


def limited(n):
    """The permits for a limit of `n` whole CPUs."""
    return min(cpus(), max(n, 2))


@linux
@pytest.mark.parametrize("cpu_max, whole", [
    ("200000 100000", 2),
    ("150000 100000", 2),
    ("250000 100000", 3),
    ("100000 100000", 1),
    ("50000 100000", 1),
    ("1000 1000000", 1),
])
def test_a_cpu_limit_lowers_the_permits(program, tmp_path, cpu_max, whole):
    root = cgroup(tmp_path, cpu_max)
    assert workers(program, **{HOOK: root}) == limited(whole)


@linux
@pytest.mark.parametrize("cpu_max", ["max 100000", None, "garbage"])
def test_no_cpu_limit_leaves_one_permit_per_cpu(program, tmp_path, cpu_max):
    root = cgroup(tmp_path, cpu_max)
    assert workers(program, **{HOOK: root}) == cpus()


@linux
def test_the_mount_root_is_taken_off_the_cgroup_path(program, tmp_path):
    # A container without its own cgroup namespace: its cgroup is the
    # mount's root, and `/proc/self/cgroup` names it from the host's root.
    root = cgroup(tmp_path, "200000 100000", path="/docker/abc", mount_root="/docker/abc")
    assert workers(program, **{HOOK: root}) == limited(2)


@linux
def test_turkey_workers_wins_over_a_cpu_limit(program, tmp_path):
    root = cgroup(tmp_path, "200000 100000")
    assert workers(program, TURKEY_WORKERS="3", **{HOOK: root}) == 3


@linux
def test_an_affinity_mask_lowers_the_permits(program):
    if shutil.which("taskset") is None:
        pytest.skip("no taskset")
    if cpus() < 2:
        pytest.skip("fewer than two CPUs")
    allowed = sorted(os.sched_getaffinity(0))[:2]
    mask = ",".join(map(str, allowed))
    assert workers(program, "taskset", "-c", mask) == 2


@linux
def test_one_cpu_gets_one_permit_whatever_the_limit(program, tmp_path):
    if shutil.which("taskset") is None:
        pytest.skip("no taskset")
    root = cgroup(tmp_path, "50000 100000")
    first = str(min(os.sched_getaffinity(0)))
    assert workers(program, "taskset", "-c", first, **{HOOK: root}) == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="performance cores are Darwin's")
def test_darwin_counts_performance_cores_and_ignores_the_hook(program, tmp_path):
    def sysctl(name):
        result = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True)
        return int(result.stdout) if result.returncode == 0 else 0
    expected = sysctl("hw.perflevel0.physicalcpu") or sysctl("hw.physicalcpu")
    root = cgroup(tmp_path, "100000 100000")
    assert workers(program) == expected
    assert workers(program, **{HOOK: root}) == expected
