# Gulnaaz Job Search

A small LinkedIn job finder. It searches the roles and cities in `config.json`,
stores what it finds, and shows them on a local page with the job title, company,
location, posted date and the LinkedIn link.

No login, no auto-apply, no AI — it only reads LinkedIn's public job listings.

## Requirements

- Python **3.10 or newer** (3.11 / 3.12 / 3.13 / 3.14 all fine).
- Standard library only — **no `pip install`, no virtualenv, no external packages**.
- macOS, Linux and Windows are all supported.

## Run

Open a terminal in this folder and run one command.

macOS / Linux:

```bash
./run.sh
```

Windows (cmd or PowerShell):

```bat
python app.py
```

If `python` isn't recognised, use the launcher:

```bat
py app.py
```

The dashboard opens in your browser automatically at
**http://127.0.0.1:8765**. Press **Ctrl+C** in the terminal to stop it.

- Add `--no-browser` if you don't want the tab to open by itself.
- `run.bat` (Windows) and `run.sh` (macOS/Linux) are just convenience wrappers
  that also stop a previous instance first — not required.
- If the port is already in use, the app says so and tells you to just open
  the dashboard, instead of crashing.

### Windows quick start (copy-paste)

**1. Install Python (once)**

- Go to https://www.python.org/downloads/ and download Python **3.10 or newer**
- ✅ On the first installer screen, tick **“Add python.exe to PATH”**
- Click Install

Or install it entirely from the terminal (Windows 10/11 ships with `winget`):

```bat
winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
```

Any 3.10+ works — `Python.Python.3.12` / `Python.Python.3.14` are also available.
Close and reopen the terminal afterwards so PATH refreshes.

**2. Check it works** (open cmd or PowerShell)

```bat
python --version
```

If that opens the Microsoft Store, use this instead: `py --version`

**3. Get the code** — no Git required

- Open https://github.com/Anikethr12/gulnaaz-job-search
- Click the green **Code** button → **Download ZIP**
- Right-click the downloaded `gulnaaz-job-search-main.zip` → **Extract All** → **Extract**
- It extracts to `C:\Users\<you>\Downloads\gulnaaz-job-search-main`

(If you *do* have Git, `git clone https://github.com/Anikethr12/gulnaaz-job-search.git`
works the same way.)

**4. Start it**

The easy way to open a terminal in the right folder: open that folder in File
Explorer, click the address bar, type `cmd`, press Enter. Then:

```bat
python app.py
```

Or from anywhere:

```bat
cd %USERPROFILE%\Downloads\gulnaaz-job-search-main
python app.py
```

PowerShell uses `cd $env:USERPROFILE\Downloads\gulnaaz-job-search-main` instead.

The dashboard opens by itself at **http://127.0.0.1:8765**.
Press **Ctrl+C** in the terminal to stop.

There is **no `pip install`** step and **nothing else to install** — the bot only
uses Python's standard library.

**5. Change the search (optional)**

Open `config.json` in Notepad, edit `roles` / `cities`, save, then run
`python app.py` again.

A search starts immediately, then **one role+city search runs every hour**,
rotating through the queue: e.g. `HR Executive @ Bengaluru`, then 1 hour later
`HR Executive @ Davanagere`, then the next role, and so on. With 18 roles ×
2 cities that is a **36-hour full cycle** at 60 min per pair.
**Search next now** runs the next queue item immediately.

Pure Python standard library — nothing to install.

## Change the roles / cities

Edit `config.json`:

```json
{
  "roles": ["HR Executive", "HR Generalist", "HRBP"],
  "cities": ["Bengaluru, Karnataka, India", "Davanagere, Karnataka, India"],
  "pages_per_search": 2,
  "interval_minutes": 60,
  "request_gap_seconds": 45,
  "port": 8765,
  "max_jobs_shown": 300
}
```

