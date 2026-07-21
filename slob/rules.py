"""Rules engine: SystemProfile + Game -> launch options string.

The baseline is derived from the local machine (GPU vendor, session type,
installed tools, Proton vs native) rather than copied from community sites:
ProtonDB-style recommendations are submitted from *different* hardware, so
they belong in the per-appid `overrides` config, applied by the user's own
judgement. Built-in rules stay conservative — a wrong option can break a
game, and an option that helps elsewhere can hurt here.
"""

import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.config/steam-launch-options-bot/config.json").expanduser()

_DOC = (
    "Edit and save; next run picks it up. enable_* toggle built-in rules. "
    "overrides: map of appid -> launch options used verbatim; the string "
    "'{auto}' inside an override expands to the generated baseline. "
    "exclude: list of appids this tool must never touch. "
    "Find per-game tips on protondb.com, but remember they come from other "
    "people's hardware - put the ones you trust in overrides."
)


def default_config():
    return {
        "_doc": _DOC,
        "enable_gamemode": True,
        "enable_mangohud": False,
        "enable_nvapi": True,
        "enable_shader_cache_skip_cleanup": True,
        "enable_mesa_glthread": True,
        "enable_proton_wayland": False,
        "advisor_command": "claude -p",
        "overrides": {},
        "exclude": [],
    }


def load_config(path=DEFAULT_CONFIG_PATH):
    """Load config, creating a documented default file on first run."""
    path = Path(path)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default_config(), indent=2) + "\n")
    config = default_config()
    config.update(json.loads(path.read_text()))
    return config


def _baseline(game, profile, config):
    env = []
    wrappers = []
    proton = game.runtime == "proton"

    if proton and profile.gpu_vendor == "nvidia" and config["enable_nvapi"]:
        env.append("PROTON_ENABLE_NVAPI=1")
    if proton and profile.session == "wayland" and config["enable_proton_wayland"]:
        env.append("PROTON_ENABLE_WAYLAND=1")
    if profile.gpu_vendor == "nvidia" and config["enable_shader_cache_skip_cleanup"]:
        env.append("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1")
    if (
        not proton
        and profile.gpu_vendor in ("amd", "intel")
        and config["enable_mesa_glthread"]
    ):
        env.append("mesa_glthread=true")

    if profile.has_gamemode and config["enable_gamemode"]:
        wrappers.append("gamemoderun")
    if profile.has_mangohud and config["enable_mangohud"]:
        wrappers.append("mangohud")

    return " ".join(env + wrappers + ["%command%"])


def build_options(game, profile, config):
    """Launch options for one game, or None if the game is excluded."""
    if game.appid in {str(a) for a in config["exclude"]}:
        return None
    override = config["overrides"].get(game.appid)
    baseline = _baseline(game, profile, config)
    if override is not None:
        return override.replace("{auto}", baseline)
    return baseline


def baseline(game, profile, config):
    """The generated hardware baseline for a game (what '{auto}' expands to)."""
    return _baseline(game, profile, config)


def save_override(path, appid, value):
    """Merge overrides[appid]=value into the config file, preserving everything else."""
    path = Path(path)
    load_config(path)  # create the documented default file if it does not exist yet
    data = json.loads(path.read_text())
    data.setdefault("overrides", {})[str(appid)] = value
    path.write_text(json.dumps(data, indent=2) + "\n")
