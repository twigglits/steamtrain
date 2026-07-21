import tempfile
import unittest
from pathlib import Path

from steamtrain import steam


def make_manifest(library, appid, name, installdir, create_dir=True):
    sa = library / "steamapps"
    (sa / "common").mkdir(parents=True, exist_ok=True)
    sa.joinpath(f"appmanifest_{appid}.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n'
        f'\t"installdir"\t\t"{installdir}"\n}}\n'
    )
    if create_dir:
        (sa / "common" / installdir).mkdir(exist_ok=True)


def make_steam_root(base, extra_libraries=()):
    root = base / "Steam"
    (root / "steamapps").mkdir(parents=True)
    (root / "config").mkdir()
    paths = [str(root)] + [str(p) for p in extra_libraries]
    blocks = []
    for i, p in enumerate(paths):
        blocks.append(f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{p}"\n\t}}\n')
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n' + "".join(blocks) + "}\n"
    )
    (root / "config" / "config.vdf").write_text(
        '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n'
        '\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
        '\t\t\t\t\t"0"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_experimental"\n\t\t\t\t\t}\n'
        '\t\t\t\t\t"100"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_experimental"\n\t\t\t\t\t}\n'
        "\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n"
    )
    return root


class TestSteam(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_library_paths_skips_unmounted(self):
        ext = self.base / "ExtLibrary"
        (ext / "steamapps").mkdir(parents=True)
        root = make_steam_root(self.base, [ext, self.base / "not-mounted"])
        paths = steam.library_paths(root)
        self.assertEqual(paths, [root, ext])

    def test_installed_games_requires_dir_on_disk(self):
        root = make_steam_root(self.base)
        make_manifest(root, "100", "Real Game", "RealGame", create_dir=True)
        make_manifest(root, "200", "Ghost Game", "GhostGame", create_dir=False)
        games = steam.installed_games(root)
        self.assertEqual([g.appid for g in games], ["100"])
        self.assertTrue(games[0].installdir.is_dir())
        self.assertEqual(games[0].library, root)

    def test_tools_are_excluded(self):
        root = make_steam_root(self.base)
        make_manifest(root, "1493710", "Proton Experimental", "Proton - Experimental")
        make_manifest(root, "1628350", "Steam Linux Runtime 3.0 (sniper)", "SLR_sniper")
        make_manifest(root, "228980", "Steamworks Common Redistributables", "Steamworks Shared")
        make_manifest(root, "2180100", "Proton Hotfix", "Proton Hotfix")
        make_manifest(root, "300", "Actual Game", "ActualGame")
        games = steam.installed_games(root)
        self.assertEqual([g.appid for g in games], ["300"])

    def test_games_found_across_libraries(self):
        ext = self.base / "ExtLibrary"
        make_manifest(ext, "400", "External Game", "ExtGame")
        root = make_steam_root(self.base, [ext])
        make_manifest(root, "100", "Main Game", "MainGame")
        games = steam.installed_games(root)
        self.assertEqual(sorted(g.appid for g in games), ["100", "400"])

    def test_runtime_detection(self):
        root = make_steam_root(self.base)
        make_manifest(root, "100", "Mapped Proton Game", "MappedGame")
        make_manifest(root, "200", "Compatdata Game", "CompatGame")
        make_manifest(root, "300", "Native Game", "NativeGame")
        (root / "steamapps" / "compatdata" / "200").mkdir(parents=True)
        games = {g.appid: g for g in steam.installed_games(root)}
        self.assertEqual(games["100"].runtime, "proton")  # per-app CompatToolMapping
        self.assertEqual(games["200"].runtime, "proton")  # compatdata exists
        self.assertEqual(games["300"].runtime, "native")  # neither signal

    def test_user_localconfigs(self):
        root = make_steam_root(self.base)
        for user in ("111", "222"):
            cfg = root / "userdata" / user / "config"
            cfg.mkdir(parents=True)
            (cfg / "localconfig.vdf").write_text('"UserLocalConfigStore"\n{\n}\n')
        (root / "userdata" / "333").mkdir()  # no config -> skipped
        users = steam.user_localconfigs(root)
        self.assertEqual([u for u, _ in users], ["111", "222"])
        for _, path in users:
            self.assertTrue(path.is_file())

    def test_is_steam_running_false_without_pidfile(self):
        root = make_steam_root(self.base)
        self.assertFalse(steam.is_steam_running(root))


if __name__ == "__main__":
    unittest.main()
