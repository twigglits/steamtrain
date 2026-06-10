# Steam Launch Options Bot — Design

Date: 2026-06-11
Status: Approved (autonomous /goal session — decisions documented in lieu of interactive review)

## Purpose

A program that runs as a systemd service on Ubuntu LTS, scans all locally
installed Steam games (only games whose install folder actually exists on
disk), and sets appropriate Steam launch options for each game based on this
machine's OS, desktop environment, and hardware.

## Target system (detected, not assumed)

The program detects everything at runtime; this is what it will find here:

| Facet | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS (noble) |
| Desktop | GNOME on Wayland |
| GPU | NVIDIA GeForce RTX 5090, proprietary driver 595.71.05 |
| CPU / RAM | i9-9900K (16 threads) / 32 GB |
| Tools | gamemoderun present, mangohud absent |
| Steam | Native install at `~/.local/share/Steam`, 8 library folders (several on removable/unmounted media), 2 user accounts in `userdata/` |
| Proton | Global default compat tool is Proton Experimental |

## Why a rules engine instead of copying ProtonDB strings

ProtonDB is the most prominent community source for launch options, but its
recommendations are submitted by users with *different* hardware — an option
that helps on an AMD APU can hurt on an NVIDIA desktop. So:

1. The **baseline** for every game is generated from the *local* system
   profile (GPU vendor/driver, session type, installed tools, Proton vs
   native), not copied from the internet.
2. A **per-appid overrides layer** in the user config lets game-specific
   quirks (the kind documented on ProtonDB) be layered on top or replace the
   baseline entirely, after the user judges they apply to their hardware.
3. The service never phones home; it is fully offline.

## Architecture

Language: Python 3.12, **stdlib only** (no pip dependencies — the `vdf`
package is not installed and a system service should not need a venv).
Package name: `slob` (Steam Launch Options Bot). Delivery: a systemd **user**
service (oneshot) + timer. A user unit (not a root system unit) is correct
because all data is user-owned Steam state; the README documents a
system-level alternative for completeness.

### Components

1. **`slob/vdf.py`** — text KeyValues (VDF) parser/serializer.
   Parses Steam's text VDF into an ordered dict-of-dicts; serializes back in
   Steam's own style (tabs, CRLF-free, quoted, `\\` / `\"` escapes).
   Round-trip of an unmodified file is byte-identical.

2. **`slob/steam.py`** — Steam discovery. Pure functions over a Steam root:
   - Find Steam root (`~/.local/share/Steam`, `~/.steam/steam`, flatpak, snap).
   - Parse `steamapps/libraryfolders.vdf` → library paths; silently skip
     libraries whose path is not currently mounted.
   - For each mounted library, read `appmanifest_*.acf`; a game counts as
     installed **only if** `steamapps/common/<installdir>` exists on disk.
   - Exclude non-games: Proton*, Steam Linux Runtime*, Steamworks Common
     Redistributables (name patterns + known appids).
   - Proton detection: per-app `CompatToolMapping` in `config/config.vdf`,
     else existing `steamapps/compatdata/<appid>/`, else the global `"0"`
     mapping. Result: `proton` / `native` / `unknown` per game.
   - Enumerate `userdata/<accountid>/config/localconfig.vdf` (all accounts).

3. **`slob/sysinfo.py`** — system profile detection → `SystemProfile`
   dataclass: distro, kernel, desktop (GNOME/KDE/…), session type
   (wayland/x11, via env vars with `$XDG_RUNTIME_DIR/wayland-*` socket
   fallback so it works from a systemd timer), GPU vendor + driver
   (lsmod/`/sys/module/nvidia/version`/lspci fallbacks), CPU threads, RAM,
   and availability of `gamemoderun` / `mangohud` / `gamescope`.

