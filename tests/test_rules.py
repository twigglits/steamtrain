import json
import tempfile
import unittest
from pathlib import Path

from steamtrain import rules
from steamtrain.steam import Game
from steamtrain.sysinfo import SystemProfile


def profile(**kw):
    base = dict(
        distro="Ubuntu 24.04.4 LTS", kernel="6.17", desktop="GNOME",
        session="wayland", gpu_vendor="nvidia", gpu_name="RTX 5090",
        gpu_driver="595.71.05", cpu_threads=16, ram_gb=31,
        has_gamemode=True, has_mangohud=False, has_gamescope=True,
    )
    base.update(kw)
    return SystemProfile(**base)


def game(appid="100", runtime="proton"):
    return Game(appid=appid, name="Game", installdir=Path("/g"), library=Path("/l"), runtime=runtime)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.config = rules.default_config()

    def test_nvidia_proton(self):
        opts = rules.build_options(game(runtime="proton"), profile(), self.config)
        self.assertEqual(
            opts,
            "PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%",
        )

    def test_nvidia_native_no_proton_vars(self):
        opts = rules.build_options(game(runtime="native"), profile(), self.config)
        self.assertEqual(opts, "__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%")

    def test_unknown_runtime_treated_as_native(self):
        opts = rules.build_options(game(runtime="unknown"), profile(), self.config)
        self.assertNotIn("PROTON_ENABLE_NVAPI", opts)

    def test_amd_native_mesa_glthread(self):
        p = profile(gpu_vendor="amd", has_gamemode=False)
        opts = rules.build_options(game(runtime="native"), p, self.config)
        self.assertEqual(opts, "mesa_glthread=true %command%")

    def test_no_tools_no_vendor(self):
        p = profile(gpu_vendor="unknown", has_gamemode=False)
        opts = rules.build_options(game(runtime="native"), p, self.config)
        self.assertEqual(opts, "%command%")

    def test_mangohud_enabled_and_present(self):
        cfg = dict(self.config, enable_mangohud=True)
        opts = rules.build_options(game(), profile(has_mangohud=True), cfg)
        self.assertIn("gamemoderun mangohud %command%", opts)

    def test_mangohud_enabled_but_absent(self):
        cfg = dict(self.config, enable_mangohud=True)
        opts = rules.build_options(game(), profile(has_mangohud=False), cfg)
        self.assertNotIn("mangohud", opts)

    def test_proton_wayland_opt_in(self):
        cfg = dict(self.config, enable_proton_wayland=True)
        opts = rules.build_options(game(runtime="proton"), profile(session="wayland"), cfg)
        self.assertIn("PROTON_ENABLE_WAYLAND=1", opts)
        # not applied on x11 sessions
        opts_x11 = rules.build_options(game(runtime="proton"), profile(session="x11"), cfg)
        self.assertNotIn("PROTON_ENABLE_WAYLAND", opts_x11)

    def test_override_verbatim(self):
        cfg = dict(self.config, overrides={"100": "MANGOHUD=1 %command% -nolauncher"})
        opts = rules.build_options(game("100"), profile(), cfg)
        self.assertEqual(opts, "MANGOHUD=1 %command% -nolauncher")

    def test_override_extends_baseline_with_auto(self):
        cfg = dict(self.config, overrides={"100": "{auto} -dx11"})
        opts = rules.build_options(game("100"), profile(), cfg)
        self.assertEqual(
            opts,
            "PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command% -dx11",
        )

    def test_excluded_appid_returns_none(self):
        cfg = dict(self.config, exclude=["100"])
        self.assertIsNone(rules.build_options(game("100"), profile(), cfg))

    def test_load_config_creates_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = rules.load_config(path)
            self.assertTrue(path.is_file())
            self.assertEqual(cfg["enable_gamemode"], True)
            # user edits survive a reload and merge over defaults
            data = json.loads(path.read_text())
            data["enable_mangohud"] = True
            data["overrides"]["42"] = "{auto} -windowed"
            path.write_text(json.dumps(data))
            cfg2 = rules.load_config(path)
            self.assertTrue(cfg2["enable_mangohud"])
            self.assertEqual(cfg2["overrides"]["42"], "{auto} -windowed")
            self.assertIn("exclude", cfg2)

    def test_baseline_ignores_existing_override(self):
        cfg = dict(self.config, overrides={"100": "{auto} -dx11"})
        self.assertEqual(
            rules.baseline(game("100"), profile(), cfg),
            "PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%",
        )

    def test_save_override_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            rules.load_config(path)  # writes documented default
            data = json.loads(path.read_text())
            data["enable_mangohud"] = True
            path.write_text(json.dumps(data))
            rules.save_override(path, "100", "{auto} -dx11")
            reloaded = json.loads(path.read_text())
            self.assertEqual(reloaded["overrides"]["100"], "{auto} -dx11")
            self.assertTrue(reloaded["enable_mangohud"])  # untouched

    def test_advisor_command_default(self):
        self.assertEqual(rules.default_config()["advisor_command"], "claude -p")


if __name__ == "__main__":
    unittest.main()
