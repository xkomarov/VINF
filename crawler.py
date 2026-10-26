import os
import re
import csv
import gzip
import time
import logging
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


SITE = "https://www.formula1.com"
FIRST_SEASON, LAST_SEASON = 1950, int(time.strftime("%Y"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_FILE = os.path.join(BASE_DIR, "crawl_history.csv")
USER_AGENT = "Mozilla/5.0 (Student Project; email: xkomarov@stuba.sk)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
WORKERS = 16       
RETRIES = 3
PAUSE = 60         
NEEDED = re.compile(r"""^/en/(
      results/\d{4}/(races|drivers|team)
    | results/\d{4}/races/\d+/[\w-]+/[\w-]+(/\d+)?
    | results/\d{4}/drivers/\w+/[\w-]+
    | results/\d{4}/team/[\w-]+
    | results/\d{4}/awards/[\w-]+
    | drivers(/[\w-]+)?
    | teams(/[\w-]+)?
    | racing/\d{4}(/[\w-]+)?
)$""", re.VERBOSE)
LINK = re.compile(r'href="(?:https://www\.formula1\.com)?(/en/[^"#?]+)"')


lock = threading.Lock()
pause_lock = threading.Lock() 


def download(url: str) -> str:

    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, RETRIES + 1):
        with pause_lock:
            pass
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            return data.decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError, EOFError) as e:
            code = e.code if isinstance(e, urllib.error.HTTPError) else None
            if code in (429, 403):
                logging.warning(f"Error: {code}, waiting {PAUSE}")
                with pause_lock:
                    time.sleep(PAUSE)
            elif code is not None and code < 500:
                raise   
            if attempt == RETRIES:
                raise
            logging.warning(f"try {attempt}/{RETRIES} error: {url} {e}")
            time.sleep(PAUSE)


def write_file(filename: str, text: str) -> int:

    with open(filename + ".tmp", "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(filename + ".tmp", filename)
    return os.path.getsize(filename)


def save_history(history, url: str, status: str, filename: str = "", size: int = 0) -> None:

    with lock:
        history.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), status, url, filename, size])


def url_to_filename(url: str) -> str:

    path = urlparse(url).path.strip("/").removeprefix("en/")
    return os.path.join(DATA_DIR, path.replace("/", "_") + ".html")


def find_links(html: str) -> set[str]:

    paths = {m.group(1) for m in LINK.finditer(html)}
    return {SITE + p for p in paths if NEEDED.match(p)}


def seed_urls() -> list[str]:

    urls = [f"{SITE}/en/results/{year}/{part}"
            for year in range(FIRST_SEASON, LAST_SEASON + 1) for part in ("races", "drivers", "team")]
    
    for loc in re.findall(r"<loc>\s*([^<\s]+)", download("https://static.formula1.com/sitemap/pages-sitemap.xml")):
        
        if loc.startswith(SITE) and NEEDED.match(urlparse(loc).path.rstrip("/")):
            urls.append(loc.rstrip("/"))

    return list(dict.fromkeys(urls))


def crawl_page(url: str, history, progress: dict) -> set[str]:
    filename = url_to_filename(url)
    if os.path.exists(filename):
        with open(filename, encoding="utf-8") as f:
            html = f.read()
    else:
        try:
            html = download(url)
        except Exception as e:
            logging.error(f"{url} {e}")
            save_history(history, url, "error")
            return set()
        size = write_file(filename, html)
        save_history(history, url, "ok", filename, size)
        with lock:
            progress["bytes"] += size

    with lock:
        progress["done"] += 1
        done = progress["done"]
    if done % 100 == 0:
        logging.info(f"{done} sides download (seeds: {progress['total']})")
    return find_links(html)


def crawl(seeds: list[str], robots: RobotFileParser, history) -> None:

    seen = set(seeds)
    level, depth = seeds, 0
    progress = {"done": 0, "bytes": 0, "total": 0, "start": time.time()}
    while level:
        progress["total"] = len(level)
        with ThreadPoolExecutor(WORKERS) as pool:
            results = list(pool.map(lambda u: crawl_page(u, history, progress), level))
        new = set().union(*results) - seen
        level = sorted(u for u in new if robots.can_fetch(USER_AGENT, u))
        seen.update(level)
        depth += 1
    logging.info(f"All pages: {progress['done']}, {progress['bytes'] / 2**20:.0f} MB")


def main() -> None:
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(DATA_DIR, exist_ok=True)

    robots = RobotFileParser()
    robots.parse(download(f"{SITE}/robots.txt").splitlines())

    seeds = [u for u in seed_urls() if robots.can_fetch(USER_AGENT, u)]
    logging.info(f"Crawler started (seeds: {seeds})")
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8", buffering=1) as f:
        history = csv.writer(f)
        if f.tell() == 0:
            history.writerow(["time", "status", "url", "file", "size_bytes"])
        crawl(seeds, robots, history)


if __name__ == "__main__":
    main()
