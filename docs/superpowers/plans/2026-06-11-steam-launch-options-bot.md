# Steam Launch Options Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A systemd user service that scans installed-on-disk Steam games and sets hardware/DE/OS-appropriate launch options, safely.

**Architecture:** Pure-stdlib Python 3.12 package `slob` with five focused modules (vdf, steam, sysinfo, rules, apply) behind an argparse CLI, driven by a systemd oneshot service + timer. Spec: `docs/superpowers/specs/2026-06-11-steam-launch-options-bot-design.md`.

**Tech Stack:** Python 3.12 stdlib only, unittest, systemd user units, bash installer.

**Test command pattern:** `python3 -m unittest tests.test_<module> -v` (all: `python3 -m unittest discover -s tests -v`).

---

## File structure

```
slob/__init__.py     version string
slob/vdf.py          loads(text)->dict, dumps(dict)->str   (text KeyValues)
slob/steam.py        Game dataclass, find_steam_root, library_paths,
                     installed_games, compat_mapping, user_localconfigs,
                     is_steam_running
slob/sysinfo.py      SystemProfile dataclass, detect()
slob/rules.py        load_config(path), build_options(game, profile, config)
slob/apply.py        State, plan_changes(...), apply_changes(...)
slob/cli.py          scan / apply [--dry-run] / status / revert
slob/__main__.py     -> cli.main()
tests/test_*.py      one per module
systemd/steam-launch-options-bot.{service,timer}
install.sh, uninstall.sh, README.md
```

Shared contracts (used across tasks — keep names exact):

```python
# slob/steam.py
@dataclass
class Game:
    appid: str
    name: str
    installdir: Path      # absolute, exists on disk
    library: Path         # the SteamLibrary root containing it
    runtime: str          # 'proton' | 'native' | 'unknown'

# slob/sysinfo.py
@dataclass
class SystemProfile:
    distro: str; kernel: str; desktop: str; session: str   # 'wayland'|'x11'|'unknown'
    gpu_vendor: str       # 'nvidia'|'amd'|'intel'|'unknown'
    gpu_name: str; gpu_driver: str
    cpu_threads: int; ram_gb: int
    has_gamemode: bool; has_mangohud: bool; has_gamescope: bool

# slob/apply.py
@dataclass
class Change:
    user: str; appid: str; name: str
    current: str; proposed: str
    action: str           # 'set' | 'skip-user-set' | 'skip-unchanged'
```

---

### Task 1: VDF parser/serializer (`slob/vdf.py`)

**Files:** Create `slob/__init__.py`, `slob/vdf.py`, `tests/test_vdf.py`, `tests/__init__.py`

- [ ] **Step 1: failing tests** — `tests/test_vdf.py`:

```python
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
        d = vdf.loads('// c\n"R"\n{\n\t"k"\t\t"v"\n}\n')
        self.assertEqual(d["R"]["k"], "v")

    def test_real_localconfig_roundtrip(self):
        import pathlib
        p = pathlib.Path.home() / ".local/share/Steam/userdata"
        for f in p.glob("*/config/localconfig.vdf"):
            text = f.read_text(encoding="utf-8", errors="surrogateescape")
            self.assertEqual(vdf.dumps(vdf.loads(text)), text, f)
```

- [ ] **Step 2:** run `python3 -m unittest tests.test_vdf -v` → FAIL (no module)
- [ ] **Step 3:** implement tokenizer (quoted strings with `\"`/`\\`/`\n`/`\t` escapes, `{`, `}`, `//` comments) + recursive block parser + `dumps` emitting Steam style: key/value rows as `\t*N"key"\t\t"value"\n`, blocks as `\t*N"key"\n\t*N{\n…\t*N}\n`.
- [ ] **Step 4:** run tests → PASS (including byte-identical round-trip of both real localconfig.vdf files)
- [ ] **Step 5:** commit `feat: stdlib text-VDF parser/serializer`

### Task 2: Steam discovery (`slob/steam.py`)

**Files:** Create `slob/steam.py`, `tests/test_steam.py` (fixture builder makes fake Steam trees in tmpdirs)

- [ ] **Step 1: failing tests** covering: library_paths skips unmounted path entries; installed_games requires both appmanifest and existing `common/<installdir>`; tools filtered by `^(Proton|Steam Linux Runtime|Steamworks Common)`; runtime resolution order = per-app CompatToolMapping → compatdata dir → 'native' when neither (global "0" only applies to titles needing compat, undecidable → those two signals only); user_localconfigs lists all `userdata/*/config/localconfig.vdf`; is_steam_running false on fixture (no pid file).

