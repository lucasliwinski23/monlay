"""
CLI interface for monlay.

Entry point: ``monlay``
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from monlay import __version__
from monlay.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    save_current_as_profile,
)
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

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG_ERROR = 2


def _load_config_or_exit(config_path: Path) -> Config:
    try:
        return load_config(config_path)
    except FileNotFoundError:
        print(f"Config file not found: {config_path}", file=sys.stderr)
        print(f"Create one with: monlay save-profile <name>", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    except Exception as e:
        log.debug("Config load traceback:", exc_info=True)
        print(f"Failed to load config: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Show connected monitors."""
    try:
        state = get_current_state()
    except DBusError as e:
        print(f"Cannot query display state: {e}", file=sys.stderr)
        return EXIT_ERROR

    log.debug("Got display state: serial=%d, %d monitors", state.serial, len(state.monitors))

    print(f"Connected monitors: {len(state.monitors)}")
    print(f"Layout mode: {'physical' if state.layout_mode == 1 else 'logical'}")
    print()

    for lm in state.logical_monitors:
        for mon in lm.monitors:
            cm = mon.current_mode
            mode_str = f"{cm.width}x{cm.height}@{cm.refresh_rate:.3f}" if cm else "disabled"
            primary = " [PRIMARY]" if lm.primary else ""
            name = mon.display_name or f"{mon.vendor} {mon.product}"

            print(f"  {mon.connector}  {name}{primary}")
            print(f"    Vendor: {mon.vendor}  Product: {mon.product}  Serial: {mon.serial}")
            print(f"    Mode: {mode_str}")
            print(f"    Position: ({lm.x}, {lm.y})  Scale: {lm.scale}  Transform: {lm.transform}")
            if mon.is_builtin:
                print(f"    Built-in: yes")
            print()

    return EXIT_OK


def cmd_list_profiles(args: argparse.Namespace) -> int:
    """List profiles from config, mark which matches current state."""
    config = _load_config_or_exit(Path(args.config))

    if not config.profiles:
        print("No profiles defined.")
        return EXIT_OK

    # Get current state for matching
    try:
        state = get_current_state()
        connected = {
            MonitorIdentity(vendor=m.vendor, product=m.product)
            for m in state.monitors
        }
    except DBusError as e:
        log.warning("Could not query current monitors (running without display?): %s", e)
        connected = set()
    except Exception:
        log.debug("Failed to get current state for matching", exc_info=True)
        connected = set()

    for profile in config.profiles:
        is_match = profile.identity_set == frozenset(connected) if connected else False
        marker = " <-- active" if is_match else ""
        print(f"  {profile.name}{marker}")
        if profile.description:
            print(f"    {profile.description}")
        for alias, pmon in profile.monitors.items():
            print(f"    {alias}: {pmon.vendor}/{pmon.product}")
        for lay in profile.layout:
            mode_str = f" mode={lay.mode}" if lay.mode else ""
            pri_str = " PRIMARY" if lay.primary else ""
            print(
                f"    -> {lay.monitor}: ({lay.x},{lay.y}) "
                f"scale={lay.scale}{mode_str}{pri_str}"
            )
        print()

    return EXIT_OK


