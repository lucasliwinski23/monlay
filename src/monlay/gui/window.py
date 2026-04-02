"""
Main application window with NavigationSplitView: sidebar (profiles) + content (editor).
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gdk, Gtk  # noqa: E402

from monlay.config import (  # noqa: E402
    Config,
    DEFAULT_CONFIG_PATH,
    load_config,
    save_current_as_profile,
)
from monlay.models import (  # noqa: E402
    DisplayState,
    MonitorIdentity,
    Profile,
    ProfileLayout,
    ProfileMonitor,
)
from monlay.gui.profile_editor import ProfileEditor  # noqa: E402

log = logging.getLogger(__name__)


class ProfileRow(Gtk.ListBoxRow):
    """A sidebar row for a single profile."""

    def __init__(self, profile: Profile, is_active: bool = False) -> None:
        super().__init__()
        self.profile = profile

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        self.set_child(box)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        box.append(text_box)

        self._name_label = Gtk.Label(
            label=profile.name,
            xalign=0,
            css_classes=["heading"],
        )
        text_box.append(self._name_label)

        subtitle = profile.description or f"{len(profile.monitors)} monitor(s)"
        self._desc_label = Gtk.Label(
            label=subtitle,
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        text_box.append(self._desc_label)

        if is_active:
            indicator = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            indicator.add_css_class("success")
            indicator.set_tooltip_text("Active profile")
            box.append(indicator)


class MonitorProfilesWindow(Adw.ApplicationWindow):
    """Main window: sidebar with profiles, content with editor."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_title("Monlay")
        self.set_default_size(1050, 700)

        self._config: Config | None = None
        self._display_state: DisplayState | None = None
        self._active_profile_name: str | None = None

        # Toast overlay wraps everything
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        # Main layout: NavigationSplitView
        self._split = Adw.NavigationSplitView()
        self._toast_overlay.set_child(self._split)

        # -- Sidebar --
        sidebar_page = Adw.NavigationPage(title="Profiles")
        self._split.set_sidebar(sidebar_page)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar_page.set_child(sidebar_box)

        # Sidebar header bar
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_title(True)

        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_tooltip_text("Add Profile")
        add_btn.connect("clicked", self._on_add_profile)
        sidebar_header.pack_start(add_btn)

        sidebar_box.append(sidebar_header)

        # Profile list
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        sidebar_box.append(scroll)

        self._listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._listbox.add_css_class("navigation-sidebar")
        self._listbox.connect("row-selected", self._on_profile_selected)
        scroll.set_child(self._listbox)

        # Sidebar action buttons
        action_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
            halign=Gtk.Align.CENTER,
        )
        sidebar_box.append(action_bar)

        self._apply_btn = Gtk.Button(label="Apply Now")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.set_tooltip_text("Apply selected profile")
        self._apply_btn.set_sensitive(False)
        self._apply_btn.connect("clicked", self._on_apply_profile)
        action_bar.append(self._apply_btn)

        self._delete_btn = Gtk.Button(label="Delete")
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.set_sensitive(False)
        self._delete_btn.connect("clicked", self._on_delete_profile)
        action_bar.append(self._delete_btn)

        # -- Content --
        content_page = Adw.NavigationPage(title="Editor")
        self._split.set_content(content_page)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_page.set_child(content_box)

        # Content header bar
        content_header = Adw.HeaderBar()

        detect_btn = Gtk.Button(label="Scan Displays")
        detect_btn.set_tooltip_text("Scan for currently connected displays")
        detect_btn.connect("clicked", self._on_detect_monitors)
        content_header.pack_start(detect_btn)

        # Service on/off toggle in header bar
        service_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        service_label = Gtk.Label(label="Automatic")
        service_label.add_css_class("dim-label")
        service_box.append(service_label)
        self._service_switch = Gtk.Switch()
        self._service_switch.set_valign(Gtk.Align.CENTER)
        self._service_switch.set_tooltip_text("Automatically apply layouts when displays change")
        self._service_switch.set_active(self._is_service_active())
        self._service_switch.connect("state-set", self._on_service_toggled)
        service_box.append(self._service_switch)
        content_header.pack_end(service_box)

        # Hamburger menu
        menu = Gio.Menu()
        menu.append("About", "app.about")
        hamburger = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=menu,
        )
        content_header.pack_end(hamburger)
        content_box.append(content_header)

        # Content stack: empty state vs editor
        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_box.append(self._content_stack)

        # Empty state
        empty_page = Adw.StatusPage(
            icon_name="preferences-desktop-display-symbolic",
            title="Welcome to Monlay",
            description="Manage your monitor layouts and switch between them automatically.\nSelect a profile from the sidebar or click + to create one.",
        )
        self._content_stack.add_named(empty_page, "empty")

        # Editor
        self._editor = ProfileEditor()
        self._editor.connect("profile-saved", self._on_profile_saved)
        self._content_stack.add_named(self._editor, "editor")

        self._content_stack.set_visible_child_name("empty")

        # Load config on startup
        GLib.idle_add(self._load_config_async)

    # -- Config loading --

    def _load_config_async(self) -> bool:
        """Load config file in a thread to not block the UI."""
        threading.Thread(target=self._load_config_thread, daemon=True).start()
        return False  # don't repeat

    def _load_config_thread(self) -> None:
        config = None
        try:
            config = load_config()
        except FileNotFoundError:
            log.info("No config file found at %s", DEFAULT_CONFIG_PATH)
        except Exception:
            log.exception("Failed to load config")
            GLib.idle_add(self._show_toast, "Failed to load config (see logs)")

        GLib.idle_add(self._config_loaded, config)

    def _config_loaded(self, config: Config | None) -> None:
        self._config = config
        self._refresh_sidebar()
        if config and config.profiles:
            self._show_toast(f"Loaded {len(config.profiles)} profile(s)")
        return False

    # -- Sidebar --

    def _refresh_sidebar(self) -> None:
        # Remove all children
        while True:
            row = self._listbox.get_row_at_index(0)
            if row is None:
                break
            self._listbox.remove(row)

        if not self._config:
            return

        # Determine active profile
        active_set: frozenset[MonitorIdentity] | None = None
        if self._display_state:
            identities = set()
            for m in self._display_state.monitors:
                identities.add(MonitorIdentity(vendor=m.vendor, product=m.product))
            active = self._config.match_profile(identities)
            if active:
                self._active_profile_name = active.name
            else:
                self._active_profile_name = None

        for profile in self._config.profiles:
            is_active = profile.name == self._active_profile_name
            row = ProfileRow(profile, is_active=is_active)
            self._listbox.append(row)

    def _on_profile_selected(
        self, listbox: Gtk.ListBox, row: ProfileRow | None
    ) -> None:
        if row and hasattr(row, "profile"):
            self._editor.load_profile(row.profile)
            self._content_stack.set_visible_child_name("editor")
            self._apply_btn.set_sensitive(True)
            self._delete_btn.set_sensitive(True)
        else:
            self._content_stack.set_visible_child_name("empty")
            self._apply_btn.set_sensitive(False)
            self._delete_btn.set_sensitive(False)

    # -- Actions --

    def _on_add_profile(self, button: Gtk.Button) -> None:
        """Add a new empty profile."""
        if not self._config:
            from monlay.models import Settings
            self._config = Config(settings=Settings(), profiles=[])

        # Generate unique name
        existing = {p.name for p in self._config.profiles}
        name = "New Profile"
        i = 2
        while name in existing:
            name = f"New Profile {i}"
            i += 1

        new_profile = Profile(name=name)
        self._config.profiles.append(new_profile)
        self._refresh_sidebar()

        # Select the new row
        idx = len(self._config.profiles) - 1
        row = self._listbox.get_row_at_index(idx)
        if row:
            self._listbox.select_row(row)

        self._show_toast(f"Created profile '{name}'")

    def _on_delete_profile(self, button: Gtk.Button) -> None:
        row = self._listbox.get_selected_row()
        if not row or not hasattr(row, "profile") or not self._config:
            return

        name = row.profile.name
        self._config.profiles = [
            p for p in self._config.profiles if p.name != name
        ]
        self._refresh_sidebar()
        self._content_stack.set_visible_child_name("empty")
        self._apply_btn.set_sensitive(False)
        self._delete_btn.set_sensitive(False)
        self._show_toast(f"Deleted profile '{name}'")

    def _on_apply_profile(self, button: Gtk.Button) -> None:
        row = self._listbox.get_selected_row()
        if not row or not hasattr(row, "profile"):
            return

        profile = row.profile
        self._show_toast(f"Applying profile '{profile.name}'...")

        def _apply_thread() -> None:
            try:
                from monlay.configurator import apply_profile
                apply_profile(profile)
                GLib.idle_add(self._on_apply_done, profile.name, None)
            except Exception as e:
                log.exception("Failed to apply profile '%s'", profile.name)
                GLib.idle_add(self._on_apply_done, profile.name, str(e))

        threading.Thread(target=_apply_thread, daemon=True).start()

    def _on_apply_done(self, name: str, error: str | None) -> bool:
        if error:
            self._show_toast(f"Failed to apply '{name}': {error}")
        else:
            self._show_toast(f"Layout '{name}' applied")
            self._active_profile_name = name
            self._refresh_sidebar()
        return False

    def _on_detect_monitors(self, button: Gtk.Button) -> None:
        """Detect current monitors via DBus in a background thread."""
        self._show_toast("Scanning for displays...")

        def _detect_thread() -> None:
            try:
                from monlay.dbus_client import get_current_state
                state = get_current_state()
                GLib.idle_add(self._on_monitors_detected, state, None)
            except Exception as e:
                log.exception("Failed to detect monitors")
                GLib.idle_add(self._on_monitors_detected, None, str(e))

        threading.Thread(target=_detect_thread, daemon=True).start()

    def _on_monitors_detected(
        self, state: DisplayState | None, error: str | None
    ) -> bool:
        if error:
            self._show_toast(f"Could not detect displays: {error}")
            return False

        if not state:
            self._show_toast("No display state returned")
            return False

        self._display_state = state
        n = len(state.monitors)
        names = ", ".join(
            m.display_name or m.connector for m in state.monitors
        )
        self._show_toast(f"Detected {n} monitor(s): {names}")
        self._refresh_sidebar()
        return False

    def _on_profile_saved(self, editor: ProfileEditor, profile: Profile) -> None:
        """Handle profile-saved signal from the editor."""
        if not self._config:
            from monlay.models import Settings
            self._config = Config(settings=Settings(), profiles=[])

        # Replace or add
        replaced = False
        for i, p in enumerate(self._config.profiles):
            if p.name == profile.name:
                self._config.profiles[i] = profile
                replaced = True
                break

        # Also check if we renamed: replace currently selected
        if not replaced:
            row = self._listbox.get_selected_row()
            if row and hasattr(row, "profile"):
                old_name = row.profile.name
                for i, p in enumerate(self._config.profiles):
                    if p.name == old_name:
                        self._config.profiles[i] = profile
                        replaced = True
                        break

        if not replaced:
            self._config.profiles.append(profile)

        # Save to disk
        self._save_config_to_disk()
        self._refresh_sidebar()

        # Re-select
        for i, p in enumerate(self._config.profiles):
            if p.name == profile.name:
                row = self._listbox.get_row_at_index(i)
                if row:
                    self._listbox.select_row(row)
                break

        self._show_toast(f"Saved profile '{profile.name}'")

    def _save_config_to_disk(self) -> None:
        """Persist current config to YAML."""
        if not self._config:
            return

        import yaml

        path = DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)

        raw: dict[str, Any] = {
            "settings": {
                "settle_delay_ms": self._config.settings.settle_delay_ms,
                "log_level": self._config.settings.log_level,
            },
            "profiles": [],
        }

        for profile in self._config.profiles:
            p_dict: dict[str, Any] = {
                "name": profile.name,
                "description": profile.description,
                "monitors": {},
                "layout": [],
                "post_config": [],
            }

            for alias, mon in profile.monitors.items():
                entry: dict[str, str] = {
                    "vendor": mon.vendor,
                    "product": mon.product,
                }
                if mon.serial:
                    entry["serial"] = mon.serial
                p_dict["monitors"][alias] = entry

            for layout in profile.layout:
                l_dict: dict[str, Any] = {
                    "monitor": layout.monitor,
                    "x": layout.x,
                    "y": layout.y,
                    "scale": layout.scale,
                    "primary": layout.primary,
                }
                if layout.transform != 0:
                    l_dict["transform"] = layout.transform
                if layout.mode:
                    l_dict["mode"] = layout.mode
                p_dict["layout"].append(l_dict)

            for action in profile.post_config:
                a_dict: dict[str, Any] = {"type": action.type}
                if hasattr(action, "monitor"):
                    a_dict["monitor"] = action.monitor
                if hasattr(action, "value"):
                    a_dict["value"] = action.value
                if hasattr(action, "command"):
                    a_dict["command"] = action.command
                p_dict["post_config"].append(a_dict)

            raw["profiles"].append(p_dict)

        try:
            with open(path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
            log.info("Config saved to %s", path)
        except Exception:
            log.exception("Failed to save config")
            self._show_toast("Failed to save config (see logs)")

    # -- Service control --

    def _is_service_active(self) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "monlay.service"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() == "active"
        except Exception:
            return False

    def _on_service_toggled(self, switch: Gtk.Switch, state: bool) -> bool:
        def _toggle():
            try:
                if state:
                    subprocess.run(
                        ["systemctl", "--user", "enable", "--now", "monlay.service"],
                        capture_output=True, timeout=10,
                    )
                    log.info("Enabled monlay.service")
                    GLib.idle_add(self._show_toast, "Auto-switch enabled")
                else:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", "monlay.service"],
                        capture_output=True, timeout=10,
                    )
                    log.info("Disabled monlay.service")
                    GLib.idle_add(self._show_toast, "Auto-switch disabled")
            except Exception as e:
                log.exception("Service toggle failed")
                GLib.idle_add(self._show_toast, f"Service error: {e}")

        threading.Thread(target=_toggle, daemon=True).start()
        return False

    # -- Toast helper --

    def _show_toast(self, message: str) -> bool:
        toast = Adw.Toast(title=message, timeout=3)
        self._toast_overlay.add_toast(toast)
        return False
