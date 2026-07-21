"""Steam installation discovery: libraries, installed games, users.

A game counts as installed only if its appmanifest exists in a currently
mounted library AND its steamapps/common/<installdir> folder exists on disk.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import vdf

TOOL_NAME_RE = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common)", re.IGNORECASE)

STEAM_ROOT_CANDIDATES = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
)


@dataclass
class Game:
    appid: str
    name: str
    installdir: Path  # absolute path that exists on disk
    library: Path  # the Steam library root containing it
    runtime: str  # 'proton' | 'native' | 'unknown'


def find_steam_root():
    """Return the first Steam root that contains a steamapps directory."""
    for candidate in STEAM_ROOT_CANDIDATES:
        path = Path(candidate).expanduser()
        if (path / "steamapps").is_dir():
            return path.resolve()
    return None


def _load_vdf(path):
    return vdf.loads(path.read_text(encoding="utf-8", errors="surrogateescape"))


def library_paths(root):
    """All library roots from libraryfolders.vdf that are currently mounted."""
    lf = root / "steamapps" / "libraryfolders.vdf"
    paths = [root]
    if lf.is_file():
        data = _load_vdf(lf)
        folders = data.get("libraryfolders", {})
        for entry in folders.values():
            if not isinstance(entry, dict):
                continue
            p = Path(entry.get("path", ""))
            if p != root and (p / "steamapps").is_dir():
                paths.append(p)
    return paths


def compat_mapping(root):
    """Per-appid compat tool names from config.vdf CompatToolMapping."""
    cfg = root / "config" / "config.vdf"
    if not cfg.is_file():
        return {}
    data = _load_vdf(cfg)
    node = data
    for key in ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"):
        node = node.get(key, {}) if isinstance(node, dict) else {}
    return {
        appid: entry.get("name", "")
        for appid, entry in node.items()
        if isinstance(entry, dict)
    }


def _resolve_runtime(appid, library, mapping):
    # The global "0" mapping only affects titles that *need* compat, which we
    # cannot know offline, so only per-app signals are trusted.
    if mapping.get(appid):
        return "proton"
    if (library / "steamapps" / "compatdata" / appid).is_dir():
        return "proton"
    return "native"


def installed_games(root):
    """Games whose manifest and install folder both exist, tools excluded."""
    mapping = {k: v for k, v in compat_mapping(root).items() if k != "0"}
    games = []
    for library in library_paths(root):
        steamapps = library / "steamapps"
        for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
            try:
                state = _load_vdf(manifest).get("AppState", {})
            except (OSError, vdf.VdfError):
                continue
            appid = state.get("appid", "")
            name = state.get("name", "")
            installdir = state.get("installdir", "")
            if not appid or not installdir or TOOL_NAME_RE.match(name):
                continue
            path = steamapps / "common" / installdir
            if not path.is_dir():
                continue
            games.append(
                Game(
                    appid=appid,
                    name=name,
                    installdir=path,
                    library=library,
                    runtime=_resolve_runtime(appid, library, mapping),
                )
            )
    return games


def user_localconfigs(root):
    """(accountid, localconfig.vdf path) for every Steam user on this machine."""
    out = []
    userdata = root / "userdata"
    if userdata.is_dir():
        for d in sorted(userdata.iterdir()):
            cfg = d / "config" / "localconfig.vdf"
            if d.name.isdigit() and cfg.is_file():
                out.append((d.name, cfg))
    return out


def is_steam_running(root):
    """True if the Steam client owning this root is currently running.

    Steam writes ~/.steam/steam.pid and symlinks ~/.steam/steam to its root;
    the global pid file is only trusted when that symlink resolves to `root`,
    so checks against fixture roots stay deterministic.
    """
    candidates = [root / "steam.pid"]
    global_link = Path("~/.steam/steam").expanduser()
    try:
        if global_link.resolve() == Path(root).resolve():
            candidates.append(Path("~/.steam/steam.pid").expanduser())
    except OSError:
        pass
    for pid_file in candidates:
        try:
            pid = int(pid_file.read_text().strip())
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except (OSError, ValueError):
            continue
        if comm == "steam":
            return True
    return False
