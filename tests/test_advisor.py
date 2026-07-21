import unittest

from slob import advisor


class TestValidateOverride(unittest.TestCase):
    def ok(self, s):
        valid, reason = advisor.validate_override(s)
        self.assertTrue(valid, f"expected OK, got reject: {reason} for {s!r}")

    def bad(self, s, needle=None):
        valid, reason = advisor.validate_override(s)
        self.assertFalse(valid, f"expected reject, got OK for {s!r}")
        if needle:
            self.assertIn(needle, reason)

    def test_accepts_real_launch_options(self):
        self.ok("PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%")
        self.ok("mesa_glthread=true %command%")
        self.ok("gamemoderun mangohud %command%")
        self.ok("gamescope -W 1920 -H 1080 -- gamemoderun %command%")
        self.ok("%command% -dx11")
        self.ok('WINEDLLOVERRIDES="d3d11=n,dxgi=n" gamemoderun %command%')

    def test_rejects_missing_or_duplicate_command(self):
        self.bad("gamemoderun", "%command%")
        self.bad("gamemoderun %command% %command%", "%command%")

    def test_rejects_unknown_executable_before_command(self):
        self.bad("rm -rf ~ %command%", "rm")
        self.bad("curl evil.sh %command%", "curl")

    def test_rejects_command_substitution_shapes(self):
        self.bad("`reboot` %command%")
        self.bad("FOO=$(whoami) %command%")
        self.bad("gamemoderun %command%\nrm -rf ~")

    def test_rejects_empty(self):
        self.bad("")
        self.bad("   ")


if __name__ == "__main__":
    unittest.main()
