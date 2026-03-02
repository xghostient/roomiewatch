# RoomieWatch

Privacy-first motion surveillance that runs on your laptop. Detects motion, captures snapshots, streams live — all local, no cloud.

Built because I don't trust my roommate.

## Features

- **Motion detection** — OpenCV-based frame diffing with configurable sensitivity
- **Live dashboard** — MJPEG stream + stats + recent captures in a dark-themed web UI
- **Privacy-first** — no cloud, no third-party servers, all data stays on your machine
- **Remote access** — E2E encrypted via [Tailscale](https://tailscale.com) (or self-hosted with [Headscale](https://github.com/juanfont/headscale))
- **Runs locked** — keeps working when your screen is locked (macOS `caffeinate`)
- **Auto-restart** — launcher script recovers from crashes automatically
- **Zero config** — works out of the box with your laptop webcam

## Install

```bash
pip install roomiewatch
```

Or from source:

```bash
git clone https://github.com/xghostient/roomiewatch.git
cd roomiewatch
pip install .
```

## Quick Start

```bash
# Motion detection only — captures saved to ./roomiewatch_captures/
roomiewatch

# With live web dashboard
roomiewatch --stream

# Full setup — stream + prevent sleep
roomiewatch --stream --caffeinate

# Custom settings
roomiewatch --stream --port 9090 --sensitivity 5 --cooldown 10
```

Lock your screen (`Ctrl+Cmd+Q`) and leave. **Do NOT close the lid.**

## Remote Access (Tailscale)

View your feed from anywhere on your phone — E2E encrypted, no middleman.

```bash
# On your machine
brew install tailscale
sudo brew services start tailscale
tailscale up

# Install Tailscale on your phone, sign in with the same account
# Then open: http://<tailscale-ip>:8080
tailscale ip -4  # shows your machine's Tailscale IP
```

**Why Tailscale over Cloudflare Tunnel?** Cloudflare terminates TLS at their edge — your images pass through their servers in plaintext. Tailscale is direct device-to-device WireGuard encryption. Nobody sees your footage.

For full self-hosted setup with zero third-party trust, use [Headscale](https://github.com/juanfont/headscale).

## Launcher Script

For long-running sessions with auto-restart and sleep prevention:

```bash
chmod +x start_roomiewatch.sh
./start_roomiewatch.sh             # stream + Tailscale
./start_roomiewatch.sh --no-stream # motion detection only
```

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--stream` | off | Enable live web dashboard |
| `--port` | 8080 | Web server port |
| `--sensitivity` | 3 | Motion threshold % (higher = less sensitive) |
| `--cooldown` | 5 | Seconds between captures |
| `--duration` | unlimited | Auto-stop after N minutes |
| `--camera` | 0 | Camera index |
| `--no-sound` | off | Disable alert beep |
| `--no-snapshots` | off | Stream only, don't save to disk |
| `--caffeinate` | off | Prevent system sleep (macOS + Linux) |

## How It Works

1. Captures frames from your webcam at ~15 FPS
2. Converts to grayscale, applies Gaussian blur
3. Computes absolute difference from previous frame
4. If changed pixels exceed the sensitivity threshold — saves a timestamped JPEG and logs the event
5. Flask serves a live MJPEG stream and a dashboard with stats and recent captures

## Files Created

```
roomiewatch_captures/
├── motion_20260302_143022.jpg   # snapshots with timestamp overlay
├── motion_log.txt               # text log of all motion events
└── launcher.log                 # launcher/restart log
```

## Troubleshooting

- **Camera not opening** — Grant Terminal camera access in System Settings > Privacy & Security > Camera
- **Stream not loading** — Check if port 8080 is in use: `lsof -i :8080`
- **Too many false alerts** — Increase sensitivity: `roomiewatch --stream --sensitivity 5`
- **macOS sleeping** — Don't close the lid. Use the launcher script which runs `caffeinate`

## Requirements

- Python 3.9+
- A webcam
- macOS or Linux (Windows: untested but should work)

## License

[MIT](LICENSE)
