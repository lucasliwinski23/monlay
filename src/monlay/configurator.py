"""
Layout configurator for monlay.

Takes a matched Profile and the current DisplayState, resolves aliases
to connectors, fuzzy-matches mode strings, and calls apply_config().
Handles stale serial errors with one automatic retry.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from monlay.dbus_client import (
    DBusError,
    MonitorInfo,
    apply_config,
    get_current_state,
)
from monlay.models import (
    DisplayState,
    Profile,
    ProfileLayout,
)

log = logging.getLogger(__name__)


class ConfiguratorError(Exception):
    """Raised when layout application fails."""


class StaleSerialError(ConfiguratorError):
    """Raised when Mutter rejects the serial (config changed underneath us)."""


# ---------------------------------------------------------------------------
# Mode matching
# ---------------------------------------------------------------------------

_MODE_RE = re.compile(r"^(\d+)x(\d+)@(\d+(?:\.\d+)?)$")


def fuzzy_match_mode(
    pattern: str,
    monitor: MonitorInfo,
    tolerance: float = 1.0,
) -> str | None:
    """
    Match a mode pattern like "5120x1440@120" against the monitor's
    available modes.  The refresh rate is fuzzy-matched within *tolerance* Hz.

    Returns the exact mode ID string (e.g. "5120x1440@119.999") or None.
    """
    m = _MODE_RE.match(pattern)
    if m is None:
        log.warning("Invalid mode pattern: %s", pattern)
        return None

    want_w = int(m.group(1))
    want_h = int(m.group(2))
    want_rate = float(m.group(3))

    best_mode = None
    best_delta = tolerance + 1

    for mode in monitor.modes:
        if mode.width != want_w or mode.height != want_h:
            continue
        delta = abs(mode.refresh_rate - want_rate)
        if delta < best_delta:
            best_delta = delta
            best_mode = mode

    if best_mode is not None and best_delta <= tolerance:
        log.debug(
            "Mode %s matched to %s (delta=%.3f Hz)",
            pattern, best_mode.id, best_delta,
        )
        return best_mode.id

    log.warning(
        "No mode matching %s on %s (best delta=%.3f)",
        pattern, monitor.connector, best_delta if best_mode else float("inf"),
    )
    return None


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

def resolve_aliases(
    profile: Profile,
    state: DisplayState,
) -> dict[str, MonitorInfo]:
    """
    Map profile monitor aliases to MonitorInfo from the current state.

    Matches by (vendor, product).  Raises ConfiguratorError if a profile
    monitor cannot be found among connected monitors.
    """
    result: dict[str, MonitorInfo] = {}
    connected_ids = [(m.connector, m.vendor, m.product) for m in state.monitors]
    log.debug("Resolving aliases against connected monitors: %s", connected_ids)

    for alias, pmon in profile.monitors.items():
        found = None
        for mon in state.monitors:
            if mon.vendor == pmon.vendor and mon.product == pmon.product:
                found = mon
                break
        if found is None:
            connected_str = ", ".join(
                f"{m.connector}({m.vendor}:{m.product})" for m in state.monitors
            )
            raise ConfiguratorError(
                f"Profile monitor {alias!r} ({pmon.vendor}:{pmon.product}) "
                f"not found among connected monitors: {connected_str}"
            )
        log.debug("Alias %r -> %s", alias, found.connector)
        result[alias] = found

    return result


# ---------------------------------------------------------------------------
# Build logical monitors for apply_config
# ---------------------------------------------------------------------------

def _build_logical_monitors(
    profile: Profile,
    alias_map: dict[str, MonitorInfo],
) -> list[dict[str, Any]]:
    """Build the logical_monitors list expected by dbus_client.apply_config()."""
    logical_monitors: list[dict[str, Any]] = []

    for layout_entry in profile.layout:
        alias = layout_entry.monitor
        monitor = alias_map.get(alias)
        if monitor is None:
            raise ConfiguratorError(
                f"Layout references unknown monitor alias {alias!r}"
            )

        # Resolve mode
        if layout_entry.mode is not None:
            mode_id = fuzzy_match_mode(layout_entry.mode, monitor)
            if mode_id is None:
                raise ConfiguratorError(
                    f"Cannot match mode {layout_entry.mode!r} on {monitor.connector}"
                )
        else:
            cm = monitor.current_mode or monitor.preferred_mode
            if cm is None:
                raise ConfiguratorError(
                    f"No current/preferred mode for {monitor.connector}"
                )
            mode_id = cm.id

        logical_monitors.append({
            "x": layout_entry.x,
            "y": layout_entry.y,
            "scale": layout_entry.scale,
            "primary": layout_entry.primary,
            "transform": layout_entry.transform,
            "monitors": [{
                "connector": monitor.connector,
                "mode": mode_id,
                "properties": {},
            }],
        })

    return logical_monitors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_profile(
    profile: Profile,
    state: DisplayState | None = None,
    method: int = 1,
) -> DisplayState:
    """
    Apply a profile's layout to the current display configuration.

    1. Re-fetches DisplayState for an atomic serial.
    2. Resolves aliases to connectors.
    3. Fuzzy-matches modes.
    4. Calls apply_config.
    5. On stale serial, retries once with a fresh state.

    Args:
        profile: The matched Profile to apply.
        state: Optional pre-fetched state (will be re-fetched for fresh serial).
        method: ApplyMonitorsConfig method (2=persistent).

    Returns:
        The DisplayState used for the successful apply call.
    """
    for attempt in range(2):
        # Always re-fetch for a fresh serial
        current_state = get_current_state()
        log.info(
            "Applying profile %r (attempt %d, serial=%d)",
            profile.name, attempt + 1, current_state.serial,
        )

        alias_map = resolve_aliases(profile, current_state)
        logical_monitors = _build_logical_monitors(profile, alias_map)

        try:
            apply_config(
                serial=current_state.serial,
                logical_monitors=logical_monitors,
                method=method,
            )
            log.info("Profile %r applied successfully", profile.name)
            return current_state
        except DBusError as e:
            err_msg = str(e)
            # Mutter raises a DBus error when the serial is stale
            if "serial" in err_msg.lower() or "configuration has changed" in err_msg.lower():
                if attempt == 0:
                    log.warning(
                        "Stale serial (%d), retrying with fresh state",
                        current_state.serial,
                    )
                    continue
                raise StaleSerialError(
                    f"Serial still stale after retry: {err_msg}"
                ) from e
            log.error("DBus call failed while applying profile %r: %s", profile.name, err_msg)
            raise ConfiguratorError(f"apply_config failed: {err_msg}") from e
        except Exception as e:
            log.error("Unexpected error applying profile %r", profile.name, exc_info=True)
            raise ConfiguratorError(f"apply_config failed: {e}") from e

    # Should not be reached, but satisfy type checker
    raise ConfiguratorError("apply_profile: unexpected loop exit")
