<p align="center">
  <img src="data/icons/hicolor/scalable/apps/com.github.monlay.svg" width="96" alt="Monlay">
</p>

<h1 align="center">Monlay</h1>

<p align="center">
  <strong>Your monitors. Your layout. Every time.</strong><br>
  Automatic monitor profile manager for GNOME Wayland.
</p>

<p align="center">
  <a href="#the-problem">Why?</a> &bull;
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#gui">GUI</a> &bull;
  <a href="#cli">CLI</a> &bull;
  <a href="#configuration">Config</a>
</p>

---

## The Problem

You plug in your monitors. GNOME doesn't recognize the setup. You open Display Settings, rearrange everything, set the right scale, pick the primary monitor. Again. Every. Single. Time.

This happens because GNOME matches saved layouts by **port name** (`DP-7`, `DP-10`, ...) and those names change on every plug cycle -- especially with NVIDIA GPUs and Thunderbolt/USB-C docks. Your perfectly arranged triple-monitor layout? Gone.

**Existing tools don't help:**

| Tool | Why it doesn't work |
|------|-------------------|
| autorandr | X11 only. Dead on Wayland. |
| kanshi | Sway/wlroots only. Not for GNOME. |
| way-displays | Same -- wlroots only. |
| gdctl (GNOME 48+) | Still matches by port name. Same problem. |

## The Solution

Monlay identifies your monitors by **hardware identity** (EDID: vendor + product code), not port names. It doesn't matter if your Samsung shows up as `DP-7` today and `DP-13` tomorrow -- Monlay knows it's your Samsung.

**How it works:**

1. A lightweight daemon listens for GNOME's `MonitorsChanged` signal
2. Monitors are identified by EDID -- constant across ports and reboots
3. The right layout profile is matched and applied instantly via Mutter's DBus API
4. Post-config actions run: dock moves to the right screen, wallpaper refreshes

Plug in. Walk away. Done.

## Quick Start

```bash
git clone https://github.com/lucasliwinski23/monlay.git
cd monlay
./install.sh
```

Then set up your profiles:

```bash
# Arrange your monitors in GNOME Settings the way you want them.
# Then save that layout:
monlay save-profile home-desk

# Undock. Arrange laptop-only if needed. Save again:
monlay save-profile laptop-only

# Enable automatic switching:
systemctl --user enable --now monlay
```

That's it. Monlay will now switch layouts automatically when you dock/undock.

## GUI

Launch `monlay-gui` or find "Monlay" in your app drawer.

The GUI lets you:
- See all your saved profiles
- Visually arrange monitors with drag-and-drop
- Set scale, primary, refresh rate per monitor
- Toggle automatic switching on/off
- Apply profiles with one click

The **Automatic** toggle in the header bar controls the background service -- turn it on and Monlay handles everything silently.

## CLI

```
monlay status            Show what's connected right now
monlay save-profile NAME Save current layout as a profile
monlay apply [NAME]      Apply a profile (auto-detect if no name given)
monlay list-profiles     List profiles, shows which one is active
monlay daemon            Run in foreground (for debugging)
monlay version           Print version
```

**Flags:** `--verbose` for debug output, `--config PATH` for custom config location.

### Example: `monlay status`

```
Connected monitors: 3
Layout mode: logical

  DP-11  Iiyama North America 26"
    Vendor: IVM  Product: PL7512U
    Mode: 1920x1080@60.000
    Position: (0, 0)  Scale: 1.0

  DP-12  Samsung Electric Company 49" [PRIMARY]
    Vendor: SAM  Product: LS49C95xU
    Mode: 5120x1440@120.000
    Position: (1920, 0)  Scale: 1.0

  eDP-1  Built-in Display
    Vendor: AUO  Product: 0x87a8
    Mode: 3840x2400@120.001
    Position: (7040, 0)  Scale: 2.0
```

## Configuration

Profiles live in `~/.config/monlay/config.yaml`. Create them with `monlay save-profile` or edit by hand.

```yaml
settings:
  settle_delay_ms: 1500    # Wait for dock to finish initializing
  log_level: INFO

profiles:
  - name: home-desk
    description: "Samsung ultrawide + laptop"
    monitors:
      samsung:
        vendor: "SAM"
        product: "LS49A950U"
      laptop:
        vendor: "AUO"
        product: "0x87a8"
    layout:
      - monitor: laptop
        x: 0
        y: 0
        scale: 2.0
        primary: false
        mode: "3840x2400@120"
      - monitor: samsung
        x: 3840
        y: 0
        scale: 1.0
        primary: true
        mode: "5120x1440@120"
    post_config:
      - type: dock_monitor
        monitor: samsung
      - type: dock_icon_size
        value: 48
      - type: wallpaper_refresh
```

Find your monitor's vendor/product with `monlay status`.

### Post-Config Actions

Actions that run after the layout is applied:

| Action | What it does |
|--------|-------------|
| `dock_monitor` | Moves Dash to Dock / Ubuntu Dock to the specified monitor |
| `dock_icon_size` | Sets dock icon size (useful for HiDPI switching) |
| `wallpaper_refresh` | Re-applies wallpaper (fixes GNOME's black wallpaper bug) |
| `command` | Runs any shell command |

Dock actions are automatically skipped on systems without Dash to Dock -- no errors, no config changes needed.

## How Monlay Compares

| Feature | Monlay | autorandr | kanshi | gdctl |
|---------|--------|-----------|--------|-------|
| GNOME Wayland | Yes | No | No | Yes |
| EDID matching | Yes | Partial | Yes | No |
| Survives port changes | Yes | N/A | N/A | No |
| Auto-apply on hotplug | Yes | Yes | Yes | No |
| GUI | Yes | No | No | No |
| Mixed DPI | Yes | N/A | Yes | Yes |
| Dock migration | Yes | No | No | No |

## Requirements

- GNOME 46+ on Wayland
- Python 3.10+
- PyGObject (`python3-gi`)
- PyYAML (`python3-yaml`)
- libadwaita (`gir1.2-adw-1`) for the GUI
- systemd (for the background service)

Works on Ubuntu 24.04+, Fedora 40+, Arch with GNOME.

## Troubleshooting

**Monlay doesn't detect my monitors:**
Run `monlay --verbose status` and check the debug output. Make sure you're on a Wayland session (`echo $XDG_SESSION_TYPE`).

**Layout isn't applied on plug-in:**
Check the daemon logs: `journalctl --user -u monlay -f`. The settle delay might be too short for your dock -- increase `settle_delay_ms` in the config.

**Stale serial error in logs:**
Normal -- happens when GNOME is still reconfiguring. Monlay retries automatically.

**Dock doesn't move:**
Only works with Dash to Dock, Ubuntu Dock, or Cosmic Dock. Check `monlay --verbose apply` for "dock not available" messages.

## License

MIT -- see [LICENSE](LICENSE).

## Author

**Luca Sliwinski** -- Built because rearranging monitors every day is not a productive use of anyone's time.
