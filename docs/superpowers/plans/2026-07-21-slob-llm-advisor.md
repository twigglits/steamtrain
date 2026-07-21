# slob LLM Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand `slob advise <appid>` command that proposes a per-game launch-options override with an LLM, grounded on ProtonDB and filtered to the user's hardware, gated behind human approval — leaving the deterministic core untouched.

**Architecture:** New `slob/advisor.py` fetches the game's ProtonDB summary (stdlib `urllib`), builds a grounded prompt, runs an LLM via subprocess (`claude -p` by default), validates the proposed launch string against an executable-token safety gate, and returns a `Proposal`. The CLI prints it and, only with `--write`, saves it into the existing `overrides` config map — which the existing rules/apply engine already consumes safely. No LLM ever runs on the timer.

**Tech Stack:** Python 3 stdlib only (`urllib`, `subprocess`, `shlex`, `json`, `dataclasses`), `unittest` tests.

## Global Constraints

- **Zero pip dependencies.** Only stdlib. (`urllib.request`, `subprocess`, `shlex`, `json`, `dataclasses`.)
- **Tests are fully offline and deterministic.** Never hit the network or spawn a real LLM. Inject `fetch=` and `run=` seams; the CLI test uses `unittest.mock.patch`.
- **Deterministic core is untouched.** Do NOT edit `apply.py`, `steam.py`, `sysinfo.py`, `systemd/*`, or `install.sh`. Allowed edits: `rules.py`, `cli.py`, new `advisor.py`, tests, `README.md`.
- **LLM output is never auto-applied.** `slob advise` without `--write` writes nothing. `--write` only merges into `overrides`; the change is applied later by the existing `slob apply`/timer path.
- **Security gate is mandatory.** Steam launch options are executed (env + wrapper around `%command%`). `validate_override` runs on the fully `{auto}`-expanded string.
- **Conventional commit messages.** Test runner: `python3 -m unittest discover -s tests -v`.

---

### Task 1: rules.py — expose baseline, add `save_override`, add `advisor_command` default

**Files:**
- Modify: `slob/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: existing `rules._baseline(game, profile, config)`, `rules.load_config(path)`, `rules.default_config()`.
- Produces:
  - `rules.baseline(game, profile, config) -> str` — the hardware baseline (what `{auto}` expands to), ignoring any override.
  - `rules.save_override(path, appid, value) -> None` — merge `overrides[str(appid)] = value` into the config file, preserving all other keys; creates the default file first if missing.
  - `default_config()["advisor_command"] == "claude -p"`.

- [ ] **Step 1: Write the failing tests** — append these methods to `class TestRules` in `tests/test_rules.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_rules -v`
Expected: FAIL — `AttributeError: module 'slob.rules' has no attribute 'baseline'` (and `save_override`), plus a KeyError/assert on `advisor_command`.

- [ ] **Step 3: Implement**

In `slob/rules.py`, add `"advisor_command": "claude -p",` to the dict returned by `default_config()` (e.g. right after `"enable_proton_wayland": False,`). Then add these two public functions after `build_options`:

```python
def baseline(game, profile, config):
    """The generated hardware baseline for a game (what '{auto}' expands to)."""
    return _baseline(game, profile, config)


def save_override(path, appid, value):
    """Merge overrides[appid]=value into the config file, preserving everything else."""
    path = Path(path)
    load_config(path)  # create the documented default file if it does not exist yet
    data = json.loads(path.read_text())
    data.setdefault("overrides", {})[str(appid)] = value
    path.write_text(json.dumps(data, indent=2) + "\n")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_rules -v`
Expected: PASS (all TestRules tests, including the three new ones).

- [ ] **Step 5: Commit**

```bash
git add slob/rules.py tests/test_rules.py
git commit -m "feat(rules): expose baseline(), add save_override(), advisor_command default"
```

---

### Task 2: advisor.py — `validate_override` safety gate + `AdvisorError`

**Files:**
- Create: `slob/advisor.py`
- Test: `tests/test_advisor.py` (create)

**Interfaces:**
- Produces:
  - `advisor.AdvisorError` (subclass of `RuntimeError`).
  - `advisor.KNOWN_WRAPPERS` (frozenset of safe wrapper program names).
  - `advisor.validate_override(s: str) -> (ok: bool, reason: str)` — runs on a fully-expanded launch string (no `{auto}` placeholder).

- [ ] **Step 1: Write the failing test** — create `tests/test_advisor.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slob.advisor'`.

