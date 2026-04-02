# monlay

Automatic monitor profile manager for GNOME Wayland that identifies monitors by EDID instead of connector names.

## The Problem

On GNOME Wayland -- especially with NVIDIA GPUs and Thunderbolt/USB-C docks -- monitor connector names (`DP-7`, `DP-10`, etc.) change on every plug/unplug cycle. GNOME's `monitors.xml` matches saved layouts by connector name, so your carefully arranged multi-monitor layout is never found after re-docking. You are left rearranging monitors in Settings every single time.

There is no existing tool that solves this for GNOME Wayland:

| Tool | Limitation |
|------|-----------|
| **autorandr** | X11/xrandr only, does not work on Wayland |
| **kanshi** | wlroots compositors only (Sway, Hyprland, etc.) |
| **way-displays** | wlroots compositors only |
| **gdctl** | Still matches by connector name, same problem |

## How It Works

1. A lightweight daemon listens for Mutter's `MonitorsChanged` DBus signal.
2. When monitors change, it identifies each monitor by EDID data (vendor + product code) -- values that stay constant regardless of which port or connector is used.
3. It matches the connected monitor set against pre-configured profiles.
4. It applies the matching layout via Mutter's `org.gnome.Mutter.DisplayConfig` DBus API.
5. Optional post-config actions run after the layout is applied (dock migration, wallpaper refresh, custom commands).

## Features

- **EDID-based monitor detection** -- connector names are ignored entirely
- **Automatic layout application** -- daemon applies profiles on hotplug events
- **Profile system** -- save and name layouts for different monitor combinations
- **Dock migration** -- move Ubuntu Dock / Dash to Dock to the correct monitor
- **Wallpaper refresh** -- workaround for the GNOME black wallpaper bug after hotplug
- **Fuzzy mode matching** -- refresh rates are matched within 1 Hz tolerance
- **Debounce** -- configurable settle delay to avoid spurious re-applies during dock plug-in
- **CLI tool** -- inspect monitors, save profiles, apply layouts manually

## Quick Start

```bash
# Clone and install
git clone https://github.com/luca-sliwinski/monlay.git
cd monlay
./install.sh

# Arrange monitors in GNOME Settings, then save the layout
monlay save-profile my-desk

# Repeat for each monitor combination (e.g., laptop-only, dual, triple)
# Then enable the daemon
systemctl --user enable --now monlay
```

## CLI Usage

```
monlay status              Show connected monitors (vendor, product, serial, mode, position)
monlay save-profile NAME   Snapshot the current layout as a named profile
monlay apply [NAME]        Apply a profile (auto-detects if name is omitted)
monlay list-profiles       List all profiles, mark which matches current monitors
monlay daemon              Run the daemon in the foreground (for debugging)
monlay version             Print version
```

Options available on all subcommands:

```
-c, --config PATH       Use a custom config file path
-v, --verbose           Enable debug logging
```

## Configuration

Profiles are stored in `~/.config/monlay/config.yaml`. You can create profiles with `monlay save-profile` or edit the file directly.

```yaml
settings:
  settle_delay_ms: 1500
  log_level: INFO

profiles:
  - name: triple-office
    description: "Iiyama left, Samsung center, laptop right"
    monitors:
      iiyama:
        vendor: "IVM"
        product: "PL7512U"
      samsung:
        vendor: "SAM"
        product: "LS49A950U"
      laptop:
        vendor: "AUO"
        product: "0x87a8"
    layout:
      - monitor: iiyama
        x: 0
        y: 0
        scale: 1.0
        primary: false
        mode: "1920x1080@60"
      - monitor: samsung
        x: 1920
        y: 0
        scale: 1.0
        primary: true
        mode: "5120x1440@120"
      - monitor: laptop
        x: 7040
        y: 0
        scale: 2.0
        primary: false
        mode: "3840x2400@120"
    post_config:
      - type: dock_monitor
        monitor: samsung
      - type: wallpaper_refresh
```

Use `monlay status` to find the vendor and product values for your monitors.

### Post-Config Actions

| Action | Description |
|--------|-------------|
| `dock_monitor` | Move Ubuntu Dock to a specific monitor |
| `dock_icon_size` | Set dock icon size |
| `wallpaper_refresh` | Re-apply wallpaper URIs (fixes GNOME black wallpaper bug) |
| `command` | Run an arbitrary shell command |

## Requirements

- Python 3.10+
- GNOME 46+ on Wayland
- PyGObject (`python3-gi`)
- PyYAML (`python3-yaml`)
- systemd (for the user service)

## License

MIT -- see [LICENSE](LICENSE).
