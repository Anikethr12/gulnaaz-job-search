#!/usr/bin/env python3
"""Gulnaaz Job Search — daily LinkedIn job finder with a local dashboard.

Runs one role+city search every `interval_minutes` (default 60), rotating through
the queue. Results are stored in data/jobs.json and shown at
http://127.0.0.1:<port>/
"""

import html
import json
import os
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STORE_PATH = os.path.join(BASE_DIR, "data", "jobs.json")

KEEP_DAYS = 30  # drop jobs not seen in a search for this long


# ────────────────────────────── helpers ──────────────────────────────
def now():
    return datetime.now(timezone.utc).astimezone()


def stamp(moment=None):
    return (moment or now()).isoformat(timespec="seconds")


def pretty(value):
    """ISO timestamp -> '24 Nov 2026, 20:07' (local time)."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return str(value)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        config = json.load(fh)
    config.setdefault("roles", [])
    config.setdefault("cities", [])
    config.setdefault("pages_per_search", 2)
    config.setdefault("interval_minutes", 60)
    config.setdefault("request_gap_seconds", 30)
    config.setdefault("port", 8765)
    config.setdefault("max_jobs_shown", 300)
    return config


# ────────────────────────────── storage ──────────────────────────────
class Store:
    """Tiny JSON-backed store; safe for concurrent reads."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.state = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
                    return data
            except (OSError, ValueError):
                pass
        return {
            "updated_at": None,
            "jobs": {},
            "last_run": None,
            "runs": [],
            "cursor": 0,
            "schedule": {},
        }

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def record(self, role, city, found, errors, next_cursor):
        """Persist one role+city search, its timestamp, and the queue cursor."""
        moment = now()
        seen_at = stamp(moment)
        cutoff = stamp(moment - timedelta(days=KEEP_DAYS))

        with self.lock:
            jobs = self.state.setdefault("jobs", {})

            for job in found:
                existing = jobs.get(job["id"])
                if existing:
                    for key in ("title", "company", "location", "url", "posted", "keyword", "city"):
                        if job.get(key):
                            existing[key] = job[key]
                    existing["last_seen"] = seen_at
                else:
                    fresh = dict(job)
                    fresh["first_seen"] = seen_at
                    fresh["last_seen"] = seen_at
                    jobs[job["id"]] = fresh

            for job_id in [jid for jid, job in jobs.items() if (job.get("last_seen") or "") < cutoff]:
                jobs.pop(job_id, None)

            schedule = self.state.setdefault("schedule", {})
            schedule[f"{role}||{city}"] = {
                "role": role,
                "city": city,
                "last_searched": seen_at,
                "jobs_found": len(found),
                "errors": errors[:5],
            }

            self.state["cursor"] = next_cursor
            run = {
                "at": seen_at,
                "role": role,
                "city": city,
                "found": len(found),
                "tracked": len(jobs),
                "errors": errors[:10],
            }
            self.state["updated_at"] = seen_at
            self.state["last_run"] = run
            runs = self.state.setdefault("runs", [])
            runs.insert(0, run)
            del runs[20:]
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)


# ────────────────────────────── searcher ──────────────────────────────
class Searcher:
    """Searches one role+city pair per run, advancing a persistent cursor."""

    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.pairs = [(role, city) for role in config["roles"] for city in config["cities"]]
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self):
        with self._lock:
            return self._running

    def next_pair(self):
        cursor = int(self.store.snapshot().get("cursor", 0))
        if not self.pairs:
            return None, cursor
        return self.pairs[cursor % len(self.pairs)], cursor

    def run_next(self, trigger="scheduled"):
        with self._lock:
            if self._running:
                return False
            self._running = True

        cursor = 0
        role, city = "?", "?"
        try:
            pair, cursor = self.next_pair()
            if pair is None:
                print("No roles/cities configured - edit config.json")
                return False

            role, city = pair
            print(f"[{stamp()}] ({trigger}) {role} @ {city}")
            jobs, errors = scraper.search(
                role, city, pages=int(self.config["pages_per_search"])
            )
            self.store.record(role, city, jobs, errors, cursor + 1)
            print(f"  {len(jobs)} jobs, {len(errors)} error(s)")
        except Exception:
            errors = [traceback.format_exc(limit=3)]
            print("search failed:\n" + errors[-1])
            self.store.record(role, city, [], errors, cursor + 1)
        finally:
            with self._lock:
                self._running = False
        return True


def scheduler_loop(searcher, stop_event):
    minutes = max(1.0, float(searcher.config["interval_minutes"]))
    while not stop_event.is_set():
        searcher.run_next(trigger="scheduled")
        if stop_event.wait(minutes * 60):
            break