- [ ] **Step 3: Implement** — create `slob/advisor.py`:

```python
"""On-demand LLM advisor: propose a per-game launch-options override.

The deterministic engine (rules/apply) is untouched; this only *proposes*
values for the existing `overrides` config, gated behind human approval.
Steam runs launch options as a real command (env vars + wrapper around
%command%), not through a shell, so a proposed override is proposed *code*:
validate_override is the safety gate, and nothing is written without the user
re-running with --write.
"""

from __future__ import annotations

KNOWN_WRAPPERS = frozenset({
    "gamemoderun", "mangohud", "mangoapp", "gamescope", "prime-run",
    "primusrun", "optirun", "strangle", "obs-gamecapture", "umu-run",
})

# Command-substitution / control shapes that have no place in a launch string.
_FORBIDDEN = ("\n", "\r", "\x00", "`", "$(")


class AdvisorError(RuntimeError):
    """LLM invocation or output could not be used."""


def _is_env_assign(tok):
    key, sep, _ = tok.partition("=")
    return bool(sep) and key.isidentifier()


def validate_override(s):
    """(ok, reason) — reject launch strings that could execute unexpected code.

    Steam does not run options through a shell; it word-splits, substitutes
    %command%, and execs a single argv. A bare word before %command% is exec'd
    (directly, or by a preceding wrapper), so those must be known-safe wrappers.
    Env assignments and flags (and a flag's bare argument) are data. Input must
    already be {auto}-expanded.

    ponytail: a wrapper that takes a non-flag bare argument (e.g. `strangle 60`)
    is rejected; add such rarities to overrides by hand. Upgrade path: teach the
    gate per-wrapper arity if that ever matters.
    """
    if not isinstance(s, str) or not s.strip():
        return False, "empty override"
    for bad in _FORBIDDEN:
        if bad in s:
            return False, f"forbidden sequence {bad!r}"
    tokens = s.split()
    if tokens.count("%command%") != 1:
        return False, "must contain exactly one %command%"
    prev_is_flag = False
    for tok in tokens:
        if tok == "%command%":
            break  # everything after %command% is game arguments (data)
        if _is_env_assign(tok):
            prev_is_flag = False
        elif tok.startswith(("-", "+")):
            prev_is_flag = True
        elif tok in KNOWN_WRAPPERS:
            prev_is_flag = False
        elif prev_is_flag:
            prev_is_flag = False  # bare argument to the preceding flag, e.g. -W 1920
        else:
            return False, f"unrecognized executable token {tok!r} before %command%"
    return True, ""
```

Note: only the `from __future__ import annotations` directive is added now (it is never an "unused import"); later tasks add the stdlib imports they actually use. `validate_override`/`AdvisorError` need no module imports.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: PASS (all `TestValidateOverride` tests).

- [ ] **Step 5: Commit**

```bash
git add slob/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): validate_override executable-token safety gate"
```

---

### Task 3: advisor.py — `protondb_summary` grounding fetch

**Files:**
- Modify: `slob/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Produces:
  - `advisor._default_fetch(url: str) -> str` — GET the URL, return decoded body (default real fetcher; 10s timeout).
  - `advisor.protondb_summary(appid, *, fetch=_default_fetch) -> dict | None` — parsed ProtonDB summary, or `None` on any failure (offline / 404 / bad JSON).

- [ ] **Step 1: Write the failing test** — append to `tests/test_advisor.py`:

```python
class TestProtonDbSummary(unittest.TestCase):
    def test_parses_summary(self):
        payload = '{"tier": "gold", "confidence": "high", "total": 1234, "trendingTier": "gold"}'
        data = advisor.protondb_summary("292030", fetch=lambda url: payload)
        self.assertEqual(data["tier"], "gold")

    def test_uses_appid_in_url(self):
        seen = {}

        def fake_fetch(url):
            seen["url"] = url
            return "{}"

        advisor.protondb_summary("292030", fetch=fake_fetch)
        self.assertIn("292030", seen["url"])

    def test_none_on_fetch_error(self):
        def boom(url):
            raise OSError("offline")

        self.assertIsNone(advisor.protondb_summary("1", fetch=boom))

    def test_none_on_bad_json(self):
        self.assertIsNone(advisor.protondb_summary("1", fetch=lambda url: "not json"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_advisor.TestProtonDbSummary -v`
Expected: FAIL — `AttributeError: module 'slob.advisor' has no attribute 'protondb_summary'`.

- [ ] **Step 3: Implement**

First add these imports at the top of `slob/advisor.py`, right below the `from __future__ import annotations` line:

```python
import json
import urllib.request
```

Then add this code (after `AdvisorError`):

```python
_PROTONDB_URL = "https://www.protondb.com/api/v1/reports/summaries/{appid}.json"


def _default_fetch(url):
    with urllib.request.urlopen(url, timeout=10) as resp:  # fixed host, GET only
        return resp.read().decode("utf-8")


def protondb_summary(appid, *, fetch=_default_fetch):
    """ProtonDB summary dict for appid, or None if unavailable.

    ponytail: a bare `except Exception -> None` is deliberate — the advisor must
    degrade to "no community data" on any network/parse failure, never crash.
    """
    try:
        return json.loads(fetch(_PROTONDB_URL.format(appid=appid)))
    except Exception:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: PASS (TestValidateOverride + TestProtonDbSummary).

- [ ] **Step 5: Commit**

```bash
git add slob/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): ProtonDB summary grounding fetch (offline-tolerant)"
```

---

### Task 4: advisor.py — `build_prompt`

**Files:**
- Modify: `slob/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Consumes: `steam.Game` (fields `appid`, `name`, `runtime`), `sysinfo.SystemProfile` (fields `gpu_name`, `gpu_vendor`, `gpu_driver`, `session`, `desktop`, `distro`, `has_gamemode`, `has_mangohud`, `has_gamescope`), a `baseline` string, and a `protondb` dict-or-None.
- Produces: `advisor.build_prompt(game, profile, baseline, protondb) -> str`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_advisor.py` (reuse the fixtures from `test_rules`):

```python
from tests.test_rules import game, profile  # noqa: E402  (fixtures reused)


