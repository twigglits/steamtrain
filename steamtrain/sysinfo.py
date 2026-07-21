"""System profile detection: OS, desktop environment, GPU, CPU, helper tools.

All inputs are injectable for testing; defaults read the live system. Works
without a desktop session in the environment (e.g. under a systemd user
timer) by falling back to the wayland socket in XDG_RUNTIME_DIR.
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SystemProfile:
    distro: str
    kernel: str
    desktop: str
    session: str  # 'wayland' | 'x11' | 'unknown'
    gpu_vendor: str  # 'nvidia' | 'amd' | 'intel' | 'unknown'
    gpu_name: str
    gpu_driver: str
    cpu_threads: int
    ram_gb: int
    has_gamemode: bool
    has_mangohud: bool
    has_gamescope: bool


def _default_read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _detect_distro(read_text):
    text = read_text("/etc/os-release") or ""
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _detect_desktop(env):
    desktop = env.get("XDG_CURRENT_DESKTOP", "")
    if desktop:
        # "ubuntu:GNOME" -> "GNOME"
        return desktop.split(":")[-1]
    return "unknown"


def _detect_session(env, path_exists):
    session = env.get("XDG_SESSION_TYPE", "")
    if session in ("wayland", "x11"):
        return session
    runtime_dir = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if path_exists(Path(runtime_dir) / "wayland-0"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def _detect_gpu(read_text, which):
    """Return (vendor, name, driver) from kernel modules and sysfs."""
    nvidia_version = read_text("/sys/module/nvidia/version")
    if nvidia_version:
        name = ""
        smi = which("nvidia-smi")
        if smi:
            import subprocess

            try:
                name = subprocess.run(
                    [smi, "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip().splitlines()[0]
            except (OSError, subprocess.SubprocessError, IndexError):
                name = ""
        return "nvidia", name or "NVIDIA GPU", nvidia_version.strip()
    modules = read_text("/proc/modules") or ""
    loaded = {line.split(" ", 1)[0] for line in modules.splitlines()}
    if "amdgpu" in loaded or "radeon" in loaded:
        return "amd", "AMD GPU", "amdgpu (Mesa)"
    if "i915" in loaded or "xe" in loaded:
        return "intel", "Intel GPU", "i915/xe (Mesa)"
    return "unknown", "", ""


def detect(env=None, read_text=None, which=None, path_exists=None):
    env = os.environ if env is None else env
    read_text = _default_read_text if read_text is None else read_text
    which = shutil.which if which is None else which
    path_exists = (lambda p: Path(p).exists()) if path_exists is None else path_exists

    cpuinfo = read_text("/proc/cpuinfo") or ""
    cpu_threads = cpuinfo.count("processor\t") or os.cpu_count() or 1

    meminfo = read_text("/proc/meminfo") or ""
    ram_gb = 0
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            ram_gb = int(line.split()[1]) // (1024 * 1024)
            break

    gpu_vendor, gpu_name, gpu_driver = _detect_gpu(read_text, which)

    return SystemProfile(
        distro=_detect_distro(read_text),
        kernel=os.uname().release,
        desktop=_detect_desktop(env),
        session=_detect_session(env, path_exists),
        gpu_vendor=gpu_vendor,
        gpu_name=gpu_name,
        gpu_driver=gpu_driver,
        cpu_threads=cpu_threads,
        ram_gb=ram_gb,
        has_gamemode=bool(which("gamemoderun")),
        has_mangohud=bool(which("mangohud")),
        has_gamescope=bool(which("gamescope")),
    )
