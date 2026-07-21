import unittest

from steamtrain import sysinfo


def fake_reader(files):
    def read_text(path):
        return files.get(str(path))

    return read_text


NVIDIA_FILES = {
    "/etc/os-release": 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\n',
    "/sys/module/nvidia/version": "595.71.05\n",
    "/proc/modules": "nvidia_uvm 1 0 - Live\nnvidia 1 400 - Live\n",
    "/proc/cpuinfo": "processor\t: 0\nprocessor\t: 1\n",
    "/proc/meminfo": "MemTotal:       32767952 kB\n",
}

AMD_FILES = {
    "/etc/os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\n',
    "/proc/modules": "amdgpu 1 99 - Live\n",
    "/proc/cpuinfo": "processor\t: 0\n",
    "/proc/meminfo": "MemTotal:       16000000 kB\n",
}


class TestSysinfo(unittest.TestCase):
    def test_nvidia_wayland_gnome(self):
        p = sysinfo.detect(
            env={
                "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
                "XDG_SESSION_TYPE": "wayland",
            },
            read_text=fake_reader(NVIDIA_FILES),
            which=lambda name: "/usr/games/gamemoderun" if name == "gamemoderun" else None,
        )
        self.assertEqual(p.gpu_vendor, "nvidia")
        self.assertEqual(p.gpu_driver, "595.71.05")
        self.assertEqual(p.desktop, "GNOME")
        self.assertEqual(p.session, "wayland")
        self.assertEqual(p.distro, "Ubuntu 24.04.4 LTS")
        self.assertEqual(p.cpu_threads, 2)
        self.assertEqual(p.ram_gb, 31)
        self.assertTrue(p.has_gamemode)
        self.assertFalse(p.has_mangohud)

    def test_amd_x11(self):
        p = sysinfo.detect(
            env={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "x11"},
            read_text=fake_reader(AMD_FILES),
            which=lambda name: None,
        )
        self.assertEqual(p.gpu_vendor, "amd")
        self.assertEqual(p.desktop, "KDE")
        self.assertEqual(p.session, "x11")
        self.assertFalse(p.has_gamemode)

    def test_wayland_socket_fallback(self):
        files = dict(AMD_FILES)
        files["/run/user/1000/wayland-0"] = ""  # socket exists -> readable marker
        p = sysinfo.detect(
            env={"XDG_RUNTIME_DIR": "/run/user/1000"},
            read_text=fake_reader(files),
            which=lambda name: None,
            path_exists=lambda path: str(path) in files,
        )
        self.assertEqual(p.session, "wayland")

    def test_unknown_everything(self):
        p = sysinfo.detect(
            env={},
            read_text=fake_reader({}),
            which=lambda name: None,
            path_exists=lambda path: False,
        )
        self.assertEqual(p.gpu_vendor, "unknown")
        self.assertEqual(p.session, "unknown")
        self.assertEqual(p.desktop, "unknown")

    def test_real_machine_smoke(self):
        p = sysinfo.detect()
        self.assertIn(p.gpu_vendor, ("nvidia", "amd", "intel", "unknown"))
        self.assertGreater(p.cpu_threads, 0)
        self.assertGreater(p.ram_gb, 0)


if __name__ == "__main__":
    unittest.main()
