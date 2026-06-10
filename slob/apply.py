"""Safe writer for LaunchOptions in each user's localconfig.vdf.

Safety contract:
- Refuses to write while the owning Steam client runs (Steam rewrites
  localconfig.vdf on exit, silently discarding edits made underneath it).
- Never clobbers options a human set: writes only when the current value is
  empty or byte-equal to what this tool wrote before (tracked in a state
  file), so manual tweaks always win.
- Timestamped backup of every file before modification, newest 10 kept.
- Atomic replace, permissions preserved.
"""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import steam, vdf

DEFAULT_STATE_DIR = Path("~/.local/state/steam-launch-options-bot").expanduser()
BACKUPS_PER_USER = 10
_APPS_PATH = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")


class SteamRunningError(RuntimeError):
    pass


@dataclass
class Change:
    user: str
    appid: str
    name: str
    current: str
    proposed: str
    action: str  # 'set' | 'skip-user-set' | 'skip-unchanged'


class State:
    """Remembers what we wrote, per 'user/appid', so we only ever update
    values that are still our own."""

    def __init__(self, data=None):
        self.data = data or {}

    @classmethod
    def load(cls, state_dir):
        path = Path(state_dir) / "state.json"
        if path.is_file():
            return cls(json.loads(path.read_text()))
        return cls()

    def save(self, state_dir):
        state_dir = Path(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(json.dumps(self.data, indent=2) + "\n")

    def get(self, user, appid):
        return self.data.get(f"{user}/{appid}")

    def record(self, user, appid, value):
        key = f"{user}/{appid}"
        if value:
            self.data[key] = value
        else:
            self.data.pop(key, None)


def _child(node, name):
    """Case-insensitive child lookup (Valve KeyValues is case-insensitive),
    creating the block with canonical casing when absent."""
    for key, value in node.items():
        if key.lower() == name.lower() and isinstance(value, dict):
            return value
    node[name] = {}
    return node[name]


def _load(path):
    return vdf.loads(path.read_text(encoding="utf-8", errors="surrogateescape"))


def _apps_node(data):
    node = data
    for name in _APPS_PATH:
        node = _child(node, name)
    return node


def _current_options(localconfig, appid):
    apps = _apps_node(_load(localconfig))
    block = apps.get(appid)
    if isinstance(block, dict):
        return str(block.get("LaunchOptions", ""))
    return ""


def _decide(current, proposed, last_written):
    if current == proposed:
        return "skip-unchanged"
    if current == "" or current == last_written:
        return "set"
    return "skip-user-set"


def plan_changes(root, options_by_appid, state, names):
    """Plan per-user changes for every (appid -> proposed options)."""
    changes = []
    for user, localconfig in steam.user_localconfigs(root):
        for appid, proposed in options_by_appid.items():
            current = _current_options(localconfig, appid)
            changes.append(
                Change(
                    user=user,
                    appid=appid,
                    name=names.get(appid, appid),
                    current=current,
                    proposed=proposed,
                    action=_decide(current, proposed, state.get(user, appid)),
                )
            )
    return changes


def plan_revert(root, state):
    """Plan restoring every still-ours managed option back to empty."""
    changes = []
    for user, localconfig in steam.user_localconfigs(root):
        for key, written in state.data.items():
            owner, _, appid = key.partition("/")
            if owner != user:
                continue
            current = _current_options(localconfig, appid)
            changes.append(
                Change(
                    user=user,
                    appid=appid,
                    name=appid,
                    current=current,
                    proposed="",
                    action="set" if current == written else "skip-user-set",
                )
            )
    return changes


def _backup(localconfig, user, state_dir):
    backups = Path(state_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    shutil.copy2(localconfig, backups / f"localconfig-{user}-{time.time_ns()}.vdf")
    old = sorted(backups.glob(f"localconfig-{user}-*.vdf"))
    for stale in old[:-BACKUPS_PER_USER]:
        stale.unlink()


def _write_atomic(localconfig, text):
    tmp = localconfig.with_name(localconfig.name + ".slob-tmp")
    tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
    shutil.copystat(localconfig, tmp)
    os.replace(tmp, localconfig)


def apply_changes(root, changes, state_dir=DEFAULT_STATE_DIR, *, is_running=None, dry_run=False):
    """Execute planned 'set' changes. Returns the changes actually written."""
    is_running = steam.is_steam_running if is_running is None else is_running
    to_set = [c for c in changes if c.action == "set"]
    if not to_set or dry_run:
        return to_set
    if is_running(root):
        raise SteamRunningError(
            "Steam is running; localconfig.vdf would be overwritten on Steam "
            "exit. Close Steam and re-run (the timer retries automatically)."
        )
    state = State.load(state_dir)
    localconfigs = dict(steam.user_localconfigs(root))
    for user in sorted({c.user for c in to_set}):
        localconfig = localconfigs[user]
        _backup(localconfig, user, state_dir)
        data = _load(localconfig)
        apps = _apps_node(data)
        for change in to_set:
            if change.user != user:
                continue
            block = apps.get(change.appid)
            if not isinstance(block, dict):
                block = apps.setdefault(change.appid, {})
            block["LaunchOptions"] = change.proposed
            state.record(user, change.appid, change.proposed)
        _write_atomic(localconfig, vdf.dumps(data))
    state.save(state_dir)
    return to_set
