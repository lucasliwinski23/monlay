"""
YAML configuration loader for monlay.

Reads ~/.config/monlay/config.yaml and parses it into
Profile / Settings objects.  Also provides profile matching and a
helper to snapshot the current layout into a new profile.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from monlay.models import (
    CommandAction,
    DisplayState,
    DockIconSizeAction,
    DockMonitorAction,
    MonitorIdentity,
    PostConfigAction,
    Profile,
    ProfileLayout,
    ProfileMonitor,
    Settings,
    WallpaperRefreshAction,
)

log = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "monlay"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


class ConfigError(Exception):
    """Raised when the config file has structural or validation errors."""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_post_config(profile_name: str, raw: list[dict[str, Any]]) -> list[PostConfigAction]:
    actions: list[PostConfigAction] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"Profile '{profile_name}': post_config[{i}] must be a mapping, "
                f"got {type(entry).__name__}"
            )
        action_type = entry.get("type", "")
        try:
            if action_type == "dock_monitor":
                if "monitor" not in entry:
                    raise ConfigError(
                        f"Profile '{profile_name}': dock_monitor action is missing 'monitor' field"
                    )
                actions.append(DockMonitorAction(type=action_type, monitor=entry["monitor"]))
            elif action_type == "dock_icon_size":
                if "value" not in entry:
                    raise ConfigError(
                        f"Profile '{profile_name}': dock_icon_size action is missing 'value' field"
                    )
                actions.append(DockIconSizeAction(type=action_type, value=int(entry["value"])))
            elif action_type == "wallpaper_refresh":
                actions.append(WallpaperRefreshAction(type=action_type))
            elif action_type == "command":
                if "command" not in entry:
                    raise ConfigError(
                        f"Profile '{profile_name}': command action is missing 'command' field"
                    )
                actions.append(CommandAction(type=action_type, command=entry["command"]))
            else:
                log.warning("Profile '%s': unknown post_config action type: %s", profile_name, action_type)
        except ConfigError:
            raise
        except (KeyError, ValueError, TypeError) as e:
            raise ConfigError(
                f"Profile '{profile_name}': invalid post_config action [{i}]: {e}"
            ) from e
    return actions


def _parse_profile(raw: dict[str, Any]) -> Profile:
    if not isinstance(raw, dict):
        raise ConfigError(f"Each profile must be a mapping, got {type(raw).__name__}")

    if "name" not in raw:
        raise ConfigError("Profile is missing required 'name' field")
    name = raw["name"]

    description = raw.get("description", "")

    # Monitors: alias -> {vendor, product, serial?}
    monitors_raw = raw.get("monitors")
    if monitors_raw is not None and not isinstance(monitors_raw, dict):
        raise ConfigError(f"Profile '{name}': 'monitors' must be a mapping")

    monitors: dict[str, ProfileMonitor] = {}
    for alias, mon_data in (monitors_raw or {}).items():
        if not isinstance(mon_data, dict):
            raise ConfigError(
                f"Profile '{name}': monitor '{alias}' must be a mapping"
            )
        for required in ("vendor", "product"):
            if required not in mon_data:
                raise ConfigError(
                    f"Profile '{name}': monitor '{alias}' is missing '{required}' field"
                )
        monitors[alias] = ProfileMonitor(
            alias=alias,
            vendor=mon_data["vendor"],
            product=mon_data["product"],
            serial=mon_data.get("serial"),
        )

    if not monitors:
        log.warning("Profile '%s' has no monitors defined", name)

    # Layout
    layout_raw = raw.get("layout")
    if layout_raw is not None and not isinstance(layout_raw, list):
        raise ConfigError(f"Profile '{name}': 'layout' must be a list")

    layout: list[ProfileLayout] = []
    for entry in (layout_raw or []):
        if not isinstance(entry, dict):
            raise ConfigError(f"Profile '{name}': each layout entry must be a mapping")
        if "monitor" not in entry:
            raise ConfigError(f"Profile '{name}': layout entry is missing 'monitor' field")
        alias = entry["monitor"]
        if alias not in monitors:
            log.warning(
                "Profile '%s': layout references unknown monitor alias '%s' "
                "(known: %s)",
                name, alias, ", ".join(monitors.keys()),
            )
        layout.append(ProfileLayout(
            monitor=alias,
            x=entry.get("x", 0),
            y=entry.get("y", 0),
            scale=float(entry.get("scale", 1.0)),
            primary=entry.get("primary", False),
            transform=entry.get("transform", 0),
            mode=entry.get("mode"),
        ))

    # Post-config actions
    post_config = _parse_post_config(name, raw.get("post_config", []))

    return Profile(
        name=name,
        description=description,
        monitors=monitors,
        layout=layout,
        post_config=post_config,
    )


def _parse_settings(raw: dict[str, Any] | None) -> Settings:
    if raw is None:
        return Settings()
    return Settings(
        settle_delay_ms=raw.get("settle_delay_ms", 1500),
        log_level=raw.get("log_level", "INFO"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Config:
    """Loaded configuration with settings and profiles."""

    def __init__(self, settings: Settings, profiles: list[Profile]) -> None:
        self.settings = settings
        self.profiles = profiles

    def match_profile(
        self,
        connected_monitors: set[MonitorIdentity],
    ) -> Profile | None:
        """
        Find a profile whose monitor identity set exactly matches the
        set of currently connected monitors.

        Returns None if no profile matches.  Warns if multiple profiles
        match (first one wins).
        """
        matched: list[Profile] = []
        for profile in self.profiles:
            if profile.identity_set == frozenset(connected_monitors):
                matched.append(profile)

        if not matched:
            log.info(
                "No profile matches connected monitors: %s",
                ", ".join(str(m) for m in connected_monitors),
            )
            return None

        if len(matched) > 1:
            log.warning(
                "Multiple profiles match connected monitors: %s — using %r",
                ", ".join(p.name for p in matched),
                matched[0].name,
            )

        log.info("Matched profile %r", matched[0].name)
        return matched[0]

    def __repr__(self) -> str:
        names = [p.name for p in self.profiles]
        return f"Config(profiles={names})"


def load_config(path: Path | None = None) -> Config:
    """
    Load and parse the YAML config file.

    Args:
        path: Override config path; defaults to
              ~/.config/monlay/config.yaml.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    log.info("Loading config from %s", config_path)

    if not config_path.exists():
        # Check for old config paths and migrate
        import shutil
        old_paths = [
            Path.home() / ".config" / "displaid" / "config.yaml",
            Path.home() / ".config" / "gnome-monitor-hotplug" / "config.yaml",
        ]
        migrated = False
        for old_config in old_paths:
            if old_config.exists():
                log.info("Migrating config from old path %s -> %s", old_config, config_path)
                config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_config, config_path)
                migrated = True
                break
        if not migrated:
            raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}") from e

    if raw is None:
        log.debug("Config file is empty, returning defaults")
        return Config(settings=Settings(), profiles=[])

    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file must be a YAML mapping (got {type(raw).__name__})"
        )

    # Validate top-level keys
    known_keys = {"settings", "profiles"}
    unknown = set(raw.keys()) - known_keys
    if unknown:
        log.warning("Unknown top-level config keys: %s", ", ".join(sorted(unknown)))

    profiles_raw = raw.get("profiles", [])
    if profiles_raw is not None and not isinstance(profiles_raw, list):
        raise ConfigError("'profiles' must be a list")

    settings = _parse_settings(raw.get("settings"))
    profiles = [_parse_profile(p) for p in (profiles_raw or [])]

    log.info("Loaded %d profile(s) from %s", len(profiles), config_path)
    return Config(settings=settings, profiles=profiles)


