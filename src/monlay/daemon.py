"""
Main daemon for monlay.

Listens for MonitorsChanged signals on the session bus and applies
the matching profile after a configurable debounce period.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from monlay.config import Config, ConfigError, load_config
from monlay.configurator import (
    ConfiguratorError,
    apply_profile,
    resolve_aliases,
)
from monlay.dbus_client import DBusError, get_current_state
from monlay.logging_config import setup_logging
from monlay.models import MonitorIdentity
from monlay.postconfig import run_post_config

log = logging.getLogger(__name__)

DBUS_NAME = "org.gnome.Mutter.DisplayConfig"
DBUS_PATH = "/org/gnome/Mutter/DisplayConfig"
DBUS_IFACE = "org.gnome.Mutter.DisplayConfig"

# Health check interval: 5 minutes
HEALTH_INTERVAL_MS = 5 * 60 * 1000


def _get_gnome_version() -> str:
    """Best-effort retrieval of GNOME Shell version."""
    try:
        r = subprocess.run(
            ["gnome-shell", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _get_session_type() -> str:
    """Return XDG_SESSION_TYPE or 'unknown'."""
    return os.environ.get("XDG_SESSION_TYPE", "unknown")


class HotplugDaemon:
    """Watches for monitor hotplug events and applies profiles."""

    def __init__(self, config: Config, config_path: str | None = None):
        self._config = config
        self._config_path = config_path
        self._debounce_ms: int = config.settings.settle_delay_ms
        self._timer_id: int | None = None
        self._health_timer_id: int | None = None
        self._loop: GLib.MainLoop | None = None
        self._bus: Gio.DBusConnection | None = None
        self._subscription_id: int | None = None

    def run(self) -> None:
        """Start the main loop and listen for signals."""
        log.info(
            "Starting monlay daemon (debounce=%dms, profiles=%d)",
            self._debounce_ms,
            len(self._config.profiles),
        )

        # Log startup system info
        self._log_system_info()

        self._loop = GLib.MainLoop()

        # Subscribe to MonitorsChanged on the session bus
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            raise DBusError(
                f"Cannot connect to session bus: {e.message}. "
                "Is a desktop session running?"
            ) from e

        self._subscription_id = self._bus.signal_subscribe(
            DBUS_NAME,           # sender
            DBUS_IFACE,          # interface
            "MonitorsChanged",   # signal name
            DBUS_PATH,           # object path
            None,                # arg0 match
            Gio.DBusSignalFlags.NONE,
            self._on_monitors_changed,
        )
        log.info("Subscribed to %s.MonitorsChanged", DBUS_IFACE)

        # Watch for bus disconnection
        self._bus.connect("closed", self._on_bus_closed)

        # Handle SIGTERM/SIGINT gracefully via GLib unix signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._on_shutdown)

        # Periodic health check
        self._health_timer_id = GLib.timeout_add(
            HEALTH_INTERVAL_MS, self._on_health_check
        )

        try:
            self._loop.run()
        finally:
            if self._subscription_id is not None and self._bus is not None:
                try:
                    self._bus.signal_unsubscribe(self._subscription_id)
                except Exception:
                    pass
                self._subscription_id = None
            if self._health_timer_id is not None:
                GLib.source_remove(self._health_timer_id)
                self._health_timer_id = None
            log.info("Daemon stopped.")

    def _log_system_info(self) -> None:
        """Log system info on startup for diagnostics."""
        gnome_ver = _get_gnome_version()
        session_type = _get_session_type()
        profile_names = [p.name for p in self._config.profiles]

        log.info("System: %s, session=%s", gnome_ver, session_type)
        log.info("Configured profiles: %s", ", ".join(profile_names) or "(none)")

        try:
            state = get_current_state()
            monitors_str = ", ".join(
                f"{m.connector}({m.display_name or m.vendor})" for m in state.monitors
            )
            log.info("Connected monitors at startup (%d): %s", len(state.monitors), monitors_str)
        except DBusError as e:
            log.warning("Could not query initial monitor state: %s", e)

    def _on_health_check(self) -> bool:
        """Periodic health log at DEBUG level."""
        log.debug(
            "Daemon alive, monitoring %d profile(s), debounce=%dms",
            len(self._config.profiles),
            self._debounce_ms,
        )
        return GLib.SOURCE_CONTINUE

    def _on_bus_closed(
        self,
        connection: Gio.DBusConnection,
        remote_peer_vanished: bool,
        error: GLib.Error | None,
    ) -> None:
        """Handle DBus connection loss."""
        if remote_peer_vanished:
            log.error("DBus session bus connection lost (peer vanished)")
        elif error:
            log.error("DBus session bus closed: %s", error.message)
        else:
            log.info("DBus session bus closed")

        # Stop the daemon -- systemd will restart us if configured
        if self._loop is not None:
            self._loop.quit()

    def _on_shutdown(self) -> bool:
        """Handle SIGTERM/SIGINT."""
        log.info("Received shutdown signal, exiting...")
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._loop is not None:
            self._loop.quit()
        return GLib.SOURCE_REMOVE

    def _on_monitors_changed(
        self,
        connection: Gio.DBusConnection,
        sender_name: str | None,
        object_path: str,
        interface_name: str,
        signal_name: str,
        parameters: GLib.Variant | None,
    ) -> None:
        """Called when MonitorsChanged fires. Resets the debounce timer."""
        log.debug("MonitorsChanged signal received, (re)starting debounce timer")

        # Cancel any pending timer
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)

        self._timer_id = GLib.timeout_add(
            self._debounce_ms, self._on_debounce_expired
        )

    def _on_debounce_expired(self) -> bool:
        """Called after debounce period. Detect state and apply profile."""
        self._timer_id = None
        log.info("Debounce expired, detecting monitors and applying profile...")

        try:
            self._apply()
        except DBusError as e:
            log.error("DBus error during hotplug handling: %s", e)
        except Exception:
            log.exception("Error during hotplug handling")

        return GLib.SOURCE_REMOVE

    def _apply(self) -> None:
        """Detect current state, match a profile, and apply it."""
        # Reload config each time so edits take effect without restart
        if self._config_path is not None:
            from pathlib import Path
            try:
                self._config = load_config(Path(self._config_path))
                self._debounce_ms = self._config.settings.settle_delay_ms
            except FileNotFoundError:
                log.warning("Config file %s not found, using cached config", self._config_path)
            except ConfigError as e:
                log.error("Config error on reload: %s -- using cached config", e)
            except Exception as e:
                log.error("Failed to reload config: %s -- using cached config", e)

        state = get_current_state()
        connected = {
            MonitorIdentity(vendor=m.vendor, product=m.product)
            for m in state.monitors
        }
        log.info(
            "Connected monitors (%d): %s",
            len(state.monitors),
            ", ".join(
                f"{m.connector}({m.vendor}/{m.product})" for m in state.monitors
            ),
        )

        profile = self._config.match_profile(connected)
        if profile is None:
            log.warning("No matching profile found, skipping apply")
            return

        log.info("Matched profile: %s", profile.name)

        # apply_profile re-fetches state atomically right before calling
        # ApplyMonitorsConfig, so stale serials are not a problem.
        try:
            final_state = apply_profile(profile)
        except ConfiguratorError as e:
            log.error("Failed to apply profile %r: %s", profile.name, e)
            return

        # Run post-config actions (dock move, wallpaper refresh, etc.)
        if profile.post_config:
            try:
                alias_map = resolve_aliases(profile, final_state)
                run_post_config(profile, alias_map)
            except Exception:
                log.exception("Post-config actions failed")


def main(config_path: str | None = None) -> None:
    """Entry point for the daemon."""
    from pathlib import Path
    from monlay.config import DEFAULT_CONFIG_PATH

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = load_config(path)

    # Set up logging: use config level, detect systemd journal automatically
    log_level = config.settings.log_level.upper()
    # If setup_logging was already called by CLI (--verbose), don't override
    # with a less verbose level. Check if root logger already has handlers.
    root = logging.getLogger()
    if not root.handlers:
        setup_logging(level=log_level)
    else:
        # CLI already set up logging; respect its level if more verbose
        cli_level = root.level
        config_level = getattr(logging, log_level, logging.INFO)
        if cli_level > config_level:
            root.setLevel(config_level)

    daemon = HotplugDaemon(config=config, config_path=str(path))
    daemon.run()


if __name__ == "__main__":
    main()
