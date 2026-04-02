"""
DBus client for GNOME Mutter DisplayConfig API.

Interfaces with org.gnome.Mutter.DisplayConfig on the session bus
to query and configure monitor layouts under GNOME/Wayland.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)

DBUS_NAME = "org.gnome.Mutter.DisplayConfig"
DBUS_PATH = "/org/gnome/Mutter/DisplayConfig"
DBUS_IFACE = "org.gnome.Mutter.DisplayConfig"


class DBusError(Exception):
    """Raised when a DBus operation fails (connection, call, etc.)."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModeInfo:
    """A display mode (resolution + refresh rate)."""
    id: str            # e.g. "5120x1440@119.999"
    width: int
    height: int
    refresh_rate: float
    preferred_scale: float
    supported_scales: list[float]
    is_current: bool = False
    is_preferred: bool = False

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def __str__(self) -> str:
        return self.id


@dataclass
class MonitorInfo:
    """A physical monitor (one EDID entity)."""
    connector: str     # e.g. "DP-8", "eDP-1"
    vendor: str        # e.g. "SAM", "AUO"
    product: str       # e.g. "LS49A950U"
    serial: str        # e.g. "HNTW500083"
    modes: list[ModeInfo] = field(default_factory=list)
    is_builtin: bool = False
    display_name: str = ""
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def current_mode(self) -> ModeInfo | None:
        for m in self.modes:
            if m.is_current:
                return m
        return None

    @property
    def preferred_mode(self) -> ModeInfo | None:
        for m in self.modes:
            if m.is_preferred:
                return m
        return None

    @property
    def current_mode_str(self) -> str:
        """Format: 'WxH@rate' or empty string if no current mode."""
        m = self.current_mode
        if m is None:
            return ""
        return f"{m.width}x{m.height}@{m.refresh_rate:.3f}"

    @property
    def edid_tuple(self) -> tuple[str, str, str, str]:
        """(connector, vendor, product, serial) — the monitor spec tuple Mutter uses."""
        return (self.connector, self.vendor, self.product, self.serial)

    def __str__(self) -> str:
        mode = self.current_mode_str or "disabled"
        name = self.display_name or f"{self.vendor} {self.product}"
        return f"{self.connector} [{name}] {mode}"


@dataclass
class LogicalMonitor:
    """A logical monitor (a positioned rectangle on the virtual desktop)."""
    x: int
    y: int
    scale: float
    transform: int     # 0=normal, 1=90, 2=180, 3=270, 4=flipped, ...
    primary: bool
    monitors: list[MonitorInfo] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        names = ", ".join(m.connector for m in self.monitors)
        return f"logical@({self.x},{self.y}) scale={self.scale} primary={self.primary} [{names}]"


@dataclass
class DisplayState:
    """Complete snapshot of the current display configuration."""
    serial: int
    monitors: list[MonitorInfo]
    logical_monitors: list[LogicalMonitor]
    layout_mode: int   # 1=physical, 2=logical
    properties: dict[str, Any] = field(default_factory=dict)

    def find_monitor(self, connector: str) -> MonitorInfo | None:
        for m in self.monitors:
            if m.connector == connector:
                return m
        return None

    def __str__(self) -> str:
        lines = [f"DisplayState serial={self.serial} layout_mode={self.layout_mode}"]
        lines.append("  Physical monitors:")
        for m in self.monitors:
            lines.append(f"    {m}")
        lines.append("  Logical monitors:")
        for lm in self.logical_monitors:
            lines.append(f"    {lm}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Known monitor EDID signatures for identify_monitors()
# ---------------------------------------------------------------------------

# Map from (vendor, product) to a friendly name.
# Extend this dict with your actual monitors.
KNOWN_MONITORS: dict[tuple[str, str], str] = {
    ("AUO", "0x87a8"): "laptop",
    ("SAM", "LS49A950U"): "samsung",
    ("IVM", "5E27"): "iiyama",       # placeholder — update product code
    ("IVM", "5E34"): "iiyama",       # common iiyama variant
}


# ---------------------------------------------------------------------------
# DBus proxy helper
# ---------------------------------------------------------------------------

def _get_proxy() -> Gio.DBusProxy:
    """Create a synchronous DBus proxy for Mutter DisplayConfig."""
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,  # GDBusInterfaceInfo — not needed
            DBUS_NAME,
            DBUS_PATH,
            DBUS_IFACE,
            None,  # GCancellable
        )
    except GLib.Error as e:
        log.error("Failed to connect to DBus: %s", e.message)
        raise DBusError(
            f"Cannot connect to {DBUS_IFACE} on session bus: {e.message}. "
            "Is a GNOME Wayland session running?"
        ) from e


