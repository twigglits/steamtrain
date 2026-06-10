import os
import tempfile
import unittest
from pathlib import Path

from slob import apply as apply_mod
from slob import vdf

LOCALCONFIG = (
    '"UserLocalConfigStore"\n{\n'
    '\t"Software"\n\t{\n'
    '\t\t"Valve"\n\t\t{\n'
    '\t\t\t"Steam"\n\t\t\t{\n'
    '\t\t\t\t"apps"\n\t\t\t\t{\n'
    '\t\t\t\t\t"100"\n\t\t\t\t\t{\n'
    '\t\t\t\t\t\t"cloud"\n\t\t\t\t\t\t{\n'
    '\t\t\t\t\t\t\t"last_sync_state"\t\t"synchronized"\n'
    "\t\t\t\t\t\t}\n"
    "\t\t\t\t\t}\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "}\n"
)


def read_options(path, appid):
    data = vdf.loads(path.read_text())
    apps = data["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
    return apps.get(appid, {}).get("LaunchOptions")


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "Steam"
        cfg = self.root / "userdata" / "111" / "config"
        cfg.mkdir(parents=True)
        self.localconfig = cfg / "localconfig.vdf"
        self.localconfig.write_text(LOCALCONFIG)
        self.state_dir = base / "state"

    def plan(self, options_by_appid, names=None):
        state = apply_mod.State.load(self.state_dir)
        return apply_mod.plan_changes(self.root, options_by_appid, state, names or {})

    def test_plan_set_when_empty(self):
        changes = self.plan({"100": "gamemoderun %command%"})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "set")
        self.assertEqual(changes[0].current, "")

    def test_plan_skip_unchanged(self):
        changes = self.plan({"100": "gamemoderun %command%"})
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        changes2 = self.plan({"100": "gamemoderun %command%"})
        self.assertEqual(changes2[0].action, "skip-unchanged")

    def test_never_clobber_user_set_options(self):
        data = vdf.loads(self.localconfig.read_text())
        apps = data["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
        apps["100"]["LaunchOptions"] = "-my-hand-tuned-flags"
        self.localconfig.write_text(vdf.dumps(data))
        changes = self.plan({"100": "gamemoderun %command%"})
        self.assertEqual(changes[0].action, "skip-user-set")
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        self.assertEqual(read_options(self.localconfig, "100"), "-my-hand-tuned-flags")

    def test_updates_our_own_previous_value(self):
        changes = self.plan({"100": "old %command%"})
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        changes2 = self.plan({"100": "new %command%"})
        self.assertEqual(changes2[0].action, "set")
        apply_mod.apply_changes(self.root, changes2, self.state_dir, is_running=lambda r: False)
        self.assertEqual(read_options(self.localconfig, "100"), "new %command%")

    def test_creates_missing_app_block_preserving_siblings(self):
        changes = self.plan({"999": "gamemoderun %command%"})
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        self.assertEqual(read_options(self.localconfig, "999"), "gamemoderun %command%")
        data = vdf.loads(self.localconfig.read_text())
        apps = data["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
        self.assertEqual(apps["100"]["cloud"]["last_sync_state"], "synchronized")

    def test_refuses_to_write_while_steam_running(self):
        changes = self.plan({"100": "gamemoderun %command%"})
        with self.assertRaises(apply_mod.SteamRunningError):
            apply_mod.apply_changes(
                self.root, changes, self.state_dir, is_running=lambda r: True
            )
        self.assertIsNone(read_options(self.localconfig, "100"))

    def test_dry_run_writes_nothing(self):
        before = self.localconfig.read_text()
        changes = self.plan({"100": "gamemoderun %command%"})
        apply_mod.apply_changes(
            self.root, changes, self.state_dir, is_running=lambda r: False, dry_run=True
        )
        self.assertEqual(self.localconfig.read_text(), before)
        self.assertFalse((self.state_dir / "state.json").exists())

    def test_backup_created_and_pruned(self):
        for i in range(12):
            changes = self.plan({"100": f"v{i} %command%"})
            apply_mod.apply_changes(
                self.root, changes, self.state_dir, is_running=lambda r: False
            )
        backups = sorted((self.state_dir / "backups").glob("localconfig-111-*.vdf"))
        self.assertEqual(len(backups), 10)
        # first backup of the original (empty options) was pruned away
        self.assertNotIn("v0", backups[0].read_text())

    def test_permissions_preserved(self):
        os.chmod(self.localconfig, 0o600)
        changes = self.plan({"100": "gamemoderun %command%"})
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        self.assertEqual(self.localconfig.stat().st_mode & 0o777, 0o600)

    def test_revert_plan_restores_empty(self):
        changes = self.plan({"100": "gamemoderun %command%"})
        apply_mod.apply_changes(self.root, changes, self.state_dir, is_running=lambda r: False)
        state = apply_mod.State.load(self.state_dir)
        reverts = apply_mod.plan_revert(self.root, state)
        self.assertEqual([(c.appid, c.proposed) for c in reverts], [("100", "")])
        apply_mod.apply_changes(self.root, reverts, self.state_dir, is_running=lambda r: False)
        self.assertEqual(read_options(self.localconfig, "100"), "")
        # state entry cleared -> nothing further to revert
        state2 = apply_mod.State.load(self.state_dir)
        self.assertEqual(apply_mod.plan_revert(self.root, state2), [])


if __name__ == "__main__":
    unittest.main()
