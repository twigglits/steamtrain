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

import shlex

KNOWN_WRAPPERS = frozenset({
    "gamemoderun", "mangohud", "mangoapp", "gamescope", "prime-run",
    "primusrun", "optirun", "strangle", "obs-gamecapture", "umu-run",
})

# Expansion/substitution/escape that must never appear, even inside quotes.
_EXPANSION = ("`", "$", "\\")
# Shell operators that are only safe when quoted.
_OPERATORS = set(";|&<>(){}\n\r\x00")


class AdvisorError(RuntimeError):
    """LLM invocation or output could not be used."""


def _is_env_assign(tok):
    key, sep, _ = tok.partition("=")
    return bool(sep) and key.isidentifier()


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
    leading command before %command% must be a known-safe wrapper, so the shell
    execs nothing unexpected. Input must already be {auto}-expanded.

    ponytail: a wrapper taking a non-flag bare argument (e.g. `strangle 60`) is
    rejected, and values genuinely needing `$` or `\\` are rejected — add such
    rarities to overrides by hand. Upgrade path: per-wrapper arity / a real shell
    grammar if that ever matters.
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
    try:
        tokens = shlex.split(s)  # safe now: balanced quotes, no unquoted operators
    except ValueError as exc:
        return False, f"unparseable launch string: {exc}"
    if tokens.count("%command%") != 1:
        return False, "must contain exactly one %command%"
    prev_is_flag = False
    for tok in tokens:
        if tok == "%command%":
            break  # no unquoted operators remain, so later tokens are game args
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