def _unpack_variant(v: GLib.Variant) -> Any:
    """Recursively unpack a GLib.Variant into plain Python types."""
    if isinstance(v, GLib.Variant):
        type_str = v.get_type_string()
        if type_str == "v":
            return _unpack_variant(v.get_variant())
        elif type_str == "b":
            return v.get_boolean()
        elif type_str == "s":
            return v.get_string()
        elif type_str == "u":
            return v.get_uint32()
        elif type_str == "i":
            return v.get_int32()
        elif type_str == "d":
            return v.get_double()
        elif type_str.startswith("a{"):
            result = {}
            n = v.n_children()
            for i in range(n):
                entry = v.get_child_value(i)
                key = _unpack_variant(entry.get_child_value(0))
                val = _unpack_variant(entry.get_child_value(1))
                result[key] = val
            return result
        elif type_str.startswith("a") or type_str.startswith("("):
            return [_unpack_variant(v.get_child_value(i)) for i in range(v.n_children())]
        else:
            # Fallback: try unpack, then str
            try:
                return v.unpack()
            except Exception:
                return str(v)
    return v


def _variant_dict(v: GLib.Variant) -> dict[str, Any]:
    """Unpack an a{sv} variant into a Python dict."""
    return _unpack_variant(v)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_state() -> DisplayState:
    """
    Call GetCurrentState and return a parsed DisplayState.

    DBus signature:
        GetCurrentState() -> (
            u serial,
            a((ssss)a(siiddada{sv})a{sv}) monitors,
            a(iiduba(ssss)a{sv}) logical_monitors,
            a{sv} properties
        )
    """
    proxy = _get_proxy()
    try:
        result = proxy.call_sync(
            "GetCurrentState",
            None,  # no parameters
            Gio.DBusCallFlags.NONE,
            -1,    # default timeout
            None,  # GCancellable
        )
    except GLib.Error as e:
        log.error("GetCurrentState DBus call failed: %s", e.message)
        raise DBusError(f"GetCurrentState failed: {e.message}") from e

    serial = result.get_child_value(0).get_uint32()
    raw_monitors = result.get_child_value(1)
    raw_logical = result.get_child_value(2)
    raw_props = result.get_child_value(3)

    # -- Parse physical monitors --
    monitors: list[MonitorInfo] = []
    for i in range(raw_monitors.n_children()):
        mon_tuple = raw_monitors.get_child_value(i)
        # Element 0: (ssss) monitor spec
        spec = mon_tuple.get_child_value(0)
        connector = spec.get_child_value(0).get_string()
        vendor = spec.get_child_value(1).get_string()
        product = spec.get_child_value(2).get_string()
        mon_serial = spec.get_child_value(3).get_string()

        # Element 1: a(siiddada{sv}) modes
        raw_modes = mon_tuple.get_child_value(1)
        modes: list[ModeInfo] = []
        for j in range(raw_modes.n_children()):
            mode_tuple = raw_modes.get_child_value(j)
            mode_id = mode_tuple.get_child_value(0).get_string()
            width = mode_tuple.get_child_value(1).get_int32()
            height = mode_tuple.get_child_value(2).get_int32()
            refresh = mode_tuple.get_child_value(3).get_double()
            pref_scale = mode_tuple.get_child_value(4).get_double()
            # Element 5: ad (supported scales)
            raw_scales = mode_tuple.get_child_value(5)
            scales = [raw_scales.get_child_value(k).get_double()
                      for k in range(raw_scales.n_children())]
            # Element 6: a{sv} mode properties
            mode_props = _variant_dict(mode_tuple.get_child_value(6))
            modes.append(ModeInfo(
                id=mode_id,
                width=width,
                height=height,
                refresh_rate=refresh,
                preferred_scale=pref_scale,
                supported_scales=scales,
                is_current=mode_props.get("is-current", False),
                is_preferred=mode_props.get("is-preferred", False),
            ))

        # Element 2: a{sv} monitor properties
        mon_props = _variant_dict(mon_tuple.get_child_value(2))
        monitors.append(MonitorInfo(
            connector=connector,
            vendor=vendor,
            product=product,
            serial=mon_serial,
            modes=modes,
            is_builtin=mon_props.get("is-builtin", False),
            display_name=mon_props.get("display-name", ""),
            properties=mon_props,
        ))

    # -- Parse logical monitors --
    # Signature: a(iiduba(ssss)a{sv})
    logical_monitors: list[LogicalMonitor] = []
    for i in range(raw_logical.n_children()):
        lm_tuple = raw_logical.get_child_value(i)
        x = lm_tuple.get_child_value(0).get_int32()
        y = lm_tuple.get_child_value(1).get_int32()
        scale = lm_tuple.get_child_value(2).get_double()
        transform = lm_tuple.get_child_value(3).get_uint32()
        primary = lm_tuple.get_child_value(4).get_boolean()

        # Element 5: a(ssss) — associated physical monitor specs
        raw_assoc = lm_tuple.get_child_value(5)
        assoc_monitors: list[MonitorInfo] = []
        for j in range(raw_assoc.n_children()):
            assoc_spec = raw_assoc.get_child_value(j)
            a_conn = assoc_spec.get_child_value(0).get_string()
            # Find the matching physical monitor by connector
            for m in monitors:
                if m.connector == a_conn:
                    assoc_monitors.append(m)
                    break

        # Element 6: a{sv} logical monitor properties
        lm_props = _variant_dict(lm_tuple.get_child_value(6))
        logical_monitors.append(LogicalMonitor(
            x=x,
            y=y,
            scale=scale,
            transform=transform,
            primary=primary,
            monitors=assoc_monitors,
            properties=lm_props,
        ))

    # -- Parse global properties --
    props = _variant_dict(raw_props)
    layout_mode = props.get("layout-mode", 0)

    state = DisplayState(
        serial=serial,
        monitors=monitors,
        logical_monitors=logical_monitors,
        layout_mode=layout_mode,
        properties=props,
    )
    log.debug(
        "GetCurrentState: serial=%d, %d monitors, %d logical, layout_mode=%d",
        serial, len(monitors), len(logical_monitors), layout_mode,
    )
    for m in monitors:
        log.debug(
            "  monitor %s: vendor=%s product=%s serial=%s mode=%s",
            m.connector, m.vendor, m.product, m.serial, m.current_mode_str or "none",
        )
    return state


