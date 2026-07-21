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
