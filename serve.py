#!/usr/bin/env python3
"""Local control panel for the fare tracker.

Serves the dashboard (index.html) plus a small API the page uses to manage
the tracker when running on your machine:

    GET  /api/status   what the page uses to detect the panel + git state
    POST /api/config   save watches / alert target into config.json
    POST /api/run      run tracker.py once, return its output
    POST /api/sync     commit config + data and push to GitHub

Binds to 127.0.0.1 only. Start it with:
    .venv/Scripts/python serve.py        (default port 8000)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HISTORY_REL = "data/history.csv"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
MAX_DATES = 16


def run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)


def git_status() -> dict:
    inside = run_git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return {"repo": False}
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    remote = run_git("remote", "get-url", "origin")
    dirty = run_git("status", "--porcelain", "config.json", "data").stdout.strip()
    return {
        "repo": True,
        "branch": branch or "main",
        "has_remote": remote.returncode == 0,
        "remote_url": remote.stdout.strip() if remote.returncode == 0 else None,
        "unsynced": bool(dirty),
    }


def validate_watches(watches) -> str | None:
    """Returns an error message, or None if valid."""
    if not isinstance(watches, list) or not watches:
        return "watches must be a non-empty list"
    total = 0
    today = date.today().isoformat()
    for w in watches:
        if not isinstance(w, dict):
            return "each watch must be an object"
        frm, to = w.get("from", ""), w.get("to", "")
        if not (isinstance(frm, str) and AIRPORT_RE.match(frm)):
            return f"bad origin airport code: {frm!r}"
        if not (isinstance(to, str) and AIRPORT_RE.match(to)):
            return f"bad destination airport code: {to!r}"
        dates = w.get("dates")
        if not isinstance(dates, list):
            return f"{frm}-{to}: dates must be a list"
        for d in dates:
            if not (isinstance(d, str) and DATE_RE.match(d)):
                return f"{frm}-{to}: bad date {d!r} (use YYYY-MM-DD)"
            if d <= today:
                return f"{frm}-{to}: {d} is not in the future"
            total += 1
    if total == 0:
        return "no dates to watch — add at least one"
    if total > MAX_DATES:
        return f"too many dates ({total}); keep it to {MAX_DATES} or fewer"
    return None


def save_config(payload: dict) -> tuple[bool, str]:
    err = validate_watches(payload.get("watches"))
    if err:
        return False, err
    target = payload.get("price_target_usd")
    if not isinstance(target, int) or not (1 <= target <= 10000):
        return False, "price target must be a whole number of dollars"

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["watches"] = [
        {"from": w["from"], "to": w["to"], "dates": sorted(w["dates"])}
        for w in payload["watches"] if w["dates"]
    ]
    cfg.setdefault("alert", {})
    cfg["alert"].pop("blue_target_usd", None)
    cfg["alert"].pop("blue_upcharge_estimate_usd", None)
    cfg["alert"]["price_target_usd"] = target
    with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    return True, "saved"


def run_tracker() -> tuple[bool, str]:
    try:
        proc = subprocess.run([sys.executable, str(ROOT / "tracker.py")],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=480)
    except subprocess.TimeoutExpired:
        return False, "tracker timed out after 8 minutes"
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


def merge_history(remote_csv: str) -> None:
    """Union of remote and local history lines, remote order first."""
    local_path = ROOT / HISTORY_REL
    local_lines = (local_path.read_text(encoding="utf-8").splitlines()
                   if local_path.exists() else [])
    remote_lines = remote_csv.splitlines()
    seen = set(remote_lines)
    merged = remote_lines + [
        ln for i, ln in enumerate(local_lines)
        if ln not in seen and not (i == 0 and ln.startswith("run_ts_utc"))
    ]
    local_path.write_text("\n".join(merged) + "\n", encoding="utf-8")


def sync() -> tuple[bool, str]:
    log: list[str] = []
    st = git_status()
    if not st.get("repo"):
        return False, "this folder is not a git repository"
    branch = st["branch"]

    if st["has_remote"]:
        fetch = run_git("fetch", "origin")
        if fetch.returncode != 0:
            return False, f"git fetch failed:\n{fetch.stderr.strip()}"
        log.append("fetched origin")
        show = run_git("show", f"origin/{branch}:{HISTORY_REL}")
        if show.returncode == 0:
            merge_history(show.stdout)
            log.append("merged GitHub's price history with local checks")

    run_git("add", "config.json", "data")
    staged = run_git("diff", "--cached", "--quiet")
    if staged.returncode != 0:
        commit = run_git("commit", "-m", "control panel: update watches/data")
        if commit.returncode != 0:
            return False, f"git commit failed:\n{commit.stderr.strip()}"
        log.append("committed local changes")
    else:
        log.append("nothing new to commit")

    if not st["has_remote"]:
        log.append("no GitHub remote yet — finish the README setup "
                   "(gh repo create ... --push), then sync again")
        return True, "\n".join(log)

    rebase = run_git("rebase", "-X", "theirs", f"origin/{branch}")
    if rebase.returncode != 0:
        run_git("rebase", "--abort")
        return False, ("automatic merge with GitHub failed — run git status "
                       "in the project folder to resolve manually")
    push = run_git("push", "-u", "origin", branch)
    if push.returncode != 0:
        return False, f"git push failed:\n{push.stderr.strip()}"
    log.append("pushed to GitHub — scheduled checks now use your changes")
    return True, "\n".join(log)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("localhost", "127.0.0.1", "[::1]")

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # the dashboard must always see fresh data/config
        if self.path.split("?")[0] in ("/", "/index.html", "/config.json") \
                or self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            if not self._host_ok():
                return self._send_json(403, {"ok": False})
            return self._send_json(200, {"ok": True, "panel": True,
                                         "git": git_status()})
        return super().do_GET()

    def do_POST(self):
        if not self._host_ok():
            return self._send_json(403, {"ok": False, "error": "bad host"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send_json(400, {"ok": False, "error": "bad JSON"})

        if self.path == "/api/config":
            ok, msg = save_config(payload)
            return self._send_json(200 if ok else 400,
                                   {"ok": ok, "error": None if ok else msg})
        if self.path == "/api/run":
            ok, out = run_tracker()
            return self._send_json(200, {"ok": ok, "output": out})
        if self.path == "/api/sync":
            ok, out = sync()
            return self._send_json(200, {"ok": ok, "output": out})
        return self._send_json(404, {"ok": False, "error": "unknown endpoint"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Fare tracker control panel: http://localhost:{PORT}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