def apply_config(
    serial: int,
    logical_monitors: list[dict],
    method: int = 1,
    properties: dict[str, Any] | None = None,
) -> None:
    """
    Call ApplyMonitorsConfig to apply a display layout.

    Args:
        serial: Config serial from get_current_state().
        logical_monitors: List of dicts, each with:
            - x: int
            - y: int
            - scale: float
            - primary: bool
            - transform: int (default 0)
            - monitors: list of dicts with:
                - connector: str
                - mode: str (mode ID like "5120x1440@119.999")
                - properties: dict (optional, usually empty)
        method: 0=verify, 1=temporary, 2=persistent
        properties: Global properties dict (optional).

    DBus signature:
        ApplyMonitorsConfig(
            u serial,
            u method,
            a(iiduba(ssa{sv})) logical_monitors,
            a{sv} properties
        )
    """
    proxy = _get_proxy()

    def _build_asv(d: dict[str, Any]) -> GLib.Variant:
        """Build an a{sv} variant, handling empty dicts correctly."""
        if not d:
            return GLib.Variant.new_array(GLib.VariantType.new("{sv}"), [])
        entries = []
        for k, v in d.items():
            if isinstance(v, GLib.Variant):
                entries.append(GLib.Variant("{sv}", (k, v)))
            else:
                entries.append(GLib.Variant("{sv}", (k, GLib.Variant("s", str(v)))))
        return GLib.Variant.new_array(GLib.VariantType.new("{sv}"), entries)

    # Build the logical_monitors variant: a(iiduba(ssa{sv}))
    lm_variants = []
    for lm in logical_monitors:
        mon_entries = []
        for mon in lm["monitors"]:
            # Each monitor entry: (ssa{sv})
            mon_props_variant = _build_asv(mon.get("properties", {}))
            mon_variant = GLib.Variant.new_tuple(
                GLib.Variant("s", mon["connector"]),
                GLib.Variant("s", mon["mode"]),
                mon_props_variant,
            )
            mon_entries.append(mon_variant)

        transform = lm.get("transform", 0)
        monitors_array = GLib.Variant.new_array(
            GLib.VariantType.new("(ssa{sv})"),
            mon_entries,
        )
        lm_variant = GLib.Variant.new_tuple(
            GLib.Variant("i", lm["x"]),
            GLib.Variant("i", lm["y"]),
            GLib.Variant("d", float(lm["scale"])),
            GLib.Variant("u", transform),
            GLib.Variant("b", lm["primary"]),
            monitors_array,
        )
        lm_variants.append(lm_variant)

    # Build properties variant: a{sv}
    props_variant = _build_asv(properties or {})

    lm_array = GLib.Variant.new_array(
        GLib.VariantType.new("(iiduba(ssa{sv}))"),
        lm_variants,
    )

    params = GLib.Variant.new_tuple(
        GLib.Variant("u", serial),
        GLib.Variant("u", method),
        lm_array,
        props_variant,
    )

    log.info("ApplyMonitorsConfig method=%d serial=%d monitors=%d",
             method, serial, len(lm_variants))

    try:
        proxy.call_sync(
            "ApplyMonitorsConfig",
            params,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error as e:
        log.error("ApplyMonitorsConfig DBus call failed: %s", e.message)
        raise DBusError(f"ApplyMonitorsConfig failed: {e.message}") from e


def identify_monitors(
    physical_monitors: list[MonitorInfo],
    known: dict[tuple[str, str], str] | None = None,
) -> dict[str, MonitorInfo]:
    """
    Match physical monitors against known EDID signatures.

    Returns a dict mapping friendly name -> MonitorInfo.
    Unknown monitors are included with their connector as key.

    Args:
        physical_monitors: List from DisplayState.monitors.
        known: Optional override for KNOWN_MONITORS mapping.
    """
    lookup = known if known is not None else KNOWN_MONITORS
    result: dict[str, MonitorInfo] = {}

    for mon in physical_monitors:
        key = (mon.vendor, mon.product)
        name = lookup.get(key)
        if name is None:
            log.warning(
                "Unknown monitor: connector=%s vendor=%s product=%s serial=%s",
                mon.connector, mon.vendor, mon.product, mon.serial,
            )
            name = mon.connector
        result[name] = mon

    return result


def build_logical_monitor(
    monitor: MonitorInfo,
    x: int,
    y: int,
    scale: float = 1.0,
    primary: bool = False,
    transform: int = 0,
    mode: str | None = None,
) -> dict:
    """
    Helper to build a logical_monitor dict for apply_config().

    If mode is None, uses the monitor's current mode.
    """
    if mode is None:
        cm = monitor.current_mode
        if cm is None:
            cm = monitor.preferred_mode
        if cm is None:
            raise ValueError(f"No current or preferred mode for {monitor.connector}")
        mode = cm.id

    return {
        "x": x,
        "y": y,
        "scale": scale,
        "primary": primary,
        "transform": transform,
        "monitors": [{
            "connector": monitor.connector,
            "mode": mode,
            "properties": {},
        }],
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")

    print("=" * 70)
    print("Querying GetCurrentState...")
    print("=" * 70)
    state = get_current_state()
    print(state)
    print()

    print("=" * 70)
    print("Identifying monitors...")
    print("=" * 70)
    identified = identify_monitors(state.monitors)
    for name, mon in identified.items():
        print(f"  {name:12s} -> {mon}")
    print()

    print("=" * 70)
    print("Monitor details (JSON-like):")
    print("=" * 70)
    for mon in state.monitors:
        cm = mon.current_mode
        print(f"  {mon.connector}:")
        print(f"    vendor={mon.vendor} product={mon.product} serial={mon.serial}")
        print(f"    builtin={mon.is_builtin} display_name={mon.display_name!r}")
        if cm:
            print(f"    current_mode={cm.id} ({cm.width}x{cm.height}@{cm.refresh_rate:.3f})")
            print(f"    preferred_scale={cm.preferred_scale} supported_scales={cm.supported_scales}")
        print(f"    available_modes={len(mon.modes)}")

    print()
    print("=" * 70)
    print("Logical monitors:")
    print("=" * 70)
    for lm in state.logical_monitors:
        print(f"  pos=({lm.x},{lm.y}) scale={lm.scale} transform={lm.transform} primary={lm.primary}")
        for m in lm.monitors:
            print(f"    -> {m.connector}")

    print(f"\nlayout_mode={state.layout_mode} ({'physical' if state.layout_mode == 1 else 'logical'})")
    print(f"serial={state.serial}")
    print(f"global properties: {state.properties}")
