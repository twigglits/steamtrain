# Design: `slob advise` — LLM per-game override advisor

Date: 2026-07-21
Status: approved (brainstorming)

## Problem

The current bot is deterministic: it detects the local machine (GPU vendor,
session type, installed helpers, Proton vs native) and generates a
*conservative hardware baseline* per game, applied every 30 min by a systemd
timer. It **deliberately refuses** per-game community knowledge — ProtonDB
reports come from other people's hardware, so an option that helps on a Steam
Deck can do nothing (or harm) on an NVIDIA desktop. That per-game judgement is
currently dumped on the human via the manual `overrides` config.

That `overrides` gap is the one honest place an LLM adds value: reasoning about
a specific game's real-world launch tips *against the user's actual hardware
profile*.

## Non-goals / what stays deterministic

- The 30-min timer loop stays **LLM-free, offline, free, deterministic.** No
  LLM ever runs on a schedule.
- The apply path — plan, backup, atomic write, never-write-while-Steam-runs,
  revert, human-set-value protection — is **unchanged**. The advisor does not
  touch `apply.py`, `steam.py`, `sysinfo.py`, or the systemd units.
- The advisor is a **config generator**, not a second apply path. Its only
  output side effect is writing into the existing `overrides` map in
  `config.json`, which the existing engine already consumes safely.

## Decisions (brainstorming)

1. **Role:** per-game advisor filling the `overrides` gap. LLM as compiler
   (generates cached rules), not interpreter (not in the hot path).
2. **Grounding:** fetch the game's ProtonDB report summary (public JSON API)
   deterministically via stdlib `urllib`; the LLM *filters* that data to the
   user's hardware rather than inventing tips.
3. **Runtime:** invoke `claude -p` (headless Claude Code) by default —
   reuses existing Claude Code auth, no API key in the tool, no HTTP client for
   the LLM, no pip dependency. Configurable to any command.
4. **Safety:** Steam substitutes `%command%` and runs the launch-options
   string **through a shell**, so launch options are arbitrary code execution
   and an LLM-proposed override is *proposed code*. It is validated (against
   shell injection, not just unknown wrappers), shown with reasoning, and
   human-approved. **Never auto-applied, never in the timer.**

## Data flow

```
slob advise <appid> [--write]            # default: propose only, write nothing
   |
   |- sysinfo.detect()           (existing)  -> hardware profile
   |- steam.installed_games()    (existing)  -> game: name, runtime, appid
   |- rules.build_options()      (existing)  -> baseline (the "{auto}" value)
   |- advisor.protondb_summary() (NEW urllib)-> community data | None
   |- advisor.build_prompt(...)  (NEW)       -> grounded prompt
   |- advisor.run_llm(prompt)    (NEW subproc)-> JSON {override, reasoning, confidence}
   |- advisor.validate_override()(NEW security)-> ok | reject+reason
   |- print reasoning + proposed override
         |- human approves (--write, or interactive y/N)
               |- write into config.json overrides[appid]   <- EXISTING mechanism
                     |- next `slob apply` / timer applies it  <- EXISTING safe path
```

## New module: `slob/advisor.py`

- `protondb_summary(appid, fetch=<urlopen>)` -> `dict | None`
  Fetches `https://www.protondb.com/api/v1/reports/summaries/<appid>.json`.
  `fetch` is injectable so tests run offline. 404 / offline / bad JSON ->
  `None` (not an exception). Short timeout.

- `build_prompt(game, profile, baseline, protondb)` -> `str`
  Builds the LLM prompt: hardware profile, the game, the current `{auto}`
  baseline, and the ProtonDB data (or "no community data"). Instructs the LLM
  to express its answer as a **`{auto}`-relative delta** where possible (e.g.
  `{auto} -dx11`) so the hardware baseline stays engine-owned and the LLM
  contributes only game-specific tokens. Requires strict-JSON output:
  `{"override": string|null, "reasoning": string, "confidence": "low|medium|high"}`.
  `override: null` means "the baseline is already right."