- `roles` — job titles to search (LinkedIn keyword search).
- `cities` — LinkedIn location strings. Spelling matters: LinkedIn resolves
  **`Davanagere, Karnataka, India`** to the Davangere area; `Davangere, …` falls
  back to state-wide results. Use `"Remote"` for remote roles.
- `pages_per_search` — 10 results per page; 2 ≈ 20 results per role/city.
- `interval_minutes` — gap between individual searches (60 = one search per hour).
- `request_gap_seconds` — minimum gap between any two LinkedIn requests
  (default 45). Automatically doubles on a 429, up to 300 s.
- `port` — change if 8765 is taken.

Restart the app after editing.

## Start automatically at login (optional)

macOS:

```bash
./install_autostart.sh          # install LaunchAgent
./install_autostart.sh remove   # uninstall
```

Windows:

```bat
install_autostart.bat           :: Task Scheduler entry, runs at logon
install_autostart.bat remove    :: uninstall
```

This keeps the dashboard running in the background; logs go to `data/gulnaaz.log`
on macOS and to the console window on Windows.

## Share it on GitHub

This is a **local app**: GitHub stores the code, and each person runs the bot on
their own computer. Nothing is deployed, no server is needed, and every person
gets their own dashboard at `http://127.0.0.1:8765`. Job results and LinkedIn
cookies never leave the machine — `data/` is git-ignored.

### Publishing it (you, once)

```bash
cd gulnaaz-job-search
git init
git add .
git commit -m "Gulnaaz Job Search"
git branch -M main
git remote add origin https://github.com/<your-username>/gulnaaz-job-search.git
git push -u origin main
```

(Or create the repo on github.com and use “upload files” — just don't upload
`data/`.)

### What the other person does

1. Open the repo → **Code ▸ Download ZIP** (or `git clone <repo-url>`)
2. In a terminal inside the folder:
   - **Windows:** `python app.py` (or `py app.py`)
   - **macOS / Linux:** `python3 app.py` (or `./run.sh`)
3. The dashboard opens by itself at **http://127.0.0.1:8765**

That's the whole setup — Python 3.10+ is the only requirement, no `pip install`,
no `run.bat` needed (it's just a double-click convenience wrapper).

### Changing the search for themselves

They edit `config.json` (`roles`, `cities`, `interval_minutes`) and restart.
Their results are stored locally in `data/jobs.json`.

### Not recommended: hosting it as a public website

It can technically run on a VPS/Render/Fly.io, but then:

- it binds `127.0.0.1` by default — you would have to change that to `0.0.0.0`,
  which exposes the dashboard publicly with **no authentication**;
- LinkedIn sees requests from a datacenter IP, which is throttled far more
  aggressively than a home connection;
- everyone would share one job list and one cookie jar.

For sharing, the ZIP/clone route above is the better fit.

## Files

```
app.py                 server, scheduler, page rendering
scraper.py             LinkedIn search + HTML parsing
config.json            roles, cities, port, interval, request gap
data/jobs.json         saved results (created on first run)
data/cookies.txt       persisted LinkedIn cookies (reduces 429s)
data/archive/          old result sets you archived
run.sh                 start script (macOS / Linux)
run.bat                start script (Windows)
install_autostart.sh   optional macOS login autostart
install_autostart.bat  optional Windows logon autostart
```

## Notes

- **Rate limits:** every request is spaced by `request_gap_seconds` (default 30),
  cookies are persisted in `data/cookies.txt`, and the request carries normal
  browser headers. If LinkedIn still answers 429/999 the bot honours
  `Retry-After`, retries up to 3 times with exponential backoff, and doubles the
  gap for the rest of the process. With one pair per hour that is ~2 requests an
  hour, well under the throttle threshold.
- Results are de-duplicated by LinkedIn job id, and entries not seen for 30 days
  are pruned automatically.
- Job cards show **NEW** when first seen in the last 24 hours.
- LinkedIn rate-limits the public endpoint sometimes (HTTP 429). The page will
  show a warning banner; the next scheduled run picks up again.
- Only titles/links are collected — no applications are submitted and no
  LinkedIn account is used.