Key fixture-based test shape:

```python
def make_library(root, appid, name, installdir, create_dir=True):
    sa = root / "steamapps"; (sa / "common").mkdir(parents=True, exist_ok=True)
    sa.joinpath(f"appmanifest_{appid}.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n'
        f'\t"installdir"\t\t"{installdir}"\n}}\n')
    if create_dir: (sa / "common" / installdir).mkdir()
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement with pure functions taking explicit `root: Path`; `find_steam_root()` checks `~/.local/share/Steam`, `~/.steam/steam`, flatpak, snap paths and requires `steamapps/` inside. `is_steam_running(root)`: read `<root>/../steam.pid` or `~/.steam/steam.pid`, verify `/proc/<pid>/comm` is steam; fallback `subprocess pgrep -x steam`.
- [ ] **Step 4:** run → PASS
- [ ] **Step 5:** commit `feat: steam library/game/user discovery with on-disk checks`

### Task 3: System profile (`slob/sysinfo.py`)

**Files:** Create `slob/sysinfo.py`, `tests/test_sysinfo.py`

- [ ] **Step 1: failing tests** — `detect()` takes injectable params for testability: `env: dict`, `which: callable`, `read_text: callable(path)->str|None`, `glob: callable` so tests simulate: NVIDIA via `/sys/module/nvidia/version`; AMD via `/sys/bus/pci .../ lsmod text` (use module list text param `modules: str`); wayland via `XDG_SESSION_TYPE` then `$XDG_RUNTIME_DIR/wayland-0` fallback; desktop from `XDG_CURRENT_DESKTOP` ("ubuntu:GNOME" → "GNOME").
- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement; real defaults read `/etc/os-release`, `os.uname()`, `/proc/cpuinfo` count, `/proc/meminfo`, `/proc/modules`, `/sys/module/nvidia/version`, `shutil.which` for gamemoderun/mangohud/gamescope.
- [ ] **Step 4:** run → PASS; also sanity `python3 -c "from slob.sysinfo import detect; print(detect())"` on this machine → expects nvidia/wayland/GNOME/gamemode=True
- [ ] **Step 5:** commit `feat: system profile detection`

### Task 4: Rules engine (`slob/rules.py`)

**Files:** Create `slob/rules.py`, `tests/test_rules.py`

- [ ] **Step 1: failing tests** (table-driven). Expected exact strings:

```python
# profile: nvidia + gamemode + wayland; proton game, default config:
"PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%"
# same profile, native game:
"__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%"
# amd profile, native game, no gamemode:
"mesa_glthread=true %command%"
# unknown runtime → treated as native
# override exact: config overrides={"123":"MANGOHUD=1 %command%"} → returned verbatim
# override extend: "{auto} -dx11" → baseline + " -dx11" merged before %command% →
#   e.g. "PROTON_ENABLE_NVAPI=1 ... gamemoderun %command% -dx11"  ({auto} replaced by full baseline)
# excluded appid → None
# enable_mangohud=True + has_mangohud → "mangohud" wrapper before gamemoderun
```

- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement `build_options` assembling `env_parts + wrapper_parts + ["%command%"]`; wrappers order `[mangohud, gamemoderun]`... gamemoderun outermost: `gamemoderun mangohud %command%`? Use order `gamemoderun mangohud %command%` (gamemode first is conventional). `load_config(path)` returns DEFAULT_CONFIG merged with JSON file; writes commented default (JSON with `_doc` keys) when absent.
- [ ] **Step 4:** run → PASS
- [ ] **Step 5:** commit `feat: hardware-aware launch options rules engine`

### Task 5: Safe writer (`slob/apply.py`)

**Files:** Create `slob/apply.py`, `tests/test_apply.py`

- [ ] **Step 1: failing tests** in tmpdirs with fixture localconfig.vdf:
  - sets LaunchOptions in existing app block w/o disturbing siblings (`cloud`)
  - creates `apps/<appid>` block when missing (and `apps` itself when missing)
  - `plan_changes`: current empty → 'set'; current == proposed → 'skip-unchanged'; current nonempty and != state record → 'skip-user-set'; current == state record != proposed → 'set' (our old value, safe to update)
  - backup file created in backups dir before write; >10 backups pruned oldest-first
  - dry_run writes nothing
  - `apply_changes` raises `SteamRunningError` when is_steam_running (injected callable) returns True
  - atomic: original file replaced, permissions preserved
- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement. `State` JSON at `state_dir/state.json` mapping `"{user}/{appid}" -> last_written_string`; `apply_changes(root, changes, state_dir, is_running=steam.is_steam_running, dry_run=False)`. Write path: parse whole localconfig via vdf.loads, navigate/create `UserLocalConfigStore→Software→Valve→Steam→apps→<appid>`, set `LaunchOptions`, vdf.dumps, tmp+`os.replace` with `shutil.copystat`.
- [ ] **Step 4:** run → PASS
- [ ] **Step 5:** commit `feat: safe localconfig writer (steam-running guard, never-clobber, backups, atomic)`

### Task 6: CLI (`slob/cli.py`, `slob/__main__.py`)

**Files:** Create `slob/cli.py`, `slob/__main__.py`, smoke asserts in `tests/test_cli.py`

- [ ] **Step 1: failing test:** `python3 -m slob scan --steam-root <fixture>` via subprocess (or call `cli.main([...])`) prints one line per game incl. proposed options; `apply --dry-run` exits 0 and writes nothing; `revert` plans empty-string for our-managed appids.
- [ ] **Step 2:** run → FAIL
- [ ] **Step 3:** implement argparse with subcommands scan/apply/status/revert; common flags `--steam-root`, `--config`, `--state-dir`, `--verbose`, `--dry-run` (apply); plain-text aligned output; exit 0 on steam-running (log NOTE), nonzero on real errors.
- [ ] **Step 4:** run full suite `python3 -m unittest discover -s tests -v` → all PASS
- [ ] **Step 5:** commit `feat: slob CLI (scan/apply/status/revert)`

### Task 7: systemd units + installer + README

**Files:** Create `systemd/steam-launch-options-bot.service`, `systemd/steam-launch-options-bot.timer`, `install.sh`, `uninstall.sh`, `README.md`

- [ ] **Step 1:** service unit: `Type=oneshot`, `ExecStart=%h/.local/bin/slob apply`, journald via stdout, `Nice=10`. Timer: `OnBootSec=2min`, `OnUnitActiveSec=30min`, `Persistent=true`, `[Install] WantedBy=timers.target`.
- [ ] **Step 2:** `install.sh`: rsync `slob/` → `~/.local/lib/steam-launch-options-bot/slob`, write launcher `~/.local/bin/slob` (`#!/bin/sh exec python3 -m slob "$@"` with PYTHONPATH), copy units → `~/.config/systemd/user/`, `systemctl --user daemon-reload && systemctl --user enable --now steam-launch-options-bot.timer`. `uninstall.sh` reverses + optional `slob revert` hint.
- [ ] **Step 3:** README: what it does, safety guarantees, config file + ProtonDB-informed `overrides` guidance, install/uninstall, system-unit alternative.
- [ ] **Step 4:** `bash -n install.sh uninstall.sh`; `systemd-analyze --user verify systemd/*.service` (best-effort)
- [ ] **Step 5:** commit `feat: systemd user units, installer, docs`