4. **`slob/rules.py`** — rules engine. Input: `SystemProfile` + game info
   (appid, name, proton/native). Output: launch-options string of the form
   `ENV=val … wrapper … %command%`. Built-in rules are deliberately
   conservative (a wrong option can break a game):
   - `gamemoderun %command%` when gamemode is installed (CPU governor boost).
   - NVIDIA + Proton: `PROTON_ENABLE_NVAPI=1` (enables DLSS/Reflex paths),
     `DXVK_NVAPI` left to Proton defaults otherwise.
   - NVIDIA shader cache hygiene: `__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1`.
   - AMD/Intel (Mesa) native GL: `mesa_glthread=true` (not applicable here,
     but the engine is hardware-generic).
   - Wayland: **no** forced `SDL_VIDEODRIVER=wayland` (breaks many titles);
     XWayland default is the safe path. Opt-in flag in config for
     `PROTON_ENABLE_WAYLAND=1`.
   - mangohud wrapper only if installed and enabled in config.
   - Config file `~/.config/steam-launch-options-bot/config.json`:
     feature toggles + `overrides` map `appid → string` (supports
     `{auto}` placeholder to extend rather than replace the baseline);
     `exclude` list of appids never to touch. A commented default config is
     created on first run.

5. **`slob/apply.py`** — the only component that writes. Safety contract:
   - **Refuses to write while Steam is running** (Steam rewrites
     `localconfig.vdf` on exit, silently discarding our edit). Detection via
     `~/.steam/steam.pid` + `/proc` check, pgrep fallback. The timer simply
     retries later; this is logged clearly.
   - **Never clobbers human-set options**: writes only when the current
     `LaunchOptions` is empty *or* byte-equal to what we wrote previously
     (tracked per user/appid in `~/.local/state/steam-launch-options-bot/state.json`).
   - Timestamped backup of each `localconfig.vdf` before modification
     (keep last 10) in the state dir.
   - Atomic replace (tmp file + `os.replace`) preserving permissions.
   - Creates the `apps/<appid>` block when missing; merges `LaunchOptions`
     into existing blocks without disturbing sibling keys (`cloud`, …).

6. **`slob/cli.py`** — `slob scan` (table: game, source library, runtime,
   current vs proposed options), `slob apply [--dry-run]`, `slob status`
   (state + last run), `slob revert` (restore our-managed options to empty).
   Logging to stdout (journald-friendly), `--verbose` flag.

7. **systemd units + installer** — `systemd/steam-launch-options-bot.service`
   (Type=oneshot, runs `slob apply`), `…​.timer` (OnBootSec=2min,
   OnUnitActiveSec=30min, Persistent=true), `install.sh` copies the package
   to `~/.local/lib/steam-launch-options-bot`, a launcher to
   `~/.local/bin/slob`, units to `~/.config/systemd/user/`, then
   `systemctl --user enable --now` the timer. `uninstall.sh` reverses it.

### Data flow

```
timer → service → cli.apply
  sysinfo.detect() ──┐
  steam.discover() ──┼→ rules.build(game, profile, config) → proposed options
  config.load()    ──┘
  apply.write(user, appid, options)   [guards: steam-not-running,
                                       empty-or-ours, backup, atomic]
  state.save()
```

### Error handling

- Unmounted library / unreadable manifest → skip with a log line, never fail
  the whole run.
- Malformed VDF → abort writing *that file*, keep backup, exit non-zero so
  systemd marks the run failed (visible in `systemctl --user status`).
- Steam running → informational log, exit 0 (expected condition, retry later).

### Testing

`unittest` (stdlib), `tests/` mirroring modules. VDF round-trip and edge
cases (escapes, empty blocks, duplicate keys); discovery against fixture
library trees (mounted/unmounted, missing installdir, tool filtering);
rules table-driven per profile; apply-safety tests in tmp dirs (steam
running guard mocked, never-clobber, backup rotation, atomicity). A final
manual verification: `slob scan` and `slob apply --dry-run` against the real
Steam install on this machine.

### Out of scope (YAGNI)

- Scraping/fetching ProtonDB (no structured launch-options API; rationale
  above) — README documents how to feed ProtonDB findings into `overrides`.
- Per-game performance auto-tuning, shader precaching, gamescope session
  management.
- Editing Steam cloud-synced `sharedconfig.vdf` (launch options live only in
  `localconfig.vdf`).