# ────────────────────────────── dashboard ─────────────────────────────
CSS = """
:root {
  color-scheme: light;
  --rose: #e8608f;
  --rose-deep: #c93f74;
  --lav: #a98bf0;
  --ink: #4a3b45;
  --muted: #a08293;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  color: var(--ink);
  font-family: ui-rounded, "SF Pro Rounded", "Nunito", "Quicksand", -apple-system,
               BlinkMacSystemFont, "Segoe UI", sans-serif;
  background:
    radial-gradient(900px 420px at 6% -12%, #ffe1ec 0%, transparent 62%),
    radial-gradient(820px 400px at 96% -4%, #e9e1ff 0%, transparent 58%),
    linear-gradient(165deg, #fff8fb 0%, #fdf2ff 46%, #f5f7ff 100%);
  background-attachment: fixed;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 42px 20px 70px; }
.hero { margin-bottom: 6px; }
h1 {
  margin: 0 0 8px;
  font-size: 34px;
  font-weight: 800;
  letter-spacing: -0.02em;
  background: linear-gradient(92deg, #ff7fae 0%, #d96be0 55%, #9c7bf0 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.bloom { -webkit-text-fill-color: initial; margin-right: 6px; }
.sub { color: var(--muted); font-size: 14px; line-height: 1.5; }
.note {
  display: inline-block;
  margin-top: 14px;
  padding: 10px 20px;
  border-radius: 999px;
  background: linear-gradient(135deg, #fff0f6, #f4ecff);
  border: 1px solid #ffd9e8;
  color: #b3306a;
  font-size: 13.5px;
  font-style: italic;
  letter-spacing: .01em;
  box-shadow: 0 6px 18px rgba(232, 96, 143, .10);
}
.stats { display: flex; flex-wrap: wrap; gap: 12px; margin: 24px 0; }
.stat {
  background: #fff;
  border: 1px solid #ffe0ec;
  border-radius: 18px;
  padding: 12px 16px;
  min-width: 132px;
  box-shadow: 0 8px 24px rgba(232, 96, 143, .08);
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .09em;
  color: var(--muted);
}
.stat:nth-child(3n + 2) { border-color: #e8ddff; }
.stat:nth-child(3n) { border-color: #ffeccc; }
.stat b {
  display: block;
  margin-top: 5px;
  font-size: 14.5px;
  font-weight: 700;
  text-transform: none;
  letter-spacing: 0;
  color: var(--ink);
}
button {
  border: 0;
  border-radius: 999px;
  padding: 12px 24px;
  font: inherit;
  font-size: 14px;
  font-weight: 800;
  color: #fff;
  cursor: pointer;
  background: linear-gradient(135deg, #ff8bb5 0%, #e06ad4 60%, #a07bf0 100%);
  box-shadow: 0 10px 24px rgba(224, 106, 212, .28);
  transition: transform .15s ease, box-shadow .15s ease;
}
button:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 14px 30px rgba(224, 106, 212, .34); }
button:active:not(:disabled) { transform: translateY(0); }
button:disabled { opacity: .55; cursor: default; box-shadow: none; }
.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 22px 0 18px; }
input[type=search], select {
  font: inherit;
  font-size: 13.5px;
  color: var(--ink);
  background: #fff;
  border: 1px solid #f0d9e6;
  border-radius: 999px;
  padding: 11px 18px;
  outline: none;
  box-shadow: 0 4px 14px rgba(232, 96, 143, .06);
  transition: border-color .15s, box-shadow .15s;
}
input[type=search]:focus, select:focus {
  border-color: #f3a7c6;
  box-shadow: 0 0 0 4px rgba(243, 167, 198, .22);
}
input[type=search] { flex: 1 1 260px; }
input::placeholder { color: #c4a9b7; }
.table-wrap {
  background: #fff;
  border: 1px solid #ffe1ec;
  border-radius: 22px;
  overflow: auto;
  box-shadow: 0 16px 44px rgba(201, 63, 116, .07);
}
table { width: 100%; min-width: 640px; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 13px 16px; vertical-align: top; }
th {
  background: linear-gradient(180deg, #fff4f9, #ffeaf3);
  color: #b06b8a;
  font-size: 10.5px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .1em;
  border-bottom: 1px solid #ffe1ec;
  white-space: nowrap;
}
td { border-bottom: 1px solid #fdf1f6; }
tbody tr:hover td { background: #fffafc; }
tbody tr:last-child td { border-bottom: 0; }
td a { color: var(--rose-deep); text-decoration: none; font-weight: 700; }
td a:hover { text-decoration: underline; text-decoration-color: #f6b7cf; text-underline-offset: 3px; }
.muted { color: var(--muted); }
.new {
  display: inline-block;
  margin-left: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: linear-gradient(135deg, #ffd3e4, #ffc0d8);
  color: #b32d63;
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: .08em;
}
.next {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  background: linear-gradient(135deg, #eadfff, #dccdff);
  color: #6a4bb5;
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: .08em;
}
.banner { border-radius: 16px; padding: 14px 16px; margin: 0 0 16px; font-size: 13px; line-height: 1.5; }
.banner.warn { background: #fff7e8; border: 1px solid #ffe2b8; color: #9a6414; }
.banner.info { background: #f4efff; border: 1px solid #e2d8ff; color: #6a4bb5; }
.empty {
  background: #fff;
  border: 1.5px dashed #ffd3e4;
  border-radius: 22px;
  padding: 46px 20px;
  text-align: center;
  color: var(--muted);
}
h2.section {
  font-size: 15px;
  font-weight: 800;
  color: #7b5c6d;
  margin: 32px 0 12px;
  letter-spacing: .01em;
}
footer { margin-top: 28px; text-align: center; color: #c0a7b5; font-size: 12px; }
"""


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def render_page(state, config, running):
    jobs = list(state.get("jobs", {}).values())
    jobs.sort(key=lambda job: job.get("first_seen") or "", reverse=True)

    shown = jobs[: int(config.get("max_jobs_shown", 300))]
    new_cutoff = stamp(now() - timedelta(hours=24))

    rows = []
    for job in shown:
        badge = '<span class="new">NEW</span>' if (job.get("first_seen") or "") >= new_cutoff else ""
        rows.append(
            '<tr data-role="{role}" data-city="{city}">'
            '<td><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>{badge}</td>'
            "<td>{company}</td>"
            '<td class="muted">{location}</td>'
            '<td class="muted">{posted}</td>'
            '<td class="muted">{keyword}</td>'
            '<td class="muted">{found}</td>'
            "</tr>".format(
                role=_escape(job.get("keyword")),
                city=_escape(job.get("city")),
                url=_escape(job.get("url")),
                title=_escape(job.get("title")),
                badge=badge,
                company=_escape(job.get("company")) or '<span class="muted">&mdash;</span>',
                location=_escape(job.get("location")),
                posted=_escape(job.get("posted") or "—"),
                keyword=_escape(job.get("keyword")),
                found=_escape(pretty(job.get("first_seen"))),
            )
        )

    role_options = "".join(
        f'<option value="{_escape(role)}">{_escape(role)}</option>' for role in config["roles"]
    )
    city_options = "".join(
        f'<option value="{_escape(city)}">{_escape(city)}</option>' for city in config["cities"]
    )

    last_run = state.get("last_run") or {}
    updated = state.get("updated_at") or "never"
    errors = last_run.get("errors") or []

    interval_min = int(config.get("interval_minutes", 60))
    pairs = [(role, city) for role in config["roles"] for city in config["cities"]]
    cursor = int(state.get("cursor", 0))
    upcoming = pairs[cursor % len(pairs)] if pairs else None

    next_at = ""
    if last_run.get("at"):
        try:
            next_at = stamp(
                datetime.fromisoformat(last_run["at"]) + timedelta(minutes=interval_min)
            )
        except ValueError:
            next_at = ""

    schedule = state.get("schedule") or {}
    schedule_rows = []
    for role, city in pairs:
        entry = schedule.get(f"{role}||{city}") or {}
        is_next = (role, city) == upcoming
        schedule_rows.append(
            '<tr><td>{role}</td><td>{city}</td><td class="muted">{when}</td>'
            '<td class="muted">{found}</td><td>{badge}</td></tr>'.format(
                role=_escape(role),
                city=_escape(city),
                when=_escape(pretty(entry.get("last_searched"))),
                found=_escape(entry.get("jobs_found", "—")),
                badge='<span class="next">next</span>' if is_next else "",
            )
        )
    schedule_table = (
        "<table><thead><tr><th>Role</th><th>City</th><th>Last searched</th>"
        "<th>Found</th><th></th></tr></thead><tbody>"
        + "".join(schedule_rows)
        + "</tbody></table>"
    )

    banners = ""
    if running:
        banners += '<div class="banner info">Search is running — this page will show results once it finishes.</div>'
    if errors:
        banners += (
            '<div class="banner warn"><b>{n} search problem(s) on the last run.</b><br>{first}</div>'.format(
                n=len(errors), first=_escape(errors[0])[:400]
            )
        )

    if rows:
        table = (
            "<table><thead><tr><th>Job</th><th>Company</th><th>Location</th>"
            "<th>Posted</th><th>Search</th><th>Found</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        table = (
            '<div class="empty">No jobs yet. The first search is running — refresh in a minute.</div>'
        )

    table_block = f'<div class="table-wrap">{table}</div>' if rows else table

    # Pre-computed for the template (keeps the f-string valid on Python 3.10+).
    role_count = len(config["roles"])
    city_count = len(config["cities"])
    last_at_text = _escape(pretty(last_run.get("at")))
    last_role_text = _escape(last_run.get("role") or "—")
    last_city_text = _escape(last_run.get("city") or "—")
    status_text = "searching…" if running else "idle"
    button_disabled = "disabled" if running else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gulnaaz Job Search</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1><span class="bloom">🌸</span>Gulnaaz Job Search</h1>
    <div class="sub">LinkedIn &middot; {role_count} roles &times; {city_count} cities
      &middot; one search every {interval_min} min, rotating role by role</div>
    <div class="note">Best wishes from my side to Gulnaaz 💌</div>
  </div>

  <div class="stats">
    <div class="stat">Last search <b>{last_at_text}</b></div>
    <div class="stat">Next search <b>{_escape(pretty(next_at))}</b></div>
    <div class="stat">Last pair <b>{last_role_text} @ {last_city_text}</b></div>
    <div class="stat">Jobs tracked <b>{len(jobs)}</b></div>
    <div class="stat">Cycle <b>{len(pairs)} searches &times; {interval_min} min</b></div>
    <div class="stat">Status <b>{status_text}</b></div>
  </div>

  {banners}

  <form method="post" action="/refresh">
    <button type="submit" {button_disabled}>Search next now</button>
  </form>

  <div class="toolbar">
    <input type="search" id="q" placeholder="Filter by job title or company…">
    <select id="role"><option value="">All roles</option>{role_options}</select>
    <select id="city"><option value="">All cities</option>{city_options}</select>
  </div>

  <h2 class="section">Jobs ✨</h2>
  {table_block}

  <h2 class="section">Search schedule 🕐</h2>
  <div class="table-wrap">{schedule_table}</div>

  <footer>Source: LinkedIn public job listings &middot; titles and links only, no applications sent &middot; made with &#9825;</footer>