### Task 8: End-to-end verification on this machine

- [ ] **Step 1:** `python3 -m unittest discover -s tests` → all pass
- [ ] **Step 2:** `python3 -m slob scan` against real Steam → shows Together: Moon Escape, The Witcher 3, God of War Ragnarök (mounted libs only), runtimes resolved, proposed options sensible for NVIDIA+Wayland+gamemode
- [ ] **Step 3:** `python3 -m slob apply --dry-run` → planned actions, no writes (Steam may be running — dry-run must still work and say so)
- [ ] **Step 4:** run `./install.sh`; `systemctl --user status steam-launch-options-bot.timer` active; `journalctl --user -u steam-launch-options-bot.service` shows a clean run (real apply will no-op with NOTE while Steam runs — expected)
- [ ] **Step 5:** commit any fixes; final commit + update README if behavior differed

## Self-review notes

- Spec coverage: vdf(T1), discovery+on-disk-only(T2), sysinfo(T3), rules+overrides+ProtonDB rationale(T4/T7), safety contract(T5), CLI(T6), service+installer(T7), live verification(T8). Revert covered in T6.
- Global "0" CompatToolMapping intentionally NOT used as a proton signal (native titles ignore it); compatdata existence is the practical signal — documented in T2 test list, matches spec's "unknown" allowance.
- Type names consistent: `Game`, `SystemProfile`, `Change`, `State`.