- `run_llm(prompt, command)` -> `dict`
  Runs `command` (default `"claude -p"`) as a subprocess, prompt on stdin,
  parses JSON from stdout. Raises a clear error on non-zero exit, missing
  binary, or non-JSON output.

- `validate_override(s)` -> `(ok: bool, reason: str)`  **security gate**
  Runs on the fully `{auto}`-expanded string. Two layers, because Steam runs
  the string through a shell:
  (1) reject `` $ `` / backtick / backslash anywhere, and any *unquoted* shell
  operator (`` ; | & < > ( ) { } `` / newline) — a metacharacter inside quotes
  (e.g. `WINEDLLOVERRIDES="d3d11=n;dxgi=n"`) is literal and allowed;
  (2) the leading command before `%command%` must be a known-safe wrapper
  (env assignments, `-`/`+` flags, and a flag's bare argument are data), and
  there must be exactly one `%command%`.
  Reject -> refuse to write, report the string + reason. Deliberately
  conservative (a rare option needing `$`/`\` or a non-flag wrapper argument is
  rejected and added by hand), which is also why the full string + reasoning is
  shown for human approval.

- `advise(game, profile, config)` -> `Proposal`
  Orchestrates the above. `Proposal` dataclass:
  `{appid, name, baseline, proposed, reasoning, confidence, valid, warning}`.

## CLI: `slob advise`

- New subparser `advise` with positional `appid`, plus the shared
  `--steam-root / --config / --state-dir`, and:
  - `--write` — after showing the proposal, write it into `overrides[appid]`
    (still prints the full string + reasoning first).
  - default (no `--write`) — propose only, write nothing.
- `cmd_advise(args)` in `cli.py`: resolve root, find the game by appid (error
  if not installed), build the Proposal, print reasoning + baseline + proposed,
  then either stop (propose-only) or write to config on `--write`.
- Writing merges into the existing `config.json` `overrides` map, preserving
  the rest of the file.

## Config additions (`rules.py` default_config)

```json
"advisor_command": "claude -p"
```

One knob. Model selection lives inside the command string when wanted
(`"claude -p --model claude-sonnet-5"`) — no separate `advisor_model` field
(the flag syntax is command-specific, so a second field would leak). No API
key handling — Claude Code owns auth. Project stays zero-pip-dependency (only
stdlib `urllib` + `subprocess` added, both stdlib).

## Error handling

- ProtonDB down / 404 / offline -> proceed with `None` grounding, note "no
  community data"; the LLM leans on its own knowledge + the hardware profile.
- `claude` missing / non-zero exit / non-JSON stdout -> clear error, write
  nothing, exit non-zero.
- LLM returns `override: null` -> report "baseline already appropriate", write
  nothing.
- `validate_override` reject -> print string + reason, write nothing,
  exit non-zero.

## Tests: `tests/test_advisor.py` (fully offline)

- `validate_override`: table of good strings (env + wrapper + flags +
  `%command%`, `{auto}` forms) and bad strings (`;`, `|`, `` ` ``, `$(`, `>`,
  newline, missing/duplicate `%command%`). Security-critical -> must be
  thorough.
- `build_prompt`: asserts the prompt contains the profile facts (gpu vendor,
  session) and the ProtonDB facts when present, and the strict-JSON
  instruction.
- `run_llm`: inject a fake command (e.g. a small stub echoing canned JSON, or
  `cat` over a prepared stdin transform) to test JSON parsing; assert clear
  errors on non-zero exit and on non-JSON output.
- `protondb_summary`: parse a sample payload via an injected `fetch`; assert
  `None` on injected 404 / URLError / bad JSON.

## Skipped (YAGNI — add when asked)

- `--all-uncovered` batch mode: a for-loop over the single-game path, still
  human-gated per game. Trivial to add later.
- A separate web-search fetch step: when the runtime is `claude -p`, the
  sub-agent can web-search itself; the ProtonDB JSON is the deterministic
  grounding the tool owns. Add a dedicated fetch only if a non-web LLM command
  becomes the common case.