def cmd_save_profile(args: argparse.Namespace) -> int:
    """Snapshot current layout as a new profile."""
    try:
        state = get_current_state()
    except DBusError as e:
        print(f"Cannot query display state: {e}", file=sys.stderr)
        return EXIT_ERROR

    config_path = Path(args.config)

    try:
        save_current_as_profile(args.name, state, config_path=config_path)
    except Exception as e:
        log.debug("save_current_as_profile traceback:", exc_info=True)
        print(f"Failed to save profile: {e}", file=sys.stderr)
        return EXIT_ERROR

    print(f"Profile '{args.name}' saved to {config_path}")

    # Show what was saved
    print(f"  Monitors:")
    for lm in state.logical_monitors:
        for mon in lm.monitors:
            cm = mon.current_mode
            mode_str = f"{cm.width}x{cm.height}@{cm.refresh_rate:.0f}" if cm else "?"
            pri = " PRIMARY" if lm.primary else ""
            print(
                f"    {mon.connector} ({mon.vendor}/{mon.product}): "
                f"({lm.x},{lm.y}) scale={lm.scale} {mode_str}{pri}"
            )

    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply a profile (auto-detect if no name given)."""
    config = _load_config_or_exit(Path(args.config))

    if args.name:
        # Find profile by name
        profile = None
        for p in config.profiles:
            if p.name == args.name:
                profile = p
                break
        if profile is None:
            print(f"Profile '{args.name}' not found.", file=sys.stderr)
            print(f"Available: {', '.join(p.name for p in config.profiles)}", file=sys.stderr)
            return EXIT_ERROR
    else:
        # Auto-detect
        try:
            state = get_current_state()
        except DBusError as e:
            print(f"Cannot query display state: {e}", file=sys.stderr)
            return EXIT_ERROR

        connected = {
            MonitorIdentity(vendor=m.vendor, product=m.product)
            for m in state.monitors
        }
        profile = config.match_profile(connected)
        if profile is None:
            print("No matching profile found for current monitors.", file=sys.stderr)
            return EXIT_ERROR

    print(f"Applying profile: {profile.name}")
    try:
        final_state = apply_profile(profile)
    except ConfiguratorError as e:
        print(f"Failed: {e}", file=sys.stderr)
        return EXIT_ERROR

    print("Layout applied successfully.")

    # Run post-config
    if profile.post_config:
        print("Running post-config actions...")
        try:
            alias_map = resolve_aliases(profile, final_state)
            run_post_config(profile, alias_map)
        except Exception as e:
            log.debug("Post-config traceback:", exc_info=True)
            print(f"Post-config warning: {e}", file=sys.stderr)
        print("Post-config complete.")

    return EXIT_OK


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run daemon in foreground."""
    from monlay.daemon import main as daemon_main

    try:
        daemon_main(config_path=args.config)
    except DBusError as e:
        print(f"Daemon failed to connect to DBus: {e}", file=sys.stderr)
        print("Is a GNOME Wayland session running?", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        log.debug("Daemon traceback:", exc_info=True)
        print(f"Daemon error: {e}", file=sys.stderr)
        return EXIT_ERROR

    return EXIT_OK


def cmd_version(args: argparse.Namespace) -> int:
    """Print version."""
    print(f"monlay {__version__}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="monlay",
        description="Automatic monitor profile manager for GNOME Wayland",
    )
    parser.add_argument(
        "-c", "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="FILE",
        help="Also write debug logs to FILE",
    )

    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="Show connected monitors")

    # list-profiles
    subparsers.add_parser("list-profiles", help="List configured profiles")

    # save-profile
    sp_save = subparsers.add_parser("save-profile", help="Snapshot current layout as profile")
    sp_save.add_argument("name", help="Profile name")

    # apply
    sp_apply = subparsers.add_parser("apply", help="Apply a profile")
    sp_apply.add_argument("name", nargs="?", help="Profile name (auto-detect if omitted)")

    # daemon
    subparsers.add_parser("daemon", help="Run daemon in foreground")

    # version
    subparsers.add_parser("version", help="Print version")

    args = parser.parse_args()

    # Set up logging via centralized config
    level = "DEBUG" if args.verbose else "WARNING"
    setup_logging(level=level, log_file=args.log_file)

    commands = {
        "status": cmd_status,
        "list-profiles": cmd_list_profiles,
        "save-profile": cmd_save_profile,
        "apply": cmd_apply,
        "daemon": cmd_daemon,
        "version": cmd_version,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(EXIT_ERROR)

    try:
        exit_code = commands[args.command](args)
    except KeyboardInterrupt:
        log.debug("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.debug("Unhandled exception:", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
