"""Command-line interface: scan / apply / status / revert."""

import argparse
import sys

from . import __version__, apply as apply_mod, advisor, rules, steam, sysinfo


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="slob",
        description="Set hardware-appropriate Steam launch options for installed games.",
    )
    parser.add_argument("--version", action="version", version=f"slob {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
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
            p.add_argument("game", nargs="?",
                           help="game name (any part, case-insensitive) or appid; "
                                "omit to list installed games")
            p.add_argument("--write", action="store_true",
                           help="save the proposal into config overrides (re-run after reviewing)")
    return parser


def _context(args):
    root = args.steam_root or steam.find_steam_root()
    if root is None:
        print("ERROR: no Steam installation found", file=sys.stderr)
        return None
    from pathlib import Path

    return Path(root)


def _proposals(root, args):
    profile = sysinfo.detect()
    config = rules.load_config(args.config)
    games = steam.installed_games(root)
    options = {}
    names = {}
    for game in games:
        opts = rules.build_options(game, profile, config)
        if opts is not None:
            options[game.appid] = opts
            names[game.appid] = game.name
    return profile, games, options, names


def _print_changes(changes):
    for c in changes:
        marker = {"set": "SET ", "skip-unchanged": "ok  ", "skip-user-set": "KEEP"}[c.action]
        print(f"  [{marker}] user {c.user}  {c.appid:>8}  {c.name}")
        if c.action == "set":
            print(f"           {c.current or '(empty)'!s}  ->  {c.proposed or '(empty)'}")
        elif c.action == "skip-user-set":
            print(f"           keeping human-set value: {c.current}")


def cmd_scan(args):
    root = _context(args)
    if root is None:
        return 1
    profile, games, options, names = _proposals(root, args)
    print(f"System: {profile.distro} | {profile.desktop}/{profile.session} | "
          f"{profile.gpu_name} ({profile.gpu_vendor} {profile.gpu_driver}) | "
          f"gamemode={'yes' if profile.has_gamemode else 'no'} "
          f"mangohud={'yes' if profile.has_mangohud else 'no'}")
    print(f"Steam root: {root}  (running: {'yes' if steam.is_steam_running(root) else 'no'})")
    if not games:
        print("No installed games found on mounted libraries.")
        return 0
    print(f"\n{len(games)} installed game(s) on disk:")
    for g in games:
        print(f"  {g.appid:>8}  {g.runtime:<7}  {g.name}")
        print(f"           library: {g.library}")
        print(f"           proposed: {options.get(g.appid, '(excluded)')}")
    return 0


def cmd_apply(args):
    root = _context(args)
    if root is None:
        return 1
    _, games, options, names = _proposals(root, args)
    state = apply_mod.State.load(args.state_dir)
    changes = apply_mod.plan_changes(root, options, state, names)
    _print_changes(changes)
    planned = [c for c in changes if c.action == "set"]
    if args.dry_run:
        print(f"dry-run: {len(planned)} change(s) would be written, nothing touched")
        return 0
    try:
        written = apply_mod.apply_changes(root, changes, args.state_dir)
    except apply_mod.SteamRunningError as exc:
        print(f"NOTE: {exc}")
        return 0  # expected condition; the timer retries later
    print(f"{len(written)} set, {len(changes) - len(written)} skipped")
    return 0


def cmd_status(args):
    root = _context(args)
    if root is None:
        return 1
    state = apply_mod.State.load(args.state_dir)
    if not state.data:
        print("No launch options are currently managed by this tool.")
        return 0
    print(f"{len(state.data)} managed launch option(s):")
    for key, value in sorted(state.data.items()):
        print(f"  {key}: {value}")
    return 0


def cmd_revert(args):
    root = _context(args)
    if root is None:
        return 1
    state = apply_mod.State.load(args.state_dir)
    changes = apply_mod.plan_revert(root, state)
    if not changes:
        print("Nothing to revert.")
        return 0
    _print_changes(changes)
    try:
        written = apply_mod.apply_changes(root, changes, args.state_dir)
    except apply_mod.SteamRunningError as exc:
        print(f"NOTE: {exc}")
        return 0
    print(f"{len(written)} reverted")
    return 0


def _resolve_game(games, query):
    """(game, error) — pick one installed game by appid or name substring.

    Exact appid wins; otherwise a case-insensitive name-substring match. Returns
    (None, message) when nothing matches or the name is ambiguous, so the caller
    never has to know an appid.
    """
    for g in games:
        if g.appid == query:
            return g, None
    q = query.casefold()
    matches = [g for g in games if q in g.name.casefold()]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"no installed game matches {query!r}. Run `slob advise` to list them."
    listing = "\n".join(f"  {g.appid:>8}  {g.name}" for g in matches)
    return None, f"{query!r} matches {len(matches)} games; be more specific:\n{listing}"


def _list_installed(games):
    print(f"{len(games)} installed game(s) — run `slob advise <name>`:")
    for g in sorted(games, key=lambda g: g.name.casefold()):
        print(f"  {g.appid:>8}  {g.runtime:<7}  {g.name}")


def cmd_advise(args):
    root = _context(args)
    if root is None:
        return 1
    games = steam.installed_games(root)
    if not games:
        print("No installed games found on mounted libraries.", file=sys.stderr)
        return 1
    if not args.game:
        _list_installed(games)
        return 0
    game, err = _resolve_game(games, args.game)
    if game is None:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    profile = sysinfo.detect()
    config = rules.load_config(args.config)
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


COMMANDS = {
    "scan": cmd_scan, "apply": cmd_apply, "status": cmd_status,
    "revert": cmd_revert, "advise": cmd_advise,
}


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