def save_current_as_profile(
    name: str,
    display_state: DisplayState,
    config_path: Path | None = None,
) -> None:
    """
    Snapshot the current display layout into a new profile and append
    it to the config file.

    Args:
        name: Profile name (e.g. "home-desk").
        display_state: Current DisplayState from dbus_client.
        config_path: Override config path.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build profile dict from current state
    monitors_dict: dict[str, dict[str, str]] = {}
    layout_list: list[dict[str, Any]] = []

    # Create alias mapping: use connector name as alias (sanitised)
    alias_map: dict[str, str] = {}  # connector -> alias
    for mon in display_state.monitors:
        alias = mon.connector.lower().replace("-", "_")
        alias_map[mon.connector] = alias
        entry: dict[str, str] = {
            "vendor": mon.vendor,
            "product": mon.product,
        }
        if mon.serial:
            entry["serial"] = mon.serial
        monitors_dict[alias] = entry

    for lm in display_state.logical_monitors:
        for mon in lm.monitors:
            alias = alias_map[mon.connector]
            layout_entry: dict[str, Any] = {
                "monitor": alias,
                "x": lm.x,
                "y": lm.y,
                "scale": lm.scale,
                "primary": lm.primary,
            }
            if lm.transform != 0:
                layout_entry["transform"] = lm.transform
            cm = mon.current_mode
            if cm:
                # Round refresh rate to integer for the mode pattern
                rate = round(cm.refresh_rate)
                layout_entry["mode"] = f"{cm.width}x{cm.height}@{rate}"
            layout_list.append(layout_entry)

    new_profile: dict[str, Any] = {
        "name": name,
        "monitors": monitors_dict,
        "layout": layout_list,
    }

    # Load existing config or start fresh
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {"settings": {"settle_delay_ms": 1500, "log_level": "INFO"}}

    if "profiles" not in raw:
        raw["profiles"] = []

    # Replace existing profile with same name, or append
    replaced = False
    for i, p in enumerate(raw["profiles"]):
        if p.get("name") == name:
            raw["profiles"][i] = new_profile
            replaced = True
            break
    if not replaced:
        raw["profiles"].append(new_profile)

    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

    log.info("Saved profile %r to %s", name, path)
