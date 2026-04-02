"""
Profile editor panel: name/description fields, monitor canvas,
monitor property controls, and post-config settings.
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from monlay.gui.monitor_canvas import CanvasMonitor, MonitorCanvas  # noqa: E402
from monlay.postconfig import _is_dash_to_dock_available  # noqa: E402
from monlay.models import (  # noqa: E402
    DockIconSizeAction,
    DockMonitorAction,
    PostConfigAction,
    Profile,
    ProfileLayout,
    ProfileMonitor,
    WallpaperRefreshAction,
)

log = logging.getLogger(__name__)


class ProfileEditor(Gtk.Box):
    """
    Editor for a single Profile. Combines canvas + property controls.

    Signals:
        profile-saved(profile: GObject.TYPE_PYOBJECT) - emitted when Save is clicked
    """

    __gsignals__ = {
        "profile-saved": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._profile: Profile | None = None
        self._canvas_monitors: list[CanvasMonitor] = []
        self._selected_alias: str | None = None
        # Suppress signals during programmatic updates
        self._updating = False

        # Scrolled window wrapping everything
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.append(scroll)

        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=600)
        scroll.set_child(clamp)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)
        outer.set_margin_start(16)
        outer.set_margin_end(16)
        clamp.set_child(outer)

        # -- Profile info --
        info_group = Adw.PreferencesGroup(title="Profile")
        outer.append(info_group)

        self._name_row = Adw.EntryRow(title="Name")
        info_group.add(self._name_row)

        self._desc_row = Adw.EntryRow(title="Description")
        info_group.add(self._desc_row)

        # -- Monitor canvas --
        canvas_group = Adw.PreferencesGroup(title="Layout")
        outer.append(canvas_group)

        canvas_frame = Gtk.Frame()
        canvas_frame.add_css_class("view")
        canvas_group.add(canvas_frame)

        self._canvas = MonitorCanvas()
        self._canvas.set_size_request(460, 260)
        self._canvas.connect("monitor-selected", self._on_monitor_selected)
        self._canvas.connect("layout-changed", self._on_layout_changed)
        canvas_frame.set_child(self._canvas)

        # -- Monitor properties --
        self._props_group = Adw.PreferencesGroup(title="Monitor Settings")
        self._props_group.set_visible(False)
        outer.append(self._props_group)

        self._selected_label = Gtk.Label(
            label="", xalign=0, css_classes=["dim-label"]
        )
        self._props_group.add(self._selected_label)

        self._resolution_row = Adw.ComboRow(title="Resolution")
        self._resolution_model = Gtk.StringList()
        self._resolution_row.set_model(self._resolution_model)
        self._resolution_row.connect("notify::selected", self._on_resolution_changed)
        self._props_group.add(self._resolution_row)

        self._refresh_row = Adw.ComboRow(title="Refresh Rate")
        self._refresh_model = Gtk.StringList()
        self._refresh_row.set_model(self._refresh_model)
        self._refresh_row.connect("notify::selected", self._on_refresh_changed)
        self._props_group.add(self._refresh_row)

        self._scale_row = Adw.ComboRow(title="Scale")
        self._scale_model = Gtk.StringList()
        for s in ["1", "1.25", "1.5", "1.75", "2"]:
            self._scale_model.append(s)
        self._scale_row.set_model(self._scale_model)
        self._scale_row.connect("notify::selected", self._on_scale_changed)
        self._props_group.add(self._scale_row)

        self._primary_row = Adw.SwitchRow(title="Primary Display")
        self._primary_row.connect("notify::active", self._on_primary_changed)
        self._props_group.add(self._primary_row)

        # -- Post-config section --
        post_group = Adw.PreferencesGroup(title="Post-Configuration")
        outer.append(post_group)

        self._dock_available = _is_dash_to_dock_available()

        self._dock_monitor_row = Adw.ComboRow(title="Dock Monitor")
        self._dock_monitor_model = Gtk.StringList()
        self._dock_monitor_row.set_model(self._dock_monitor_model)
        post_group.add(self._dock_monitor_row)

        self._dock_icon_adj = Gtk.Adjustment(
            value=48, lower=16, upper=128, step_increment=4, page_increment=16
        )
        self._dock_icon_row = Adw.SpinRow(
            title="Dock Icon Size",
            adjustment=self._dock_icon_adj,
        )
        post_group.add(self._dock_icon_row)

        self._dock_unavailable_label = Gtk.Label(
            label="Dock settings available when Dash to Dock is installed",
            css_classes=["dim-label", "caption"],
            margin_top=4,
            margin_bottom=4,
        )
        post_group.add(self._dock_unavailable_label)

        if self._dock_available:
            self._dock_monitor_row.set_visible(True)
            self._dock_icon_row.set_visible(True)
            self._dock_unavailable_label.set_visible(False)
        else:
            self._dock_monitor_row.set_visible(False)
            self._dock_icon_row.set_visible(False)
            self._dock_unavailable_label.set_visible(True)

        self._wallpaper_row = Adw.SwitchRow(title="Wallpaper Refresh")
        self._wallpaper_row.set_subtitle(
            "Re-set wallpaper after hotplug to fix black background"
        )
        post_group.add(self._wallpaper_row)

        # -- Save button --
        btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.END,
            spacing=8,
        )
        btn_box.set_margin_top(8)
        outer.append(btn_box)

        self._save_btn = Gtk.Button(label="Save Profile")
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.connect("clicked", self._on_save_clicked)
        btn_box.append(self._save_btn)

    # -- Public API --

    def load_profile(self, profile: Profile) -> None:
        """Populate the editor from a Profile object."""
        self._updating = True
        self._profile = profile
        self._selected_alias = None

        self._name_row.set_text(profile.name)
        self._desc_row.set_text(profile.description)

        # Build canvas monitors from layout
        self._canvas_monitors = []
        for layout in profile.layout:
            pmon = profile.monitors.get(layout.monitor)
            width, height, refresh = 1920, 1080, 60.0
            if layout.mode:
                parts = layout.mode.replace("@", "x").split("x")
                if len(parts) >= 2:
                    try:
                        width = int(parts[0])
                        height = int(parts[1])
                    except ValueError:
                        pass
                if len(parts) >= 3:
                    try:
                        refresh = float(parts[2])
                    except ValueError:
                        pass

            cm = CanvasMonitor(
                alias=layout.monitor,
                x=layout.x,
                y=layout.y,
                width=width,
                height=height,
                scale=layout.scale,
                primary=layout.primary,
                mode_str=f"{width}x{height}",
                refresh_str=f"{refresh:.0f} Hz",
            )
            self._canvas_monitors.append(cm)

        self._canvas.set_monitors(self._canvas_monitors)

        # Update dock monitor dropdown
        self._dock_monitor_model.splice(0, self._dock_monitor_model.get_n_items(), [])
        aliases = list(profile.monitors.keys())
        for a in aliases:
            self._dock_monitor_model.append(a)

        # Load post-config values
        dock_mon_alias = None
        dock_icon_size = 48
        wallpaper_refresh = False
        for action in profile.post_config:
            if isinstance(action, DockMonitorAction):
                dock_mon_alias = action.monitor
            elif isinstance(action, DockIconSizeAction):
                dock_icon_size = action.value
            elif isinstance(action, WallpaperRefreshAction):
                wallpaper_refresh = True

        if dock_mon_alias and dock_mon_alias in aliases:
            self._dock_monitor_row.set_selected(aliases.index(dock_mon_alias))
        elif aliases:
            self._dock_monitor_row.set_selected(0)

        self._dock_icon_adj.set_value(dock_icon_size)
        self._wallpaper_row.set_active(wallpaper_refresh)

        self._props_group.set_visible(False)
        self._updating = False

    def build_profile(self) -> Profile:
        """Build a Profile from the current editor state."""
        if not self._profile:
            return Profile(name="New Profile")

        name = self._name_row.get_text().strip() or "Unnamed"
        desc = self._desc_row.get_text().strip()

        # Rebuild layout from canvas
        layout: list[ProfileLayout] = []
        for cm in self._canvas_monitors:
            # Find existing layout entry for mode/transform info
            orig_layout = None
            if self._profile:
                for ol in self._profile.layout:
                    if ol.monitor == cm.alias:
                        orig_layout = ol
                        break

            mode_str = None
            transform = 0
            if orig_layout:
                mode_str = orig_layout.mode
                transform = orig_layout.transform

            # If resolution changed from property panel, update mode
            refresh = cm.refresh_str.replace(" Hz", "").strip()
            try:
                refresh_int = int(float(refresh))
            except ValueError:
                refresh_int = 60
            mode_str = f"{cm.width}x{cm.height}@{refresh_int}"

            layout.append(ProfileLayout(
                monitor=cm.alias,
                x=cm.x,
                y=cm.y,
                scale=cm.scale,
                primary=cm.primary,
                transform=transform,
                mode=mode_str,
            ))

        # Build post-config
        post_config: list[PostConfigAction] = []
        if self._dock_available:
            sel_dock_idx = self._dock_monitor_row.get_selected()
            aliases = list(self._profile.monitors.keys()) if self._profile else []
            if aliases and 0 <= sel_dock_idx < len(aliases):
                post_config.append(
                    DockMonitorAction(type="dock_monitor", monitor=aliases[sel_dock_idx])
                )

            icon_size = int(self._dock_icon_adj.get_value())
            post_config.append(
                DockIconSizeAction(type="dock_icon_size", value=icon_size)
            )

        if self._wallpaper_row.get_active():
            post_config.append(WallpaperRefreshAction(type="wallpaper_refresh"))

        return Profile(
            name=name,
            description=desc,
            monitors=dict(self._profile.monitors) if self._profile else {},
            layout=layout,
            post_config=post_config,
        )

    # -- Signal handlers --

    def _on_monitor_selected(self, canvas: MonitorCanvas, alias: str) -> None:
        self._selected_alias = alias if alias else None
        self._update_property_panel()

    def _on_layout_changed(self, canvas: MonitorCanvas) -> None:
        # Sync canvas positions back to our list
        self._canvas_monitors = canvas.get_monitors()

    def _update_property_panel(self) -> None:
        """Update the monitor properties panel for the selected monitor."""
        if not self._selected_alias:
            self._props_group.set_visible(False)
            return

        cm = None
        for m in self._canvas_monitors:
            if m.alias == self._selected_alias:
                cm = m
                break

        if not cm:
            self._props_group.set_visible(False)
            return

        self._updating = True
        self._props_group.set_visible(True)
        self._selected_label.set_label(f"Selected: {cm.alias}")

        # Populate resolution options
        self._resolution_model.splice(
            0, self._resolution_model.get_n_items(), []
        )
        current_res = f"{cm.width}x{cm.height}"
        # Common resolutions to offer
        resolutions = sorted(set([
            current_res,
            "1920x1080", "2560x1440", "3840x2160",
            "3440x1440", "5120x1440", "5120x2160",
            "2560x1600", "3840x2400",
        ]))
        sel_idx = 0
        for i, r in enumerate(resolutions):
            self._resolution_model.append(r)
            if r == current_res:
                sel_idx = i
        self._resolution_row.set_selected(sel_idx)

        # Refresh rates
        self._refresh_model.splice(0, self._refresh_model.get_n_items(), [])
        current_refresh = cm.refresh_str.replace(" Hz", "").strip()
        refreshes = sorted(set([current_refresh, "60", "120", "144", "165", "240"]))
        sel_r = 0
        for i, r in enumerate(refreshes):
            self._refresh_model.append(f"{r} Hz")
            if r == current_refresh:
                sel_r = i
        self._refresh_row.set_selected(sel_r)

        # Scale
        scales = ["1", "1.25", "1.5", "1.75", "2"]
        scale_str = str(cm.scale) if cm.scale != int(cm.scale) else str(int(cm.scale))
        if scale_str in scales:
            self._scale_row.set_selected(scales.index(scale_str))
        else:
            self._scale_row.set_selected(0)

        self._primary_row.set_active(cm.primary)
        self._updating = False

    def _on_resolution_changed(self, row: Adw.ComboRow, pspec: Any) -> None:
        if self._updating or not self._selected_alias:
            return
        sel = row.get_selected_item()
        if not sel:
            return
        res = sel.get_string()
        parts = res.split("x")
        if len(parts) != 2:
            return
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            return
        for cm in self._canvas_monitors:
            if cm.alias == self._selected_alias:
                cm.width = w
                cm.height = h
                cm.mode_str = res
                break
        self._canvas.set_monitors(self._canvas_monitors)

    def _on_refresh_changed(self, row: Adw.ComboRow, pspec: Any) -> None:
        if self._updating or not self._selected_alias:
            return
        sel = row.get_selected_item()
        if not sel:
            return
        for cm in self._canvas_monitors:
            if cm.alias == self._selected_alias:
                cm.refresh_str = sel.get_string()
                break
        self._canvas.set_monitors(self._canvas_monitors)

    def _on_scale_changed(self, row: Adw.ComboRow, pspec: Any) -> None:
        if self._updating or not self._selected_alias:
            return
        sel = row.get_selected_item()
        if not sel:
            return
        try:
            scale = float(sel.get_string())
        except ValueError:
            return
        for cm in self._canvas_monitors:
            if cm.alias == self._selected_alias:
                cm.scale = scale
                break
        self._canvas.set_monitors(self._canvas_monitors)

    def _on_primary_changed(self, row: Adw.SwitchRow, pspec: Any) -> None:
        if self._updating or not self._selected_alias:
            return
        is_primary = row.get_active()
        for cm in self._canvas_monitors:
            if cm.alias == self._selected_alias:
                cm.primary = is_primary
            elif is_primary:
                # Only one primary
                cm.primary = False
        self._canvas.set_monitors(self._canvas_monitors)

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        profile = self.build_profile()
        self.emit("profile-saved", profile)
