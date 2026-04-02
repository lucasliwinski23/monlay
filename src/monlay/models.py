"""
Domain models for monlay.

Re-exports dataclasses from dbus_client and defines higher-level
config/profile models used by the YAML configuration layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Re-export dbus_client dataclasses so callers can import from one place.
from monlay.dbus_client import (  # noqa: F401
    DisplayState,
    LogicalMonitor,
    ModeInfo,
    MonitorInfo,
)


# ---------------------------------------------------------------------------
# Identity / matching
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonitorIdentity:
    """
    Hashable monitor identity used as set/dict key for profile matching.

    Serial is deliberately excluded — many monitors report 0x00000000.
    """
    vendor: str
    product: str

    def __str__(self) -> str:
        return f"{self.vendor}:{self.product}"


# ---------------------------------------------------------------------------
# Profile models (parsed from YAML config)
# ---------------------------------------------------------------------------

@dataclass
class ProfileMonitor:
    """A monitor entry inside a profile's ``monitors:`` section."""
    alias: str
    vendor: str
    product: str
    serial: str | None = None

    @property
    def identity(self) -> MonitorIdentity:
        return MonitorIdentity(vendor=self.vendor, product=self.product)


@dataclass
class ProfileLayout:
    """A layout entry inside a profile's ``layout:`` section."""
    monitor: str          # alias referencing a ProfileMonitor
    x: int = 0
    y: int = 0
    scale: float = 1.0
    primary: bool = False
    transform: int = 0
    mode: str | None = None  # e.g. "5120x1440@120"


# ---------------------------------------------------------------------------
# Post-config actions
# ---------------------------------------------------------------------------

@dataclass
class PostConfigAction:
    """Base for post-configuration actions."""
    type: str

    def describe(self) -> str:
        return f"{self.type}"


@dataclass
class DockMonitorAction(PostConfigAction):
    """Set the preferred Dash-to-Dock monitor connector via dconf."""
    monitor: str  # alias — resolved to connector at runtime

    def describe(self) -> str:
        return f"dock_monitor -> {self.monitor}"


@dataclass
class DockIconSizeAction(PostConfigAction):
    """Set dash-max-icon-size via dconf."""
    value: int

    def describe(self) -> str:
        return f"dock_icon_size -> {self.value}"


@dataclass
class WallpaperRefreshAction(PostConfigAction):
    """Re-set current wallpaper URIs to work around GNOME black wallpaper bug."""

    def describe(self) -> str:
        return "wallpaper_refresh"


@dataclass
class CommandAction(PostConfigAction):
    """Run an arbitrary shell command."""
    command: str

    def describe(self) -> str:
        return f"command: {self.command}"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    """Global settings from the config file."""
    settle_delay_ms: int = 1500
    log_level: str = "INFO"


@dataclass
class Profile:
    """A complete monitor profile from the config file."""
    name: str
    description: str = ""
    monitors: dict[str, ProfileMonitor] = field(default_factory=dict)
    layout: list[ProfileLayout] = field(default_factory=list)
    post_config: list[PostConfigAction] = field(default_factory=list)

    @property
    def identity_set(self) -> frozenset[MonitorIdentity]:
        """The set of MonitorIdentity values this profile expects."""
        return frozenset(m.identity for m in self.monitors.values())

    def __str__(self) -> str:
        mons = ", ".join(self.monitors.keys())
        return f"Profile({self.name!r}, monitors=[{mons}])"
