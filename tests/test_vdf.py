import pathlib
import unittest

from slob import vdf

SAMPLE = '"Root"\n{\n\t"key"\t\t"value"\n\t"Nested"\n\t{\n\t\t"a"\t\t"1"\n\t}\n}\n'


class TestVdf(unittest.TestCase):
    def test_parse_nested(self):
        d = vdf.loads(SAMPLE)
        self.assertEqual(d["Root"]["key"], "value")
        self.assertEqual(d["Root"]["Nested"]["a"], "1")

    def test_roundtrip_identical(self):
        self.assertEqual(vdf.dumps(vdf.loads(SAMPLE)), SAMPLE)

    def test_escapes(self):
        s = vdf.dumps({"R": {"k": 'a "quoted" \\ value'}})
        self.assertEqual(vdf.loads(s)["R"]["k"], 'a "quoted" \\ value')

    def test_skips_line_comments(self):
        d = vdf.loads('// comment\n"R"\n{\n\t"k"\t\t"v"\n}\n')
        self.assertEqual(d["R"]["k"], "v")

    def test_empty_block(self):
        d = vdf.loads('"R"\n{\n\t"apps"\n\t{\n\t}\n}\n')
        self.assertEqual(d["R"]["apps"], {})

    def test_preserves_key_order(self):
        d = vdf.loads('"R"\n{\n\t"b"\t\t"1"\n\t"a"\t\t"2"\n}\n')
        self.assertEqual(list(d["R"].keys()), ["b", "a"])

    def test_real_steam_files_roundtrip(self):
        steam = pathlib.Path.home() / ".local/share/Steam"
        candidates = list(steam.glob("userdata/*/config/localconfig.vdf"))
        candidates += list(steam.glob("steamapps/appmanifest_*.acf"))
        candidates.append(steam / "steamapps" / "libraryfolders.vdf")
        tested = 0
        for f in candidates:
            if not f.is_file():
                continue
            text = f.read_text(encoding="utf-8", errors="surrogateescape")
            self.assertEqual(vdf.dumps(vdf.loads(text)), text, f"round-trip mismatch: {f}")
            tested += 1
        if tested == 0:
            self.skipTest("no real Steam files on this machine")


if __name__ == "__main__":
    unittest.main()
