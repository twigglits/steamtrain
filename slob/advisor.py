"""On-demand LLM advisor: propose a per-game launch-options override.

The deterministic engine (rules/apply) is untouched; this only *proposes*
values for the existing `overrides` config, gated behind human approval.

Steam substitutes %command% into the launch-options string and runs the
result through a shell, so any unquoted shell operator, or a $/backtick
expansion, in a proposed override is a command-injection vector. A legitimate
override is only environment assignments, known wrapper programs, flags, and
exactly one %command%; validate_override enforces that shape and is the safety
gate. Nothing is written without the user re-running with --write.
"""

from __future__ import annotations

import json
import re
import shlex
import urllib.request

# Wrappers Steam may exec as the leading command. A wrapper's CLI must not treat
# a following bare word as a subcommand to run (that word is not re-validated) —
# vet that property before adding one here. `--` is handled by rejecting any
# bare word before %command%, so gamescope's `-- <cmd>` cannot smuggle a program.
KNOWN_WRAPPERS = frozenset({
    "gamemoderun", "mangohud", "mangoapp", "gamescope", "prime-run",
    "primusrun", "optirun", "strangle", "obs-gamecapture", "umu-run",
})

# A POSIX assignment word: ASCII identifier before the '='. bash treats a
# non-ASCII "KEY=val" token as a command name, not an assignment, so isidentifier
# (which accepts Unicode) is too lax for a security check.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Expansion/substitution/escape that must never appear, even inside quotes.
_EXPANSION = ("`", "$", "\\")
# Shell operators that are only safe when quoted.
_OPERATORS = set(";|&<>(){}\n\r\x00")


class AdvisorError(RuntimeError):
    """LLM invocation or output could not be used."""


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


def _is_env_assign(tok):
    key, sep, _ = tok.partition("=")
    return bool(sep) and bool(_ENV_KEY_RE.fullmatch(key))


def _strip_quoted(s):
    """Return s with every '...'/"..." span removed, or None if a quote is unbalanced.

    Used to check that shell metacharacters appear only inside quotes (where the
    shell treats them literally), e.g. WINEDLLOVERRIDES="d3d11=n;dxgi=n".
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in ("'", '"'):
            j = s.find(c, i + 1)
            if j == -1:
                return None  # unbalanced quote
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def validate_override(s):
    """(ok, reason) — reject launch strings that could execute unexpected code.

    Steam runs the substituted launch string through a shell, so the gate is two
    layers. Layer 1: no $/backtick/backslash anywhere, and no *unquoted* shell
    operator (a metacharacter inside quotes is literal and allowed). Layer 2: the
    literal text %command% must appear exactly once (Steam substitutes every
    occurrence, so a second one hidden in an env value or flag is an extra,
    ungated substitution point) and as a standalone token; every token before it
    must be an env-assignment, an option flag, or a known-safe wrapper — so the
    shell (and any wrapper it chains) execs nothing unexpected. Input must already
    be {auto}-expanded.

    ponytail: a separate-token flag value (e.g. `-W 1920`) and an unknown wrapper
    are rejected, since a bare word before %command% could otherwise be a program
    a wrapper execs (e.g. `gamescope -- evilprog`). Use `--flag=value` form, or
    add the rarity to overrides by hand. Likewise values needing `$`/`\\` are
    rejected. Upgrade path: a real shell grammar if that ever matters.
    """
    if not isinstance(s, str) or not s.strip():
        return False, "empty override"
    for bad in _EXPANSION:
        if bad in s:
            return False, f"forbidden shell expansion character {bad!r}"
    unquoted = _strip_quoted(s)
    if unquoted is None:
        return False, "unbalanced quote"
    meta = _OPERATORS & set(unquoted)
    if meta:
        return False, f"unquoted shell metacharacter(s): {''.join(sorted(meta))}"
    if s.count("%command%") != 1:  # matches Steam's literal-substring substitution
        return False, "must contain exactly one %command%"
    try:
        tokens = shlex.split(s)  # safe now: balanced quotes, no unquoted operators
    except ValueError as exc:
        return False, f"unparseable launch string: {exc}"
    if tokens.count("%command%") != 1:
        return False, "%command% must be a standalone token"
    for tok in tokens:
        if tok == "%command%":
            break  # no unquoted operators remain, so later tokens are game args
        if _is_env_assign(tok):
            continue
        if tok.startswith(("-", "+")):
            continue  # an option flag to a wrapper (inert; cannot name a program)
        if tok in KNOWN_WRAPPERS:
            continue
        return False, f"unrecognized executable token {tok!r} before %command%"
    return True, ""
