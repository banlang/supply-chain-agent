"""
take_screenshots.py — one full-height PNG per persona tab, 1600 px wide.

Starts Streamlit headless, drives headless Chrome (or Edge) over the DevTools
protocol, opens app.py?tab=planner|exec|ops, sizes the viewport to the page
content and saves docs/screenshots/<tab>.png. Read-only: clicks nothing,
writes no logs.

planner_queue.png is rendered by a second app process whose SCA_DECISIONS_LOG
points at a new empty temporary file (deleted afterwards), so it shows the
current run's recommendations before any review.

Run from the project root:  python scripts/take_screenshots.py
Needs: Chrome or Edge installed, websocket-client (pip install websocket-client).
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

ROOT     = Path(__file__).resolve().parent.parent
OUT_DIR  = ROOT / "docs" / "screenshots"
APP_PORT = 8599
QUEUE_PORT = 8598   # second app process for planner_queue.png
CDP_PORT = 9333
WIDTH    = 1600
TABS     = ["planner", "exec", "ops"]
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "msedge",
]


def _find_browser() -> str:
    for b in BROWSERS:
        if Path(b).exists() or shutil.which(b):
            return b
    sys.exit("Chrome or Edge not found — edit BROWSERS in scripts/take_screenshots.py")


def _wait_http(url: str, timeout: float = 60) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return urllib.request.urlopen(url, timeout=2).read().decode()
        except OSError:
            time.sleep(0.5)
    sys.exit(f"Timed out waiting for {url}")


class Cdp:
    def __init__(self, ws_url: str):
        self.ws, self.n = websocket.create_connection(ws_url, timeout=60), 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True)["result"].get("value")


def _shoot(cdp: Cdp, tab: str, port: int = APP_PORT, name: str | None = None) -> Path:
    cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=1000, deviceScaleFactor=1, mobile=False)
    cdp.call("Page.navigate", url=f"http://localhost:{port}/?tab={tab}")
    # Wait until the script has finished running and the banner is on screen
    deadline = time.time() + 60
    while time.time() < deadline:
        ready = cdp.js(
            "document.body.innerText.includes('Prototype on public Kaggle data') && "
            "!document.querySelector('[data-testid=\"stStatusWidget\"]')"
        )
        if ready:
            break
        time.sleep(0.5)
    time.sleep(2)  # let fonts and dataframes settle
    height = cdp.js(
        "Math.max(...['[data-testid=\"stMain\"]', '[data-testid=\"stAppViewContainer\"]', 'section.main']"
        ".map(s => document.querySelector(s)).filter(Boolean).map(e => e.scrollHeight), "
        "document.body.scrollHeight)"
    )
    cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=int(height) + 40,
             deviceScaleFactor=1, mobile=False)
    time.sleep(1.5)
    png = cdp.call("Page.captureScreenshot", format="png")["data"]
    path = OUT_DIR / f"{name or tab}.png"
    path.write_bytes(base64.b64decode(png))
    return path


def _start_app(port: int, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
         "--server.port", str(port), "--browser.gatherUsageStats", "false",
         "--theme.base", "light"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    empty_dir = tempfile.mkdtemp(prefix="sc-empty-log-")
    empty_log = Path(empty_dir) / "decisions_log.jsonl"
    empty_log.touch()
    app = _start_app(APP_PORT)
    queue_app = _start_app(QUEUE_PORT, {**os.environ, "SCA_DECISIONS_LOG": str(empty_log)})
    profile = tempfile.mkdtemp(prefix="sc-shots-")
    browser = subprocess.Popen(
        [_find_browser(), "--headless=new", f"--remote-debugging-port={CDP_PORT}",
         "--remote-allow-origins=*", f"--user-data-dir={profile}", "--hide-scrollbars",
         "--no-first-run", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_http(f"http://localhost:{APP_PORT}/_stcore/health")
        pages = json.loads(_wait_http(f"http://127.0.0.1:{CDP_PORT}/json"))
        page = next(p for p in pages if p.get("type") == "page")
        cdp = Cdp(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        for tab in TABS:
            print(f"Saved {_shoot(cdp, tab).relative_to(ROOT)}")
        _wait_http(f"http://localhost:{QUEUE_PORT}/_stcore/health")
        print(f"Saved {_shoot(cdp, 'planner', QUEUE_PORT, 'planner_queue').relative_to(ROOT)}")
        if empty_log.stat().st_size:
            sys.exit("Temporary decisions log was written to — planner_queue.png is not a pre-review view")
    finally:
        browser.terminate()
        app.terminate()
        queue_app.terminate()
        for p in (app, queue_app, browser):
            p.wait(timeout=30)
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(empty_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
