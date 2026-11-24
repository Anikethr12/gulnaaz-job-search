"""
LinkedIn job search via the public guest endpoint (no login required).

Rate-limit strategy: persisted cookie jar, full browser headers, a minimum gap
between every request, Retry-After honouring and exponential backoff.

Returns plain dicts: id, title, company, location, url, posted, keyword, city.
"""

import gzip
import html
import http.cookiejar
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOBS_PAGE = "https://www.linkedin.com/jobs/"
JOB_URL = "https://www.linkedin.com/jobs/view/{job_id}/"
COOKIE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cookies.txt")

# Cookies survive restarts, so LinkedIn sees a returning visitor instead of a
# brand-new client every hour.
COOKIES = http.cookiejar.LWPCookieJar(COOKIE_PATH)
try:
    COOKIES.load(ignore_discard=True, ignore_expires=True)
except (OSError, http.cookiejar.LoadError):
    pass

OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIES))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Minimum seconds between any two requests to linkedin.com; set via configure().
MIN_GAP = 30.0
_last_request = 0.0
_gap_lock = threading.Lock()
_warmed = False


def configure(min_gap_seconds=30.0):
    """Set the minimum gap between requests (seconds)."""
    global MIN_GAP
    MIN_GAP = max(5.0, float(min_gap_seconds))


def _throttle():
    """Block until at least MIN_GAP seconds have passed since the last request."""
    global _last_request
    with _gap_lock:
        wait = _last_request + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()


def _headers(referer=JOBS_PAGE):
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _decode(response):
    raw = response.read()
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


def _save_cookies():
    try:
        os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
        COOKIES.save(ignore_discard=True, ignore_expires=True)
    except OSError:
        pass


def warm_up():
    """Visit the public jobs page once so we carry the cookies a visitor would."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    _throttle()
    try:
        request = urllib.request.Request(
            JOBS_PAGE, headers=_headers(referer="https://www.linkedin.com/")
        )
        with OPENER.open(request, timeout=25) as response:
            _decode(response)
    except Exception:
        pass
    _save_cookies()


def _fetch(keyword, location, start, timeout=25):
    query = urllib.parse.urlencode(
        {"keywords": keyword, "location": location, "start": start}
    )
    _throttle()
    request = urllib.request.Request(f"{SEARCH_URL}?{query}", headers=_headers())
    with OPENER.open(request, timeout=timeout) as response:
        body = _decode(response)
    _save_cookies()
    return body


def _retry_wait(exc, attempt, base=45.0):
    """Sleep long enough for LinkedIn's throttle to clear."""
    retry_after = ""
    if exc.headers is not None:
        retry_after = (exc.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return min(float(retry_after), 600.0)
    return min(base * (2 ** attempt), 600.0) + random.uniform(0, 15)


def _fetch_with_retry(keyword, location, start, retries=3, timeout=25):
    """Fetch one page, honouring Retry-After and backing off exponentially on 429/999."""
    for attempt in range(retries + 1):
        try:
            return _fetch(keyword, location, start, timeout=timeout), None
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 999):
                if attempt < retries:
                    wait = _retry_wait(exc, attempt)
                    print(
                        f"  [throttled] {keyword} / {location}: HTTP {exc.code}, "
                        f"waiting {wait:.0f}s then retrying",
                        flush=True,
                    )
                    time.sleep(wait)
                    # Be gentler from now on: widen the global gap (max 5 min).
                    configure(min(MIN_GAP * 2, 300))
                    continue
                return (
                    None,
                    f"{keyword} / {location}: throttled by LinkedIn (HTTP {exc.code}) "
                    f"after {retries + 1} attempts",
                )
            return None, f"{keyword} / {location}: HTTP {exc.code}"
        except Exception as exc:  # timeout, DNS, reset
            if attempt < retries:
                time.sleep(min(15 * (attempt + 1), 90) + random.uniform(0, 10))
                continue
            return None, f"{keyword} / {location}: {exc}"



CARD_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
TITLE_RE = re.compile(r'base-search-card__title">\s*(.*?)\s*</h3>', re.S)
COMPANY_RE = re.compile(r'base-search-card__subtitle">\s*(.*?)\s*</h4>', re.S)
LOCATION_RE = re.compile(r'job-search-card__location">\s*(.*?)\s*</span>', re.S)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")

PAGE_SIZE = 10  # the guest endpoint returns 10 cards per request


def _text(fragment):
    """Strip tags, unescape entities, collapse whitespace."""
    return " ".join(html.unescape(TAG_RE.sub(" ", fragment)).split())


def parse_cards(body, keyword, city):
    jobs = []
    for chunk in body.split("<li>")[1:]:
        urn = CARD_RE.search(chunk)
        if not urn:
            continue

        job_id = urn.group(1)
        title_match = TITLE_RE.search(chunk)
        company_match = COMPANY_RE.search(chunk)
        location_match = LOCATION_RE.search(chunk)
        time_match = TIME_RE.search(chunk)

        title = _text(title_match.group(1)) if title_match else ""
        if not title:
            continue

        jobs.append(
            {
                "id": job_id,
                "title": title,
                "company": _text(company_match.group(1)) if company_match else "",
                "location": _text(location_match.group(1)) if location_match else city,
                "url": JOB_URL.format(job_id=job_id),
                "posted": time_match.group(1) if time_match else "",
                "keyword": keyword,
                "city": city,
                "source": "linkedin",
            }
        )
    return jobs


def search(keyword, city, pages=2):
    """Search one role in one city. Returns (jobs, errors)."""
    warm_up()
    jobs, errors = [], []
    seen = set()

    for page in range(max(1, pages)):
        start = page * PAGE_SIZE
        body, error = _fetch_with_retry(keyword, city, start)
        if error:
            errors.append(error)
            break

        page_jobs = parse_cards(body, keyword, city)
        if not page_jobs:
            break

        for job in page_jobs:
            if job["id"] not in seen:
                seen.add(job["id"])
                jobs.append(job)

        # _fetch() already enforces MIN_GAP between requests

    return jobs, errors


def search_all(roles, cities, pages=2, progress=None):
    """Run every role x city combination. Returns (jobs_by_id, errors)."""
    results, errors = {}, []
    total = len(roles) * len(cities)
    done = 0

    for role in roles:
        for city in cities:
            jobs, errs = search(role, city, pages=pages)
            errors.extend(errs)
            for job in jobs:
                results[job["id"]] = job
            done += 1
            if progress:
                progress(done, total, role, city, len(jobs))
            if done < total:
                time.sleep(random.uniform(2.0, 4.0))

    return results, errors