</div>
<script>
const q = document.getElementById('q');
const role = document.getElementById('role');
const city = document.getElementById('city');
const rows = Array.from(document.querySelectorAll('tbody tr'));
function applyFilters() {{
  const term = q.value.trim().toLowerCase();
  rows.forEach(function (row) {{
    const matchesText = !term || row.innerText.toLowerCase().includes(term);
    const matchesRole = !role.value || row.dataset.role === role.value;
    const matchesCity = !city.value || row.dataset.city === city.value;
    row.style.display = matchesText && matchesRole && matchesCity ? '' : 'none';
  }});
}}
[q, role, city].forEach(function (el) {{ el.addEventListener('input', applyFilters); }});
</script>
</body>
</html>"""


# ────────────────────────────── server ───────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "GulnaazJobSearch/1.0"

    def _send(self, body, status=200, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            state = self.server.store.snapshot()
            self._send(render_page(state, self.server.config, self.server.searcher.running))
        elif path == "/api/jobs":
            self._send(
                json.dumps(self.server.store.snapshot(), indent=2, ensure_ascii=False),
                content_type="application/json; charset=utf-8",
            )
        elif path == "/health":
            self._send("ok", content_type="text/plain; charset=utf-8")
        else:
            self._send("Not found", status=404, content_type="text/plain; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/refresh":
            threading.Thread(
                target=self.server.searcher.run_next,
                kwargs={"trigger": "manual"},
                daemon=True,
            ).start()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self._send("Not found", status=404, content_type="text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # keep the console readable


def main():
    config = load_config()
    store = Store(STORE_PATH)
    scraper.configure(config.get("request_gap_seconds", 45))

    port = int(config["port"])
    url = f"http://127.0.0.1:{port}"

    # Bind first: if the port is taken, the app is probably already running.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        print("")
        print(f"  Port {port} is already in use.")
        print(f"  If Gulnaaz Job Search is already running, just open: {url}")
        print('  Otherwise change "port" in config.json, or close the other program.')
        raise SystemExit(1)

    pid_path = os.path.join(BASE_DIR, "data", "app.pid")
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)
    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    searcher = Searcher(config, store)
    stop_event = threading.Event()
    threading.Thread(target=scheduler_loop, args=(searcher, stop_event), daemon=True).start()

    server.store = store
    server.searcher = searcher
    server.config = config

    print("=" * 62)
    print("  Gulnaaz Job Search")
    print(f"  Dashboard : {url}")
    print(
        f"  Roles     : {len(config['roles'])} | Cities: {len(config['cities'])} "
        f"| Every {config['interval_minutes']} min"
    )
    print("  Press Ctrl+C to stop.")
    print("=" * 62)

    # Open the dashboard in the default browser unless --no-browser is passed.
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_event.set()
        server.server_close()
        try:
            os.remove(pid_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()