class TestBuildPrompt(unittest.TestCase):
    def test_includes_hardware_and_game_and_contract(self):
        p = build = advisor.build_prompt(
            game(appid="292030", runtime="proton"), profile(),
            "gamemoderun %command%", None,
        )
        self.assertIn("nvidia", p)          # profile.gpu_vendor
        self.assertIn("wayland", p)         # profile.session
        self.assertIn("292030", p)          # appid
        self.assertIn("gamemoderun %command%", p)  # baseline
        self.assertIn("{auto}", p)          # delta instruction
        self.assertIn("STRICT JSON", p)     # output contract
        self.assertIn("ProtonDB summary: unavailable", p)

    def test_includes_protondb_when_present(self):
        p = advisor.build_prompt(
            game(), profile(), "gamemoderun %command%",
            {"tier": "gold", "confidence": "high", "total": 5, "trendingTier": "gold"},
        )
        self.assertIn("tier=gold", p)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_advisor.TestBuildPrompt -v`
Expected: FAIL — `AttributeError: module 'slob.advisor' has no attribute 'build_prompt'`.

- [ ] **Step 3: Implement** — add to `slob/advisor.py`:

```python
def build_prompt(game, profile, baseline, protondb):
    facts = [
        f"- GPU: {profile.gpu_name} (vendor={profile.gpu_vendor}, driver={profile.gpu_driver})",
        f"- Session: {profile.session} on {profile.desktop}, {profile.distro}",
        f"- Runtime for this game: {game.runtime}",
        f"- Helpers present: gamemode={profile.has_gamemode}, "
        f"mangohud={profile.has_mangohud}, gamescope={profile.has_gamescope}",
    ]
    if protondb:
        pdb = (
            f"ProtonDB summary: tier={protondb.get('tier')}, "
            f"confidence={protondb.get('confidence')}, "
            f"trendingTier={protondb.get('trendingTier')}, "
            f"reports={protondb.get('total')}."
        )
    else:
        pdb = "ProtonDB summary: unavailable."
    return (
        "You are an expert Linux Steam gaming advisor. Recommend launch options "
        f'for the game "{game.name}" (appid {game.appid}) tuned to THIS machine.\n\n'
        "This machine:\n" + "\n".join(facts) + "\n\n"
        f"{pdb}\n\n"
        f"The tool's generated hardware baseline for this game is:\n  {baseline}\n"
        "In your answer, the literal token {auto} expands to exactly that baseline. "
        'Prefer returning {auto} plus any game-specific tokens (e.g. "{auto} -dx11") '
        "so the hardware baseline stays owned by the tool.\n\n"
        "Rules:\n"
        "- Only suggest options that help THIS hardware/session; never anything "
        "known to break the game. Be conservative.\n"
        "- Allowed: KEY=VALUE env vars, known wrappers (gamemoderun, mangohud, "
        "gamescope), and Steam launch flags. Exactly one %command%. No shell "
        "metacharacters, no command substitution.\n"
        "- If the baseline is already appropriate, return override=null.\n\n"
        "Respond with STRICT JSON only, no prose outside it:\n"
        '{"override": string-or-null, "reasoning": string, '
        '"confidence": "low"|"medium"|"high"}'
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add slob/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): build_prompt with hardware + ProtonDB grounding"
```

---

### Task 5: advisor.py — `run_llm` + `_extract_json`

**Files:**
- Modify: `slob/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Produces:
  - `advisor._extract_json(text: str) -> dict` — parse the first `{...}` object in text; raise `AdvisorError` if none/invalid.
  - `advisor.run_llm(prompt, command, *, run=subprocess.run) -> dict` — run `command` (shlex-split) with `prompt` on stdin; return parsed JSON with `reasoning`/`confidence` defaulted; raise `AdvisorError` on missing binary, non-zero exit, timeout, or non-JSON output. Result dict always has an `"override"` key.

- [ ] **Step 1: Write the failing test** — append to `tests/test_advisor.py`:

```python
from types import SimpleNamespace  # noqa: E402


def _proc(returncode=0, stdout="", stderr=""):
    return lambda argv, **kw: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr)


class TestRunLlm(unittest.TestCase):
    def test_parses_plain_json(self):
        run = _proc(stdout='{"override": "{auto} -dx11", "reasoning": "r", "confidence": "high"}')
        data = advisor.run_llm("prompt", "fake", run=run)
        self.assertEqual(data["override"], "{auto} -dx11")
        self.assertEqual(data["confidence"], "high")

    def test_parses_fenced_json_with_prose(self):
        run = _proc(stdout='Here you go:\n```json\n{"override": null, "reasoning": "ok"}\n```\n')
        data = advisor.run_llm("prompt", "fake", run=run)
        self.assertIsNone(data["override"])
        self.assertEqual(data["confidence"], "low")  # defaulted

    def test_defaults_reasoning_and_confidence(self):
        run = _proc(stdout='{"override": null}')
        data = advisor.run_llm("prompt", "fake", run=run)
        self.assertEqual(data["reasoning"], "")
        self.assertEqual(data["confidence"], "low")

    def test_raises_on_nonzero_exit(self):
        run = _proc(returncode=2, stderr="boom")
        with self.assertRaises(advisor.AdvisorError):
            advisor.run_llm("prompt", "fake", run=run)

    def test_raises_on_non_json(self):
        run = _proc(stdout="I could not help with that.")
        with self.assertRaises(advisor.AdvisorError):
            advisor.run_llm("prompt", "fake", run=run)

    def test_raises_when_binary_missing(self):
        def missing(argv, **kw):
            raise FileNotFoundError(argv[0])

        with self.assertRaises(advisor.AdvisorError):
            advisor.run_llm("prompt", "does-not-exist", run=missing)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_advisor.TestRunLlm -v`
