"""
Post-configuration actions for monlay.

Runs actions defined in a profile's post_config section after the
monitor layout has been applied.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from monlay.models import (
    CommandAction,
    DockIconSizeAction,
    DockMonitorAction,
    MonitorInfo,
    PostConfigAction,
    Profile,
    WallpaperRefreshAction,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# dconf helpers
# ---------------------------------------------------------------------------

def _dconf_read(key: str) -> str | None:
    """Read a dconf key, returning None if unset or on error."""
    try:
        result = subprocess.run(
            ["dconf", "read", key],
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = result.stdout.strip()
        return value if value else None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("dconf read %s failed: %s", key, e)
        return None


def _dconf_write(key: str, value: str) -> bool:
    """Write a dconf key. Returns True on success."""
    try:
        subprocess.run(
            ["dconf", "write", key, value],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("dconf write %s %s failed: %s", key, value, e)
        return False


# ---------------------------------------------------------------------------
# Dock availability check
# ---------------------------------------------------------------------------

def _is_dash_to_dock_available() -> bool:
    """Check if any Dash-to-Dock variant is installed (ubuntu-dock, cosmic-dock, dash-to-dock)."""
    try:
        result = subprocess.run(
            ["gsettings", "list-keys", "org.gnome.shell.extensions.dash-to-dock"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug("Dash-to-Dock availability check failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _handle_dock_monitor(
    action: DockMonitorAction,
    alias_map: dict[str, MonitorInfo],
) -> None:
    """Set Dash-to-Dock preferred monitor by connector."""
    monitor = alias_map.get(action.monitor)
    if monitor is None:
        log.error(
            "dock_monitor: alias %r not found in alias map", action.monitor
        )
        return

    connector = monitor.connector
    dconf_key = "/org/gnome/shell/extensions/dash-to-dock/preferred-monitor-by-connector"
    log.info("Setting dock preferred monitor to %s (%s)", action.monitor, connector)
    _dconf_write(dconf_key, f"'{connector}'")


def _handle_dock_icon_size(action: DockIconSizeAction) -> None:
    """Set Dash-to-Dock max icon size."""
    dconf_key = "/org/gnome/shell/extensions/dash-to-dock/dash-max-icon-size"
    log.info("Setting dock icon size to %d", action.value)
    _dconf_write(dconf_key, str(action.value))


def _handle_wallpaper_refresh() -> None:
    """
    Re-set the current wallpaper URIs to work around the GNOME black
    wallpaper bug that occurs after monitor hotplug.
    """
    keys = [
        "/org/gnome/desktop/background/picture-uri",
        "/org/gnome/desktop/background/picture-uri-dark",
    ]
    for key in keys:
        current = _dconf_read(key)
        if current:
            log.info("Refreshing wallpaper: %s = %s", key, current)
            _dconf_write(key, current)
        else:
            log.debug("Wallpaper key %s not set, skipping", key)


def _handle_command(action: CommandAction) -> None:
    """Run an arbitrary shell command."""
    log.info("Running post-config command: %s", action.command)
    try:
        result = subprocess.run(
            action.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(
                "Command exited %d: %s\nstderr: %s",
                result.returncode, action.command, result.stderr.strip(),
            )
        elif result.stdout.strip():
            log.debug("Command output: %s", result.stdout.strip())
    except subprocess.TimeoutExpired:
        log.error("Command timed out (30s): %s", action.command)
    except Exception as e:
        log.error("Command failed: %s — %s", action.command, e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_post_config(
    profile: Profile,
    alias_map: dict[str, MonitorInfo],
) -> None:
    """
    Execute all post_config actions for a profile.

    Args:
        profile: The applied profile.
        alias_map: Mapping from monitor alias to MonitorInfo
                   (as resolved by configurator.resolve_aliases).
    """
    if not profile.post_config:
        return

    log.info(
        "Running %d post-config action(s) for profile %r",
        len(profile.post_config), profile.name,
    )

    for action in profile.post_config:
        try:
            if isinstance(action, DockMonitorAction):
                if _is_dash_to_dock_available():
                    _handle_dock_monitor(action, alias_map)
                else:
                    log.warning("Skipping dock_monitor action: Dash to Dock is not installed")
            elif isinstance(action, DockIconSizeAction):
                if _is_dash_to_dock_available():
                    _handle_dock_icon_size(action)
                else:
                    log.warning("Skipping dock_icon_size action: Dash to Dock is not installed")
            elif isinstance(action, WallpaperRefreshAction):
                _handle_wallpaper_refresh()
            elif isinstance(action, CommandAction):
                _handle_command(action)
            else:
                log.warning("Unhandled post_config action type: %s", action.type)
        except Exception:
            log.exception(
                "Post-config action %s failed", action.describe(),
            )
