"""
Custom Gtk.DrawingArea that renders monitor rectangles with Cairo.

Supports click-to-select and drag-to-reposition with snap-to-edge.
Emits GObject signals for selection changes and layout modifications.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

# Internal representation of a monitor rectangle on the canvas
@dataclass
class CanvasMonitor:
    alias: str
    x: int          # layout x (physical pixels)
    y: int          # layout y (physical pixels)
    width: int      # mode width (physical pixels)
    height: int     # mode height (physical pixels)
    scale: float    # display scale
    primary: bool
    mode_str: str   # e.g. "3840x2160"
    refresh_str: str  # e.g. "60 Hz"

    @property
    def logical_width(self) -> int:
        return int(self.width / self.scale)

    @property
    def logical_height(self) -> int:
        return int(self.height / self.scale)


# Snap distance in canvas pixels
SNAP_DISTANCE = 12
# Padding around the entire layout
CANVAS_PADDING = 40
# Corner radius for monitor rectangles
CORNER_RADIUS = 8
# Minimum canvas size
MIN_CANVAS_WIDTH = 500
MIN_CANVAS_HEIGHT = 300


class MonitorCanvas(Gtk.DrawingArea):
    """
    Renders monitor layout as draggable rectangles.

    Signals:
        monitor-selected(alias: str) - emitted when a monitor is clicked
        layout-changed() - emitted when a monitor is dragged to a new position
    """

    __gsignals__ = {
        "monitor-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "layout-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()

        self._monitors: list[CanvasMonitor] = []
        self._selected_alias: str | None = None
        self._dragging: CanvasMonitor | None = None
        self._drag_offset_x: float = 0
        self._drag_offset_y: float = 0

        # Canvas transform: maps layout coords -> widget coords
        self._scale_factor: float = 1.0
        self._offset_x: float = 0
        self._offset_y: float = 0

        self.set_draw_func(self._draw)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_size_request(MIN_CANVAS_WIDTH, MIN_CANVAS_HEIGHT)

        # Click gesture
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        # Drag gesture
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

    # -- Public API --

    def set_monitors(self, monitors: list[CanvasMonitor]) -> None:
        self._monitors = monitors
        if self._selected_alias and not any(
            m.alias == self._selected_alias for m in monitors
        ):
            self._selected_alias = None
        self._compute_transform()
        self.queue_draw()

    def get_monitors(self) -> list[CanvasMonitor]:
        return list(self._monitors)

    def set_selected(self, alias: str | None) -> None:
        self._selected_alias = alias
        self.queue_draw()

    def get_selected(self) -> str | None:
        return self._selected_alias

    # -- Coordinate transform --

    def _compute_transform(self) -> None:
        """Compute scale and offset to center the layout in the widget."""
        if not self._monitors:
            self._scale_factor = 1.0
            self._offset_x = 0
            self._offset_y = 0
            return

        # Bounding box of the layout in physical pixel coordinates
        # GNOME uses physical pixels for x/y even in logical layout mode
        min_x = min(m.x for m in self._monitors)
        min_y = min(m.y for m in self._monitors)
        max_x = max(m.x + m.width for m in self._monitors)
        max_y = max(m.y + m.height for m in self._monitors)

        layout_w = max_x - min_x
        layout_h = max_y - min_y

        if layout_w == 0 or layout_h == 0:
            self._scale_factor = 1.0
            self._offset_x = 0
            self._offset_y = 0
            return

        alloc = self.get_allocation()
        avail_w = max(alloc.width - 2 * CANVAS_PADDING, 100)
        avail_h = max(alloc.height - 2 * CANVAS_PADDING, 100)

        sx = avail_w / layout_w
        sy = avail_h / layout_h
        self._scale_factor = min(sx, sy, 0.35)  # cap to avoid huge rects

        # Center
        scaled_w = layout_w * self._scale_factor
        scaled_h = layout_h * self._scale_factor
        self._offset_x = (alloc.width - scaled_w) / 2 - min_x * self._scale_factor
        self._offset_y = (alloc.height - scaled_h) / 2 - min_y * self._scale_factor

    def _layout_to_canvas(self, lx: float, ly: float) -> tuple[float, float]:
        return (
            lx * self._scale_factor + self._offset_x,
            ly * self._scale_factor + self._offset_y,
        )

    def _canvas_to_layout(self, cx: float, cy: float) -> tuple[float, float]:
        return (
            (cx - self._offset_x) / self._scale_factor,
            (cy - self._offset_y) / self._scale_factor,
        )

    # -- Hit test --

    def _hit_test(self, cx: float, cy: float) -> CanvasMonitor | None:
        """Find which monitor contains the canvas point (cx, cy)."""
        # Check in reverse order so topmost (last drawn) wins
        for m in reversed(self._monitors):
            rx, ry = self._layout_to_canvas(m.x, m.y)
            rw = m.width * self._scale_factor
            rh = m.height * self._scale_factor
            if rx <= cx <= rx + rw and ry <= cy <= ry + rh:
                return m
        return None

    # -- Click handling --

    def _on_click(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        hit = self._hit_test(x, y)
        if hit:
            self._selected_alias = hit.alias
            self.emit("monitor-selected", hit.alias)
        else:
            self._selected_alias = None
            self.emit("monitor-selected", "")
        self.queue_draw()

    # -- Drag handling --

    def _on_drag_begin(
        self, gesture: Gtk.GestureDrag, start_x: float, start_y: float
    ) -> None:
        hit = self._hit_test(start_x, start_y)
        if hit:
            self._dragging = hit
            rx, ry = self._layout_to_canvas(hit.x, hit.y)
            self._drag_offset_x = start_x - rx
            self._drag_offset_y = start_y - ry
            self._selected_alias = hit.alias
            self.emit("monitor-selected", hit.alias)
            self.queue_draw()

    def _on_drag_update(
        self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float
    ) -> None:
        if not self._dragging:
            return

        ok, start_x, start_y = gesture.get_start_point()
        if not ok:
            return

        cx = start_x + offset_x - self._drag_offset_x
        cy = start_y + offset_y - self._drag_offset_y

        lx, ly = self._canvas_to_layout(cx, cy)
        self._dragging.x = int(lx)
        self._dragging.y = int(ly)
        self.queue_draw()

    def _on_drag_end(
        self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float
    ) -> None:
        if not self._dragging:
            return

        # Snap to edges of other monitors
        self._snap_to_edges(self._dragging)
        self._dragging = None
        self.emit("layout-changed")
        self.queue_draw()

    def _snap_to_edges(self, target: CanvasMonitor) -> None:
        """Snap target monitor edges to other monitors' edges."""
        snap_threshold = SNAP_DISTANCE / self._scale_factor if self._scale_factor > 0 else 20

        t_left = target.x
        t_right = target.x + target.logical_width
        t_top = target.y
        t_bottom = target.y + target.logical_height

        best_dx: float | None = None
        best_dy: float | None = None

        for m in self._monitors:
            if m is target:
                continue

            m_left = m.x
            m_right = m.x + m.width
            m_top = m.y
            m_bottom = m.y + m.height

            # Horizontal snapping
            for t_edge, m_edge in [
                (t_left, m_right), (t_left, m_left),
                (t_right, m_right), (t_right, m_left),
            ]:
                d = m_edge - t_edge
                if abs(d) < snap_threshold:
                    if best_dx is None or abs(d) < abs(best_dx):
                        best_dx = d

            # Vertical snapping
            for t_edge, m_edge in [
                (t_top, m_bottom), (t_top, m_top),
                (t_bottom, m_bottom), (t_bottom, m_top),
            ]:
                d = m_edge - t_edge
                if abs(d) < snap_threshold:
                    if best_dy is None or abs(d) < abs(best_dy):
                        best_dy = d

        if best_dx is not None:
            target.x += int(best_dx)
        if best_dy is not None:
            target.y += int(best_dy)

    # -- Drawing --

    def _draw(
        self, area: Gtk.DrawingArea, cr: "cairo.Context", width: int, height: int
    ) -> None:
        self._compute_transform()

        # Check dark mode
        is_dark = Adw.StyleManager.get_default().get_dark()

        # Background fill
        if is_dark:
            cr.set_source_rgba(0.12, 0.12, 0.14, 1.0)
        else:
            cr.set_source_rgba(0.92, 0.92, 0.94, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Draw subtle grid
        self._draw_grid(cr, width, height, is_dark)

        if not self._monitors:
            # Empty state text
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(14)
            if is_dark:
                cr.set_source_rgba(0.6, 0.6, 0.6, 1.0)
            else:
                cr.set_source_rgba(0.4, 0.4, 0.4, 1.0)
            text = "No monitors in layout"
            extents = cr.text_extents(text)
            cr.move_to(
                (width - extents.width) / 2,
                (height + extents.height) / 2,
            )
            cr.show_text(text)
            return

        # Draw monitors
        for m in self._monitors:
            self._draw_monitor(cr, m, is_dark)

    def _draw_grid(
        self, cr: "cairo.Context", width: int, height: int, is_dark: bool
    ) -> None:
        """Draw a subtle dot grid."""
        spacing = 24
        if is_dark:
            cr.set_source_rgba(0.2, 0.2, 0.22, 0.6)
        else:
            cr.set_source_rgba(0.82, 0.82, 0.84, 0.6)

        for x in range(spacing, width, spacing):
            for y in range(spacing, height, spacing):
                cr.arc(x, y, 0.8, 0, 2 * math.pi)
                cr.fill()

    def _draw_monitor(
        self, cr: "cairo.Context", m: CanvasMonitor, is_dark: bool
    ) -> None:
        selected = m.alias == self._selected_alias
        x, y = self._layout_to_canvas(m.x, m.y)
        w = m.width * self._scale_factor
        h = m.height * self._scale_factor
        r = CORNER_RADIUS

        # Shadow
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.18 if is_dark else 0.10)
        _rounded_rect(cr, x + 3, y + 3, w, h, r)
        cr.fill()
        cr.restore()

        # Monitor body fill
        if selected:
            # Use GNOME accent blue
            if is_dark:
                cr.set_source_rgba(0.24, 0.40, 0.72, 1.0)
            else:
                cr.set_source_rgba(0.30, 0.50, 0.85, 1.0)
        else:
            if is_dark:
                cr.set_source_rgba(0.22, 0.22, 0.24, 1.0)
            else:
                cr.set_source_rgba(0.98, 0.98, 1.0, 1.0)
        _rounded_rect(cr, x, y, w, h, r)
        cr.fill()

        # Border
        if selected:
            cr.set_source_rgba(0.35, 0.55, 0.95, 1.0)
        else:
            if is_dark:
                cr.set_source_rgba(0.35, 0.35, 0.38, 1.0)
            else:
                cr.set_source_rgba(0.72, 0.72, 0.75, 1.0)
        cr.set_line_width(1.5 if selected else 1.0)
        _rounded_rect(cr, x, y, w, h, r)
        cr.stroke()

        # Text color
        if selected:
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        else:
            if is_dark:
                cr.set_source_rgba(0.88, 0.88, 0.90, 1.0)
            else:
                cr.set_source_rgba(0.15, 0.15, 0.18, 1.0)

        cx = x + w / 2
        cy = y + h / 2

        # Alias name (large)
        cr.select_font_face("Sans", 0, 1)  # bold
        font_size = max(11, min(w / 8, h / 4, 20))
        cr.set_font_size(font_size)
        ext = cr.text_extents(m.alias)
        cr.move_to(cx - ext.width / 2, cy - 4)
        cr.show_text(m.alias)

        # Resolution text (smaller)
        cr.select_font_face("Sans", 0, 0)  # normal
        small_size = max(8, font_size * 0.6)
        cr.set_font_size(small_size)

        res_text = m.mode_str
        if m.refresh_str:
            res_text += f" {m.refresh_str}"
        ext2 = cr.text_extents(res_text)
        cr.move_to(cx - ext2.width / 2, cy + small_size + 4)
        cr.show_text(res_text)

        # Scale indicator
        if m.scale != 1.0:
            scale_text = f"{m.scale:.0f}x" if m.scale == int(m.scale) else f"{m.scale}x"
            cr.set_font_size(small_size * 0.85)
            ext3 = cr.text_extents(scale_text)
            cr.move_to(cx - ext3.width / 2, cy + small_size * 2 + 8)
            cr.show_text(scale_text)

        # Primary star indicator
        if m.primary:
            star_size = max(8, min(12, w / 12))
            star_x = x + w - star_size - 6
            star_y = y + star_size + 6
            if selected:
                cr.set_source_rgba(1.0, 0.9, 0.3, 1.0)
            else:
                cr.set_source_rgba(0.85, 0.7, 0.2, 1.0)
            _draw_star(cr, star_x, star_y, star_size)


def _rounded_rect(
    cr: "cairo.Context", x: float, y: float, w: float, h: float, r: float
) -> None:
    """Draw a rounded rectangle path."""
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_star(
    cr: "cairo.Context", cx: float, cy: float, size: float
) -> None:
    """Draw a 5-pointed star centered at (cx, cy)."""
    points = 5
    outer = size
    inner = size * 0.4
    cr.new_sub_path()
    for i in range(points * 2):
        angle = (i * math.pi / points) - math.pi / 2
        radius = outer if i % 2 == 0 else inner
        px = cx + radius * math.cos(angle)
        py = cy + radius * math.sin(angle)
        if i == 0:
            cr.move_to(px, py)
        else:
            cr.line_to(px, py)
    cr.close_path()
    cr.fill()