Expected: FAIL — `AttributeError: module 'slob.advisor' has no attribute 'run_llm'`.

- [ ] **Step 3: Implement**

First add these imports at the top of `slob/advisor.py`, in the stdlib import group (below `import json`):

```python
import shlex
import subprocess
```

Then add this code:

```python
def _extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AdvisorError(f"no JSON object in LLM output: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AdvisorError(f"invalid JSON from LLM: {exc}") from exc


def run_llm(prompt, command, *, run=subprocess.run):
    argv = shlex.split(command)
    if not argv:
        raise AdvisorError("advisor_command is empty")
    try:
        result = run(argv, input=prompt, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as exc:
        raise AdvisorError(f"advisor command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdvisorError("advisor command timed out") from exc
    if result.returncode != 0:
        raise AdvisorError(
            f"advisor command exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    data = _extract_json(result.stdout)
    if "override" not in data:
        raise AdvisorError("LLM JSON missing 'override' field")
    data.setdefault("reasoning", "")
    data.setdefault("confidence", "low")
    return data
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add slob/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): run_llm subprocess + robust JSON extraction"
```

---

### Task 6: advisor.py — `advise` orchestrator + `Proposal`

**Files:**
- Modify: `slob/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Consumes: `rules.baseline`, `protondb_summary`, `build_prompt`, `run_llm`, `validate_override` (all above).
- Produces:
  - `advisor.Proposal` dataclass: `appid: str, name: str, baseline: str, proposed: str | None, reasoning: str, confidence: str, valid: bool, warning: str`.
  - `advisor.advise(game, profile, config, *, fetch=_default_fetch, run=subprocess.run) -> Proposal`. Validation runs on the `{auto}`-expanded proposal; `proposed` stored raw (may contain `{auto}`). `override: null` → `proposed=None, valid=True`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_advisor.py`:

```python
from slob import rules  # noqa: E402


class TestAdvise(unittest.TestCase):
    def _run(self, override, confidence="high"):
        payload = json.dumps({"override": override, "reasoning": "because", "confidence": confidence})
        return lambda argv, **kw: SimpleNamespace(returncode=0, stdout=payload, stderr="")

    def test_valid_delta_proposal(self):
        prop = advisor.advise(
            game("100", "proton"), profile(), rules.default_config(),
            fetch=lambda url: "x",  # -> protondb None
            run=self._run("{auto} -dx11"),
        )
        self.assertEqual(prop.proposed, "{auto} -dx11")
        self.assertTrue(prop.valid)
        self.assertEqual(prop.confidence, "high")
        self.assertIn("gamemoderun %command%", prop.baseline)

    def test_null_override_means_baseline_ok(self):
        prop = advisor.advise(
            game("100"), profile(), rules.default_config(),
            fetch=lambda url: "x", run=self._run(None),
        )
        self.assertIsNone(prop.proposed)
        self.assertTrue(prop.valid)

    def test_unsafe_proposal_flagged_invalid(self):
        prop = advisor.advise(
            game("100"), profile(), rules.default_config(),
            fetch=lambda url: "x", run=self._run("rm -rf ~ %command%"),
        )
        self.assertFalse(prop.valid)
        self.assertIn("rm", prop.warning)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_advisor.TestAdvise -v`
Expected: FAIL — `AttributeError: module 'slob.advisor' has no attribute 'advise'`.

- [ ] **Step 3: Implement**

First add these imports at the top of `slob/advisor.py` — `from dataclasses import dataclass` in a group below the stdlib imports, and `from . import rules` as a local import below that:

```python
from dataclasses import dataclass

from . import rules
```

Then add this code:

```python
@dataclass
class Proposal:
    appid: str
    name: str
    baseline: str
    proposed: str | None
    reasoning: str
    confidence: str
    valid: bool
    warning: str


def advise(game, profile, config, *, fetch=_default_fetch, run=subprocess.run):
    base = rules.baseline(game, profile, config)
    pdb = protondb_summary(game.appid, fetch=fetch)
    prompt = build_prompt(game, profile, base, pdb)
    data = run_llm(prompt, config.get("advisor_command", "claude -p"), run=run)
    proposed = data.get("override")
    if proposed is None:
        valid, warning = True, ""
    else:
        proposed = str(proposed)
        valid, warning = validate_override(proposed.replace("{auto}", base))
    return Proposal(
        appid=game.appid,
        name=game.name,
        baseline=base,
        proposed=proposed,
        reasoning=str(data.get("reasoning", "")),
        confidence=str(data.get("confidence", "low")),
        valid=valid,
        warning=warning,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_advisor -v`
Expected: PASS (all advisor test classes).

- [ ] **Step 5: Commit**

```bash
git add slob/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): advise() orchestrator returning a validated Proposal"
```

---

### Task 7: CLI `slob advise` + README, full suite green

**Files:**
- Modify: `slob/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `advisor.advise`, `advisor.AdvisorError`, `rules.save_override`, `steam.installed_games`, `sysinfo.detect`, `rules.load_config`.
- Produces: `cli.cmd_advise(args) -> int`; subparser `advise` with positional `appid` and `--write`; `COMMANDS["advise"]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`. Add `import json` and `from unittest import mock` at the top of the file, then add these methods to `class TestCli`:

```python
    def _advise(self, *extra, override="{auto} -dx11", confidence="high"):
        payload = {"override": override, "reasoning": "stabler on NVIDIA", "confidence": confidence}
        with mock.patch("slob.advisor.protondb_summary", return_value=None), \
             mock.patch("slob.advisor.run_llm", return_value=payload):
            return self.run_cli("advise", "100", *extra)

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cli -v`
Expected: FAIL — `argparse` error / `SystemExit` on the unknown `advise` subcommand (or `KeyError: 'advise'`).

- [ ] **Step 3: Implement**

(a) In `slob/cli.py`, change the import line to include `advisor`:

```python
from . import __version__, apply as apply_mod, advisor, rules, steam, sysinfo
```

(b) In `_build_parser`, add `advise` to the subcommand tuple and its extra args. The loop becomes:

```python
    for name, help_text in (
        ("scan", "show installed games and proposed launch options"),
        ("apply", "write launch options (skips anything a human set)"),
        ("status", "show what this tool manages and last state"),
        ("revert", "restore every option this tool set back to empty"),
        ("advise", "LLM-propose a per-game override for one appid (needs network + claude)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--steam-root", default=None, help="Steam root (auto-detected)")
        p.add_argument("--config", default=rules.DEFAULT_CONFIG_PATH)
        p.add_argument("--state-dir", default=apply_mod.DEFAULT_STATE_DIR)
        if name == "apply":
            p.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
        if name == "advise":
            p.add_argument("appid", help="Steam appid to advise on")
            p.add_argument("--write", action="store_true",
                           help="save the proposal into config overrides (re-run after reviewing)")
    return parser
```

(c) Add the command handler (e.g. after `cmd_revert`):

```python
def cmd_advise(args):
    root = _context(args)
    if root is None:
        return 1
    profile = sysinfo.detect()
    config = rules.load_config(args.config)
    game = next((g for g in steam.installed_games(root) if g.appid == str(args.appid)), None)
    if game is None:
        print(f"ERROR: appid {args.appid} is not installed on any mounted library",
              file=sys.stderr)
        return 1
    try:
        prop = advisor.advise(game, profile, config)
    except advisor.AdvisorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{prop.name}  (appid {prop.appid}, {game.runtime})")
    print(f"  baseline  : {prop.baseline}")
    shown = prop.proposed if prop.proposed is not None else "(LLM: baseline already appropriate)"
    print(f"  proposed  : {shown}")
    print(f"  confidence: {prop.confidence}")
    print(f"  reasoning : {prop.reasoning}")
    if prop.proposed is None:
        return 0
    if not prop.valid:
        print(f"  REJECTED by safety check: {prop.warning}", file=sys.stderr)
        print("  Nothing written. Add it to overrides by hand only if you trust it.")
        return 1
    if not args.write:
        print("\nReview looks right? Re-run with --write to save it to overrides.")
        return 0
    rules.save_override(args.config, prop.appid, prop.proposed)
    print(f"\nSaved overrides[{prop.appid}] = {prop.proposed}")
    print("Nothing changed yet — the next `slob apply` (or timer run) applies it.")
    return 0
```

(d) Register it in `COMMANDS`:

```python
COMMANDS = {
    "scan": cmd_scan, "apply": cmd_apply, "status": cmd_status,
    "revert": cmd_revert, "advise": cmd_advise,
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Update README**

In `README.md`, add `advise` to the CLI block:

```sh
slob advise 292030   # LLM-propose a per-game override for your hardware (review only)
slob advise 292030 --write   # save the reviewed proposal into overrides
```

And add this section after "## Configuration":

```markdown
## LLM advisor (hybrid, opt-in)

The scheduled bot stays fully deterministic and offline. `slob advise <appid>`
is a separate, on-demand step for the one thing rules can't do well: judging a
*specific game's* community launch tips against *your* hardware.

It fetches the game's ProtonDB summary, asks an LLM (default `claude -p`, set
`advisor_command` in config to change it) to filter that to your GPU/session,
and prints a proposed override with its reasoning. The proposal is **validated**
(launch options are executed code) and **never auto-applied** — re-run with
`--write` to save it into `overrides`, after which the normal `slob apply`/timer
path applies it with all existing safety guarantees. The advisor never runs on
the timer; no API key is stored (Claude Code owns auth).
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — every test across `test_rules`, `test_advisor`, `test_cli`, `test_apply`, `test_steam`, `test_sysinfo`, `test_vdf`.

- [ ] **Step 7: Commit**

```bash
git add slob/cli.py tests/test_cli.py README.md
git commit -m "feat(cli): slob advise command + README hybrid-advisor docs"
```

---

## Self-Review

**Spec coverage:**
- Data flow (detect → installed_games → baseline → protondb → prompt → run_llm → validate → print → --write → overrides) → Tasks 1–7. ✓
- `advisor.py` functions `protondb_summary` (T3), `build_prompt` (T4), `run_llm` (T5), `validate_override` (T2), `advise`/`Proposal` (T6). ✓
- Security gate on executed launch string, `{auto}`-expanded → T2 + T6. ✓
- Config `advisor_command` default, no `advisor_model` → T1. ✓
- CLI propose-only default, `--write` to save, unknown-appid error → T7. ✓
- Error handling: ProtonDB→None (T3), claude missing/nonzero/non-JSON→AdvisorError (T5), override null→no write (T6/T7), validation reject→no write (T6/T7). ✓
- Tests fully offline via injected `fetch`/`run` and `mock.patch` → all tasks. ✓
- Untouched core (apply/steam/sysinfo/systemd/install) → no task edits them. ✓
- Skipped `--all-uncovered` and separate web-search fetch → not in plan (YAGNI, per spec). ✓

**Placeholder scan:** none — every step has full code and exact run/expected. ✓

**Type consistency:** `advise(game, profile, config, *, fetch, run)`, `Proposal.proposed: str|None`, `validate_override(str)->(bool,str)`, `run_llm(prompt, command, *, run)->dict`, `protondb_summary(appid, *, fetch)->dict|None`, `rules.baseline`/`rules.save_override` — names match across Tasks 1–7. ✓
