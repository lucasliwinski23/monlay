"""
Adw.Application subclass for monlay GUI.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from monlay import __version__  # noqa: E402
from monlay.logging_config import setup_logging  # noqa: E402
from monlay.gui.window import MonitorProfilesWindow  # noqa: E402

log = logging.getLogger(__name__)


def _gather_debug_info() -> str:
    """Collect system debug info for bug reports."""
    lines: list[str] = [f"Monlay {__version__}"]

    # GNOME Shell version
    try:
        out = subprocess.check_output(
            ["gnome-shell", "--version"], text=True, timeout=3
        ).strip()
        lines.append(out)
    except Exception:
        lines.append("GNOME Shell: unknown")

    # Session type
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    lines.append(f"Session type: {session}")

    # GPU info
    try:
        lspci = subprocess.check_output(["lspci"], text=True, timeout=3)
        for line in lspci.splitlines():
            if "VGA" in line or "3D" in line or "Display" in line:
                lines.append(f"GPU: {line.split(': ', 1)[-1].strip()}")
    except Exception:
        lines.append("GPU: unknown")

    # Connected monitors via GNOME D-Bus
    try:
        out = subprocess.check_output(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Mutter.DisplayConfig",
                "--object-path", "/org/gnome/Mutter/DisplayConfig",
                "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
            ],
            text=True,
            timeout=5,
        )
        connectors = re.findall(r"'((?:DP|HDMI|eDP|VGA|DVI)-[^']*)'", out)
        if connectors:
            lines.append(
                f"Connected monitors: {', '.join(dict.fromkeys(connectors))}"
            )
    except Exception:
        lines.append("Connected monitors: unknown")

    # GTK / Adw versions
    lines.append(
        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}"
        f".{Gtk.get_micro_version()}"
    )
    lines.append(
        f"Libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}"
        f".{Adw.get_micro_version()}"
    )

    return "\n".join(lines)


_RELEASE_NOTES = """\
<ul>
  <li>Initial release of Monlay</li>
  <li>Automatic monitor detection by hardware identity (vendor, model, serial)</li>
  <li>Instant layout restore on hotplug — port-independent</li>
  <li>GUI profile editor with live canvas preview and drag-to-reposition</li>
  <li>CLI for headless and scripted usage</li>
  <li>Post-config actions: dock migration, wallpaper refresh, custom commands</li>
  <li>Systemd user service for always-on monitoring</li>
</ul>
"""


class MonitorProfilesApp(Adw.Application):
    """Main application for managing monitor profiles."""

    def __init__(self) -> None:
        super().__init__(
            application_id="com.github.monlay",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        # Set default icon so headerbar / taskbar pick it up
        Gtk.Window.set_default_icon_name("com.github.monlay")

        # Prefer dark color scheme
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

        # Register actions
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = MonitorProfilesWindow(application=self)
        win.present()

    def _on_about(self, action: Gio.SimpleAction, param: None) -> None:
        about = Adw.AboutDialog(
            application_name="Monlay",
            application_icon="com.github.monlay",
            version=__version__,
            developer_name="Luca Sliwinski",
            developers=["Luca Sliwinski"],
            copyright="\u00a9 2026 Luca Sliwinski",
            website="https://github.com/luca-sliwinski/monlay",
            issue_url="https://github.com/luca-sliwinski/monlay/issues",
            license_type=Gtk.License.MIT_X11,
            comments=(
                "Monlay automatically applies your preferred monitor layout "
                "when displays are connected or disconnected. It identifies "
                "monitors by their hardware identity, not port names \u2014 so "
                "your setup is always right, no matter which port you plug into."
            ),
            debug_info=_gather_debug_info(),
            debug_info_filename="monlay-debug.txt",
            release_notes_version=__version__,
            release_notes=_RELEASE_NOTES,
        )
        about.present(self.props.active_window)


def main() -> None:
    setup_logging(level="INFO")
    log.info("Starting monlay GUI")
    app = MonitorProfilesApp()
    app.run(sys.argv)
