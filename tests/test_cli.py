import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slob import cli
from slob import vdf

from tests.test_steam import make_manifest, make_steam_root


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
        with mock.patch("slob.advisor.protondb_summary", return_value=None), \
             mock.patch("slob.advisor.run_llm", return_value=payload):
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
        with mock.patch("slob.advisor.protondb_summary", return_value=None), \
             mock.patch("slob.advisor.run_llm", return_value={"override": None}):
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


if __name__ == "__main__":
    unittest.main()
