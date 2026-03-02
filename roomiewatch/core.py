"""
RoomieWatch — Privacy-first motion surveillance for your room.
"""

import cv2
import numpy as np
import time
import os
import argparse
import platform
import subprocess
import shutil
import threading
import signal
import socket
from datetime import datetime, timedelta
from roomiewatch import __version__

from flask import Flask, Response, render_template_string, jsonify, send_from_directory


# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_SENSITIVITY = 3.0      # % of pixels that must change to trigger
DEFAULT_COOLDOWN = 5           # seconds between captures
MOTION_THRESHOLD = 30          # per-pixel intensity difference threshold
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
WARMUP_SECONDS = 3             # ignore motion for first N seconds
JPEG_QUALITY = 70              # JPEG quality for stream (lower = less bandwidth)
STREAM_FPS = 10                # target FPS for the web stream


# ─── Helpers ─────────────────────────────────────────────────────────────────

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def file_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def log(msg, level="INFO"):
    colors = {"INFO": "\033[36m", "ALERT": "\033[91m", "OK": "\033[92m", "WARN": "\033[93m"}
    reset = "\033[0m"
    c = colors.get(level, "")
    print(f"{c}[{timestamp()}] [{level}] {msg}{reset}")

def start_caffeinate():
    """Prevent system sleep. Returns subprocess to kill on exit, or None."""
    system = platform.system()
    try:
        if system == "Darwin" and shutil.which("caffeinate"):
            proc = subprocess.Popen(["caffeinate", "-is"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("Sleep prevention active (caffeinate)", "OK")
            return proc
        elif system == "Linux" and shutil.which("systemd-inhibit"):
            proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", "--who=roomiewatch",
                 "--reason=Surveillance active", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            log("Sleep prevention active (systemd-inhibit)", "OK")
            return proc
        else:
            log("No sleep prevention available on this platform", "WARN")
    except Exception as e:
        log(f"Could not start sleep prevention: {e}", "WARN")
    return None


def beep():
    """Cross-platform alert sound."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Linux":
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            import winsound
            winsound.Beep(880, 300)
    except Exception:
        pass


# ─── Web Stream Dashboard ───────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RoomieWatch — Live Feed</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .header {
            padding: 20px;
            text-align: center;
            border-bottom: 1px solid #222;
            width: 100%;
        }
        .header h1 {
            font-size: 1.4em;
            font-weight: 600;
            letter-spacing: 2px;
            color: #ff4444;
        }
        .header .subtitle {
            font-size: 0.85em;
            color: #666;
            margin-top: 4px;
        }
        .feed-container {
            margin: 20px auto;
            max-width: 720px;
            width: 95%;
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            border: 2px solid #222;
            background: #111;
        }
        .feed-container img {
            width: 100%;
            display: block;
        }
        .live-badge {
            position: absolute;
            top: 12px;
            left: 12px;
            background: #ff0000;
            color: white;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 700;
            letter-spacing: 1px;
            animation: pulse 2s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            max-width: 720px;
            width: 95%;
            margin: 0 auto 20px;
        }
        .stat-card {
            background: #151515;
            border: 1px solid #222;
            border-radius: 10px;
            padding: 14px;
            text-align: center;
        }
        .stat-card .label {
            font-size: 0.7em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .stat-card .value {
            font-size: 1.5em;
            font-weight: 700;
            margin-top: 4px;
            color: #fff;
        }
        .stat-card .value.alert { color: #ff4444; }
        .stat-card .value.ok { color: #44ff44; }
        .recent-captures {
            max-width: 720px;
            width: 95%;
            margin: 0 auto 30px;
        }
        .recent-captures h3 {
            font-size: 0.85em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }
        .captures-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 8px;
        }
        .captures-grid img {
            width: 100%;
            border-radius: 8px;
            border: 1px solid #222;
        }
        .no-captures { color: #444; font-style: italic; font-size: 0.9em; }
    </style>
</head>
<body>
    <div class="header">
        <h1>ROOMIEWATCH</h1>
        <div class="subtitle">Privacy-First Motion Surveillance — Live Feed</div>
    </div>
    <div class="feed-container">
        <div class="live-badge">● LIVE</div>
        <img src="/video_feed" alt="Live Feed" />
    </div>
    <div class="stats">
        <div class="stat-card">
            <div class="label">Status</div>
            <div class="value ok" id="status">Active</div>
        </div>
        <div class="stat-card">
            <div class="label">Alerts</div>
            <div class="value alert" id="alerts">0</div>
        </div>
        <div class="stat-card">
            <div class="label">Uptime</div>
            <div class="value" id="uptime">0m</div>
        </div>
        <div class="stat-card">
            <div class="label">Last Motion</div>
            <div class="value" id="last-motion">None</div>
        </div>
    </div>
    <div class="recent-captures">
        <h3>Recent Captures</h3>
        <div class="captures-grid" id="captures">
            <span class="no-captures">No motion detected yet</span>
        </div>
    </div>
    <script>
        function updateStats() {
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('alerts').textContent = data.total_alerts;
                    document.getElementById('uptime').textContent = data.uptime;
                    document.getElementById('last-motion').textContent = data.last_motion || 'None';
                    if (data.recent_captures && data.recent_captures.length > 0) {
                        const grid = document.getElementById('captures');
                        grid.innerHTML = data.recent_captures.map(f =>
                            `<img src="/captures/${f}" alt="${f}" />`
                        ).join('');
                    }
                })
                .catch(() => {});
        }
        setInterval(updateStats, 3000);
        updateStats();
    </script>
</body>
</html>
"""


# ─── Motion Detector ────────────────────────────────────────────────────────

class RoomieWatch:
    def __init__(self, args):
        self.sensitivity = args.sensitivity
        self.cooldown = args.cooldown
        self.duration = args.duration
        self.camera_idx = args.camera
        self.sound = not args.no_sound
        self.save_snapshots = not args.no_snapshots
        self.enable_stream = args.stream
        self.stream_port = args.port
        self.stream_host = '0.0.0.0' if args.expose else '127.0.0.1'
        self.max_captures = args.max_captures if args.max_captures > 0 else None
        self.capture_dir = os.path.join(os.getcwd(), "roomiewatch_captures")
        self.log_file = os.path.join(self.capture_dir, "motion_log.txt")

        self.prev_gray = None
        self.last_capture_time = 0
        self.total_alerts = 0
        self.start_time = None
        self.running = True
        self.last_motion_time_str = None

        # Thread-safe frame sharing for the web stream
        self.current_frame = None
        self.frame_lock = threading.Lock()

        # Camera health tracking
        self.consecutive_failures = 0
        self.max_failures = 30  # restart camera after this many consecutive failures
        self.camera_restarts = 0

        os.makedirs(self.capture_dir, exist_ok=True)

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        log("Received shutdown signal", "INFO")
        self.running = False

    def write_log(self, msg):
        try:
            with open(self.log_file, "a") as f:
                f.write(f"[{timestamp()}] {msg}\n")
        except Exception:
            pass

    def save_snapshot(self, frame, motion_pct):
        fname = f"motion_{file_timestamp()}.jpg"
        path = os.path.join(self.capture_dir, fname)

        overlay = frame.copy()
        text = f"MOTION {motion_pct:.1f}% | {timestamp()}"
        cv2.putText(overlay, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                     0.7, (0, 0, 255), 2)
        cv2.imwrite(path, overlay)
        return fname

    def enforce_capture_limit(self):
        if self.max_captures is None:
            return
        try:
            files = sorted(
                f for f in os.listdir(self.capture_dir)
                if f.startswith("motion_") and f.endswith(".jpg")
            )
            to_delete = len(files) - self.max_captures
            if to_delete > 0:
                for f in files[:to_delete]:
                    os.remove(os.path.join(self.capture_dir, f))
                log(f"Retention: deleted {to_delete} oldest capture(s)", "INFO")
        except Exception:
            pass

    def detect_motion(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self.prev_gray is None:
            self.prev_gray = gray
            return 0.0

        delta = cv2.absdiff(self.prev_gray, gray)
        _, thresh = cv2.threshold(delta, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)

        changed_pixels = np.count_nonzero(thresh)
        total_pixels = thresh.shape[0] * thresh.shape[1]
        motion_pct = (changed_pixels / total_pixels) * 100

        self.prev_gray = gray
        return motion_pct

    def get_uptime_str(self):
        if not self.start_time:
            return "0m"
        elapsed = time.time() - self.start_time
        hours = int(elapsed // 3600)
        mins = int((elapsed % 3600) // 60)
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    def get_recent_captures(self, count=6):
        try:
            files = sorted(
                [f for f in os.listdir(self.capture_dir) if f.startswith("motion_") and f.endswith(".jpg")],
                reverse=True
            )
            return files[:count]
        except Exception:
            return []

    def open_camera(self):
        """Open camera with retries."""
        for attempt in range(3):
            log(f"Opening camera {self.camera_idx} (attempt {attempt + 1})...")
            cap = cv2.VideoCapture(self.camera_idx)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
                log("Camera opened successfully", "OK")
                return cap
            log(f"Camera open attempt {attempt + 1} failed", "WARN")
            time.sleep(2)
        return None

    def restart_camera(self, cap):
        """Release and reopen camera."""
        self.camera_restarts += 1
        log(f"Restarting camera (restart #{self.camera_restarts})...", "WARN")
        self.write_log(f"Camera restart #{self.camera_restarts}")
        try:
            cap.release()
        except Exception:
            pass
        time.sleep(2)
        new_cap = self.open_camera()
        if new_cap:
            self.consecutive_failures = 0
            self.prev_gray = None  # reset motion baseline
            log("Camera restarted successfully", "OK")
        else:
            log("Camera restart FAILED", "WARN")
        return new_cap

    def start_web_server(self):
        """Start Flask web server in a background thread."""
        app = Flask(__name__)
        app.logger.disabled = True

        import logging
        wlog = logging.getLogger('werkzeug')
        wlog.setLevel(logging.ERROR)

        watcher = self

        @app.route('/')
        def index():
            return render_template_string(DASHBOARD_HTML)

        @app.route('/video_feed')
        def video_feed():
            def generate():
                while watcher.running:
                    with watcher.frame_lock:
                        frame = watcher.current_frame
                    if frame is not None:
                        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                        frame_bytes = buffer.tobytes()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    time.sleep(1.0 / STREAM_FPS)
            return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

        @app.route('/api/stats')
        def stats():
            return jsonify({
                'total_alerts': watcher.total_alerts,
                'uptime': watcher.get_uptime_str(),
                'last_motion': watcher.last_motion_time_str,
                'recent_captures': watcher.get_recent_captures(),
                'camera_restarts': watcher.camera_restarts,
                'running': watcher.running
            })

        @app.route('/captures/<filename>')
        def serve_capture(filename):
            return send_from_directory(watcher.capture_dir, filename)

        # Check if port is available before starting
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_sock.bind((self.stream_host, self.stream_port))
            test_sock.close()
        except OSError:
            log(f"PORT {self.stream_port} IS ALREADY IN USE!", "WARN")
            log(f"On macOS, port 5000 is used by AirPlay. Try: --port 8080", "WARN")
            log(f"Or check what's using it: lsof -i :{self.stream_port}", "WARN")
            log("Continuing without web stream...", "WARN")
            return

        server_thread = threading.Thread(
            target=lambda: app.run(host=self.stream_host, port=self.stream_port, threaded=True),
            daemon=True
        )
        server_thread.start()
        log(f"Web stream started on http://localhost:{self.stream_port}", "OK")
        if self.stream_host == '0.0.0.0':
            log(f"Exposed on all interfaces — remote access via Tailscale: tailscale ip -4", "OK")
        else:
            log(f"Localhost only. Use --expose to allow remote/Tailscale access.", "INFO")

    def run(self):
        cap = self.open_camera()

        if not cap:
            log("FAILED to open camera after 3 attempts.", "WARN")
            log("Tip: On macOS, grant Terminal camera access in System Preferences > Privacy.", "WARN")
            return

        self.start_time = time.time()
        self.write_log("=== RoomieWatch surveillance STARTED ===")

        log(f"Sensitivity: {self.sensitivity}% | Cooldown: {self.cooldown}s", "INFO")
        if self.save_snapshots:
            log(f"Captures saved to: {self.capture_dir}", "INFO")
        log(f"Warming up for {WARMUP_SECONDS}s...", "INFO")

        if self.duration:
            end_time = datetime.now() + timedelta(minutes=self.duration)
            log(f"Auto-stop at: {end_time.strftime('%H:%M:%S')}", "INFO")

        # Start web stream if requested
        if self.enable_stream:
            self.start_web_server()

        log("━" * 50)
        log("YOU CAN NOW LOCK YOUR SCREEN — surveillance is active", "OK")
        log("━" * 50)
        log("Press Ctrl+C to stop\n")

        try:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    self.consecutive_failures += 1
                    if self.consecutive_failures >= self.max_failures:
                        cap = self.restart_camera(cap)
                        if not cap:
                            log("Could not restart camera. Waiting 30s before retry...", "WARN")
                            time.sleep(30)
                            cap = self.open_camera()
                            if not cap:
                                log("Camera permanently unavailable. Exiting.", "WARN")
                                break
                    else:
                        time.sleep(0.5)
                    continue

                self.consecutive_failures = 0

                # Share frame with web stream
                if self.enable_stream:
                    with self.frame_lock:
                        self.current_frame = frame.copy()

                # Check duration limit
                elapsed = time.time() - self.start_time
                if self.duration and elapsed > self.duration * 60:
                    log(f"Duration limit reached ({self.duration} min). Stopping.", "INFO")
                    break

                # Skip warmup period
                if elapsed < WARMUP_SECONDS:
                    self.detect_motion(frame)
                    continue

                motion_pct = self.detect_motion(frame)
                now = time.time()

                if motion_pct > self.sensitivity and (now - self.last_capture_time) > self.cooldown:
                    self.total_alerts += 1
                    self.last_capture_time = now
                    self.last_motion_time_str = datetime.now().strftime("%H:%M:%S")

                    if self.save_snapshots:
                        fname = self.save_snapshot(frame, motion_pct)
                        self.enforce_capture_limit()
                        log(f"MOTION DETECTED — {motion_pct:.1f}% change -> saved {fname}", "ALERT")
                        self.write_log(f"MOTION {motion_pct:.1f}% -> {fname}")
                    else:
                        log(f"MOTION DETECTED — {motion_pct:.1f}% change", "ALERT")
                        self.write_log(f"MOTION {motion_pct:.1f}%")

                    if self.sound:
                        threading.Thread(target=beep, daemon=True).start()

                # ~15 fps — plenty for surveillance, easy on resources
                time.sleep(0.066)

        except Exception as e:
            log(f"Unexpected error: {e}", "WARN")
            self.write_log(f"ERROR: {e}")
        finally:
            try:
                cap.release()
            except Exception:
                pass
            run_time = time.time() - self.start_time
            mins = int(run_time // 60)
            secs = int(run_time % 60)

            self.write_log(f"=== RoomieWatch STOPPED | {self.total_alerts} alerts in {mins}m {secs}s | {self.camera_restarts} camera restarts ===\n")

            log("━" * 50)
            log("RoomieWatch surveillance ENDED", "INFO")
            log(f"Duration: {mins}m {secs}s", "INFO")
            log(f"Total motion alerts: {self.total_alerts}", "INFO")
            log(f"Camera restarts: {self.camera_restarts}", "INFO")
            if self.save_snapshots:
                log(f"Captures: {self.capture_dir}", "INFO")
            log(f"Log file: {self.log_file}", "INFO")
            log("━" * 50)


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    banner = f"""
    ╔═══════════════════════════════════════════════════════╗
    ║  ROOMIEWATCH v{__version__}                                   ║
    ║  Privacy-first motion surveillance for your room      ║
    ║  E2E encrypted remote viewing via Tailscale           ║
    ╚═══════════════════════════════════════════════════════╝
    """
    print(banner)

    parser = argparse.ArgumentParser(
        prog="roomiewatch",
        description="Privacy-first motion surveillance that runs on your laptop.",
    )
    parser.add_argument("--version", action="version", version=f"roomiewatch {__version__}")
    parser.add_argument("--stream", action="store_true",
                        help="Enable live web stream for remote viewing")
    parser.add_argument("--port", type=int, default=8080,
                        help="Web stream port (default: 8080)")
    parser.add_argument("--expose", action="store_true",
                        help="Bind to all interfaces (0.0.0.0) instead of localhost only")
    parser.add_argument("--sensitivity", type=float, default=DEFAULT_SENSITIVITY,
                        help=f"Motion threshold %% (default: {DEFAULT_SENSITIVITY}, higher=less sensitive)")
    parser.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN,
                        help=f"Seconds between captures (default: {DEFAULT_COOLDOWN})")
    parser.add_argument("--duration", type=int, default=None,
                        help="Auto-stop after N minutes (default: unlimited)")
    parser.add_argument("--camera", type=int, default=0,
                        help="Camera index (default: 0)")
    parser.add_argument("--no-sound", action="store_true",
                        help="Disable alert sound")
    parser.add_argument("--no-snapshots", action="store_true",
                        help="Stream only, don't save snapshots to disk")
    parser.add_argument("--max-captures", type=int, default=1000,
                        help="Max snapshots to keep; oldest auto-deleted (default: 1000, 0=unlimited)")
    parser.add_argument("--caffeinate", action="store_true",
                        help="Prevent system sleep (macOS: caffeinate, Linux: systemd-inhibit)")
    args = parser.parse_args()

    caff_proc = None
    if args.caffeinate:
        caff_proc = start_caffeinate()

    try:
        watcher = RoomieWatch(args)
        watcher.run()
    finally:
        if caff_proc:
            caff_proc.terminate()
            caff_proc.wait()
            log("Sleep prevention stopped", "INFO")


if __name__ == "__main__":
    main()
