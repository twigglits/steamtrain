import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steamtrain import cli
from steamtrain import sysinfo
from steamtrain import vdf

from tests.test_steam import make_manifest, make_steam_root


def fake_profile(vendor="unknown", **overrides):
    fields = dict(
        distro="Arch Linux", kernel="6.9.0", desktop="KDE", session="wayland",
        gpu_vendor=vendor, gpu_name="", gpu_driver="",
        cpu_threads=8, ram_gb=16,
        has_gamemode=False, has_mangohud=False, has_gamescope=False,
    )
    fields.update(overrides)
    return sysinfo.SystemProfile(**fields)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = make_steam_root(base)
        make_manifest(self.root, "100", "Fixture Game", "FixtureGame")
        cfg = self.root / "userdata" / "111" / "config"
        cfg.mkdir(parents=True)
        self.localconfig = cfg / "localconfig.vdf"
        self.localconfig.write_text('"UserLocalConfigStore"\n{\n}\n')
        self.state_dir = base / "state"
        self.config_path = base / "config.json"

    def run_cli(self, *args):
        out = io.StringIO()
        argv = [
            *args,
            "--steam-root", str(self.root),
            "--state-dir", str(self.state_dir),
            "--config", str(self.config_path),
        ]
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def run_setup(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["setup", "--config", str(self.config_path)])
        return code, out.getvalue()

    def current_options(self):
        data = vdf.loads(self.localconfig.read_text())
        apps = data["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
        return apps["100"]["LaunchOptions"]

    def test_scan_lists_games_with_proposals(self):
        code, out = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("Fixture Game", out)
        self.assertIn("%command%", out)

    def test_apply_dry_run_writes_nothing(self):
        before = self.localconfig.read_text()
        code, out = self.run_cli("apply", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out.lower())
        self.assertEqual(self.localconfig.read_text(), before)

    def test_apply_then_status_then_revert(self):
        code, out = self.run_cli("apply")
        self.assertEqual(code, 0)
        self.assertIn("%command%", self.current_options())

        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("100", out)

        code, out = self.run_cli("revert")
        self.assertEqual(code, 0)
        self.assertEqual(self.current_options(), "")

    def test_apply_is_idempotent(self):
        self.run_cli("apply")
        code, out = self.run_cli("apply")
        self.assertEqual(code, 0)
        self.assertIn("0 set", out)

    def _advise(self, *extra, selector="100", override="{auto} -dx11", confidence="high"):
        payload = {"override": override, "reasoning": "stabler on NVIDIA", "confidence": confidence}
        with mock.patch("steamtrain.advisor.protondb_summary", return_value=None), \
             mock.patch("steamtrain.advisor.run_llm", return_value=payload):
            return self.run_cli("advise", selector, *extra)

    def test_advise_propose_only_writes_nothing(self):
        code, out = self._advise()
        self.assertEqual(code, 0)
        self.assertIn("-dx11", out)
        self.assertIn("--write", out)
        data = json.loads(self.config_path.read_text())
        self.assertNotIn("100", data.get("overrides", {}))

    def test_advise_write_saves_override(self):
        code, out = self._advise("--write")
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["overrides"]["100"], "{auto} -dx11")

    def test_advise_rejects_unsafe_override(self):
        code, out = self._advise("--write", override="rm -rf ~ %command%")
        self.assertEqual(code, 1)
        data = json.loads(self.config_path.read_text())
        self.assertNotIn("100", data.get("overrides", {}))

    def test_advise_unknown_appid_errors(self):
        with mock.patch("steamtrain.advisor.protondb_summary", return_value=None), \
             mock.patch("steamtrain.advisor.run_llm", return_value={"override": None}):
            code, out = self.run_cli("advise", "999999")
        self.assertEqual(code, 1)

    def test_advise_by_name_substring(self):
        code, out = self._advise(selector="fixture")  # substring of "Fixture Game"
        self.assertEqual(code, 0)
        self.assertIn("-dx11", out)

    def test_advise_no_arg_lists_games(self):
        code, out = self.run_cli("advise")
        self.assertEqual(code, 0)
        self.assertIn("Fixture Game", out)
        self.assertIn("100", out)

    def test_advise_ambiguous_name_errors(self):
        make_manifest(self.root, "200", "Fixture Two", "FixtureTwo")
        code, out = self.run_cli("advise", "fixture")  # matches both games
        self.assertEqual(code, 1)

    def test_advise_unique_name_among_many(self):
        make_manifest(self.root, "200", "Totally Different", "TotDiff")
        code, out = self._advise(selector="totally")  # matches only "Totally Different"
        self.assertEqual(code, 0)

    def test_setup_unknown_vendor_persists_choice(self):
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", return_value="1"):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "nvidia")

    def test_setup_detected_vendor_does_not_prompt(self):
        profile = fake_profile("nvidia", gpu_name="NVIDIA GPU", gpu_driver="595.71.05")
        with mock.patch("steamtrain.sysinfo.detect", return_value=profile), \
             mock.patch("builtins.input") as inp:
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        inp.assert_not_called()
        self.assertIn("nvidia", out)
        data = json.loads(self.config_path.read_text())
        # load_config creates the documented default file; the point is that
        # no vendor was written on the detected path.
        self.assertEqual(data["gpu_vendor"], "")

    def test_setup_reprompts_until_valid(self):
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", side_effect=["9", "nonsense", "2"]):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "amd")

    def test_setup_skip_writes_nothing(self):
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", return_value="4"):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "")

    def test_setup_eof_exits_without_writing(self):
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", side_effect=EOFError):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "")

    def test_setup_keyboard_interrupt_exits_130_without_writing(self):
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, out = self.run_setup()
        self.assertEqual(code, 130)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "")

    def test_setup_unknown_with_override_skip_keeps_override(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "nvidia"}))
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", return_value="4"):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        self.assertIn("gpu_vendor='nvidia'", out)
        self.assertIn("stays in effect", out)
        self.assertNotIn("autodetection stays in effect", out)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "nvidia")

    def test_setup_unknown_with_override_can_change_it(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "nvidia"}))
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")), \
             mock.patch("builtins.input", return_value="2"):
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["gpu_vendor"], "amd")

    def test_setup_detected_with_override_notes_it(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "amd"}))
        profile = fake_profile("nvidia", gpu_name="NVIDIA GPU", gpu_driver="595.71.05")
        with mock.patch("steamtrain.sysinfo.detect", return_value=profile), \
             mock.patch("builtins.input") as inp:
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        inp.assert_not_called()
        self.assertIn("wins over autodetection", out)

    def test_setup_detected_with_unrecognized_override_notes_it(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "banana"}))
        profile = fake_profile("nvidia", gpu_name="NVIDIA GPU", gpu_driver="595.71.05")
        with mock.patch("steamtrain.sysinfo.detect", return_value=profile), \
             mock.patch("builtins.input") as inp:
            code, out = self.run_setup()
        self.assertEqual(code, 0)
        inp.assert_not_called()
        self.assertIn("not recognized", out)

    def test_malformed_config_errors_cleanly(self):
        # syntax error, valid-but-non-object roots
        for bad in ('{"gpu_vendor": "nvidia",}', "null", "[1, 2]", '"hello"'):
            self.config_path.write_text(bad)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code, out = self.run_cli("scan")
            self.assertEqual(code, 1, f"config: {bad}")
            self.assertIn("invalid", err.getvalue())
            self.assertIn(str(self.config_path), err.getvalue())

    def test_non_utf8_config_errors_cleanly(self):
        self.config_path.write_bytes(b'\xff\xfe{"a": 1}')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.run_setup()
        self.assertEqual(code, 1)
        self.assertIn("invalid", err.getvalue())

    def test_override_is_case_insensitive(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "NVIDIA"}))
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")):
            code, out = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("PROTON_ENABLE_NVAPI=1", out)

    def test_override_reaches_proposals(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "nvidia"}))
        with mock.patch("steamtrain.sysinfo.detect", return_value=fake_profile("unknown")):
            code, out = self.run_cli("scan")  # fixture appid 100 is a Proton game
        self.assertEqual(code, 0)
        self.assertIn("PROTON_ENABLE_NVAPI=1", out)

    def test_invalid_config_value_falls_back_to_autodetect(self):
        self.config_path.write_text(json.dumps({"gpu_vendor": "banana"}))
        err = io.StringIO()
        profile = fake_profile("amd", gpu_name="AMD GPU")
        with mock.patch("steamtrain.sysinfo.detect", return_value=profile), \
             contextlib.redirect_stderr(err):
            code, out = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("(amd", out)                    # autodetected vendor used
        self.assertNotIn("PROTON_ENABLE_NVAPI", out)  # not treated as nvidia
        self.assertIn("banana", err.getvalue())       # warned about the ignored value


if __name__ == "__main__":
    unittest.main()
