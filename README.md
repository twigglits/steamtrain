# steam-launch-options-bot

A systemd service for Ubuntu LTS that scans your installed Steam games (only
game folders that actually exist on disk) and sets launch options appropriate
for **your** OS, desktop environment, and hardware.

No dependencies beyond Python 3 (stdlib only). Fully offline.

## Why not just copy options from ProtonDB?

[ProtonDB](https://www.protondb.com) is the best community source for
per-game launch options, but every report comes from *someone else's*
hardware — an option that helps on a Steam Deck or an AMD APU can do nothing
(or harm) on an NVIDIA desktop. This tool inverts that: it detects your GPU
vendor/driver, session type (Wayland/X11), desktop, and installed helpers
(gamemode, MangoHud), and generates a conservative baseline per game. When
you find a game-specific tip on ProtonDB that you trust for your hardware,
put it in the config `overrides` (see below) and it takes precedence.

## What it sets (examples for an NVIDIA + Wayland + gamemode system)

| Game type | Generated launch options |
|---|---|
| Proton game | `PROTON_ENABLE_NVAPI=1 __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%` |
| Native game | `__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 gamemoderun %command%` |

On AMD/Intel (Mesa) systems native GL games get `mesa_glthread=true` instead
of the NVIDIA variables. Rules are deliberately conservative: nothing that is
known to break games (e.g. it never forces `SDL_VIDEODRIVER=wayland`).

## Safety guarantees

- **Never overwrites options a human set.** It only writes when the current
  value is empty or byte-identical to what it wrote previously (tracked in
  `~/.local/state/steam-launch-options-bot/state.json`). Your manual tweaks
  always win.
- **Never writes while Steam is running** (Steam would silently discard the
  change on exit). The timer just retries later.
- **Backs up** `localconfig.vdf` before every write (last 10 kept in the
  state dir) and replaces it atomically, preserving permissions.
- **Only touches games that exist on disk**: a game counts as installed only
  if its `appmanifest_*.acf` is present in a *mounted* library and
  `steamapps/common/<installdir>/` exists.
- `slob revert` restores everything it manages back to empty.

## Install

```sh
./install.sh
```

This copies the package to `~/.local/lib/steam-launch-options-bot`, a `slob`
launcher to `~/.local/bin`, and installs + starts a systemd **user** timer
(2 min after boot, then every 30 min — new installs get options
automatically). Restart Steam to see applied options take effect in the UI.

```sh
./uninstall.sh        # run `slob revert` first if you want options cleared
```

## CLI

```sh
slob scan             # detected system profile + per-game proposals
slob apply --dry-run  # what would change, writing nothing
slob apply            # write (skipped safely if Steam is running)
slob status           # what the tool currently manages
slob revert           # restore managed options to empty
slob advise            # list installed games (no appid to look up)
slob advise witcher    # LLM-propose an override, matched by game name (review only)
slob advise witcher --write   # save the reviewed proposal into overrides
```

## Configuration

`~/.config/steam-launch-options-bot/config.json` (created on first run):

```json
{
  "enable_gamemode": true,
  "enable_mangohud": false,
  "enable_nvapi": true,
  "enable_shader_cache_skip_cleanup": true,
  "enable_mesa_glthread": true,
  "enable_proton_wayland": false,
  "overrides": {
    "292030": "{auto} -dx11"
  },
  "exclude": ["3744430"]
}
```

- `enable_*` — toggle individual built-in rules.
- `overrides` — appid → launch options used verbatim; `{auto}` expands to the
  generated baseline. This is where ProtonDB-sourced, hardware-vetted tips go.
- `exclude` — appids the tool must never touch.

## LLM advisor (hybrid, opt-in)

The scheduled bot stays fully deterministic and offline. `slob advise <game>`
(a game name, or run it bare to list installed games — no appid to look up)
is a separate, on-demand step for the one thing rules can't do well: judging a
*specific game's* community launch tips against *your* hardware.

It fetches the game's ProtonDB summary, asks an LLM (default `claude -p`, set
`advisor_command` in config to change it) to filter that to your GPU/session,
and prints a proposed override with its reasoning. The proposal is **validated**
(launch options are executed code) and **never auto-applied** — re-run with
`--write` to save it into `overrides`, after which the normal `slob apply`/timer
path applies it with all existing safety guarantees. The advisor never runs on
the timer; no API key is stored (Claude Code owns auth).

## Running as a root system service instead

A user unit is the right default (all Steam data is user-owned), but a
system-level variant works too — create
`/etc/systemd/system/steam-launch-options-bot.service` with `User=<you>` and
`Environment=HOME=/home/<you>`, plus a matching timer, and point `ExecStart`
at `/home/<you>/.local/bin/slob apply`.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Design and plan docs live in `docs/superpowers/`.
