import contextlib
import io
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
