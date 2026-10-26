import os
import re
import html
import json
import logging
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_DIR = os.path.join(BASE_DIR, "extracted")
WORKERS = os.cpu_count() or 4

SESSION_PAGE = re.compile(
    r"^results_(?P<season>\d{4})_races_(?P<race_id>\d+)_(?P<gp_slug>[\w-]+?)_(?P<kind>[a-z-]+)(?:_(?P<number>\d+))?\.html$")
SEASON_PAGE = re.compile(r"^results_(?P<season>\d{4})_(?P<kind>races|drivers|team)\.html$")
AWARD_PAGE = re.compile(r"^results_(?P<season>\d{4})_awards_(?P<award>[\w-]+)\.html$")
DRIVER_SEASON_PAGE = re.compile(r"^results_(?P<season>\d{4})_drivers_(?P<driver_id>\w+)_(?P<driver_slug>[\w-]+)\.html$")
TEAM_SEASON_PAGE = re.compile(r"^results_(?P<season>\d{4})_team_(?P<team_slug>[\w-]+)\.html$")
EVENT_PAGE = re.compile(r"^racing_(?P<season>\d{4})_(?P<gp_slug>[\w-]+)\.html$")
DRIVER_PAGE = re.compile(r"^drivers_(?P<driver_slug>[\w-]+)\.html$")
TEAM_PAGE = re.compile(r"^teams_(?P<team_slug>[\w-]+)\.html$")

SESSION_TABLES = {"race-result": "race_results", "pit-stop-summary": "pit_stops"}
SEASON_TABLES = {"races": "races", "drivers": "driver_standings", "team": "team_standings"}
SKIP_PAGES = {"drivers_hall-of-fame.html"}

NOISE = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<svg\b.*?</svg>|<!--.*?-->", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"\s+")
TABLE = re.compile(r"<table\b.*?</table>", re.S)
HEADER = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)
CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S)
HREF = re.compile(r'href="([^"]+)"')
DRIVER = re.compile(r"<span[^>]*>([^<]*)</span>[^<]*<span[^>]*>([^<]*)</span>\s*<span[^>]*>([A-Z0-9]{3})</span>")
H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S)
EVENT_HEADER = re.compile(r"</h1>.*?<p\b[^>]*>([^<]*)</p>\s*<p\b[^>]*>([^<]*)</p>", re.S)
STATS = re.compile(
    r"<h([23])\b[^>]*>(.*?)</h\1>"
    r"|<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>(?:\s*<span\b[^>]*>(.*?)</span>)?", re.S)
H2_SECTION = r"<h2\b[^>]*>\s*{}\s*</h2>(.*?)(?=<h2\b|$)"
PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
SCHEDULE_ITEM = re.compile(r"<li\b[^>]*data-f1rd-a7s-context=\"[^\"]*\"[^>]*>(.*?)</li>", re.S)
TIME = re.compile(r"<time\b[^>]*>([^<]*)</time>")
SESSION_NAME = re.compile(r"^(Practice \d|Qualifying|Sprint Qualifying|Sprint Shootout|Sprint|Race|Day \d|Summary)$", re.I)
RACE_LINK = re.compile(r"/results/\d{4}/races/(\d+)/([\w-]+)/")
DRIVER_LINK = re.compile(r"/results/\d{4}/drivers/(\w+)/([\w-]+)")
TEAM_LINK = re.compile(r"/results/\d{4}/team/([\w-]+)")
SEASON_HEADING = re.compile(r"^(\d{4})\s*SEASON$", re.I)
CAREER_HEADINGS = {"career stats", "team summary"}
NUMBER = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def text(fragment: str) -> str:
    return SPACES.sub(" ", html.unescape(TAG.sub(" ", fragment))).strip()


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def find_text(regex, page: str) -> str:
    m = regex.search(page)
    return text(m.group(1)) if m else ""


def section(page: str, heading: str) -> str:
  
    m = re.search(H2_SECTION.format(re.escape(heading)), page, re.S | re.I)
    return m.group(1) if m else ""



def parse_cell(column: str, cell: str) -> dict:
  
    values = {}
    driver = DRIVER.search(cell)
    href = HREF.search(cell)
    link = href.group(1) if href else ""
    if driver:
        first, last, code = (html.unescape(g).replace("\xa0", " ").strip() for g in driver.groups())
        values[column] = f"{first} {last}".strip()
        values[f"{column}_code"] = code
    else:
        values[column] = text(cell)

    driver_link = DRIVER_LINK.search(link)
    team_link = TEAM_LINK.search(link)
    race_link = RACE_LINK.search(link)
    if driver_link:
        values[f"{column}_id"], values[f"{column}_slug"] = driver_link.groups()
    elif team_link:
        values[f"{column}_slug"] = team_link.group(1)
    elif race_link:
        values["race_id"], values["gp_slug"] = race_link.groups()
    return values


def parse_table(page: str) -> list[dict]:
    table = TABLE.search(page)
    if not table:
        return []
    columns = [slug(text(h)) for h in HEADER.findall(table.group(0))]
    rows = []
    for row in ROW.findall(table.group(0)):
        cells = CELL.findall(row)
        if not cells:
            continue
        record = {}
        for column, cell in zip(columns, cells):
            if column:
                record.update(parse_cell(column, cell))
        rows.append(record)
    return rows



def parse_stats(page: str) -> dict:

    record, prefix = {}, ""
    for level, heading, label, value, note in STATS.findall(page):
        if heading:
            heading = text(heading)
            season = SEASON_HEADING.match(heading)
            if season:
                record["season"], prefix = season.group(1), "season_"
            elif heading.lower() in CAREER_HEADINGS:
                prefix = "career_"
            elif level == "2":
                prefix = ""
            continue
        key = prefix + slug(text(label))
        record[key] = text(value)
        if note and text(note):
            record[key + "_note"] = text(note)
    return record


def section_text(page: str, heading: str) -> str:
    body = re.sub(r"<dl\b.*?</dl>", "", section(page, heading), flags=re.S)
    return " ".join(t for t in (text(p) for p in PARAGRAPH.findall(body)) if t)



def event_header(page: str) -> dict:
    header = EVENT_HEADER.search(page)
    record = {"official_name": find_text(H1, page)}

    if header:
        record["dates"] = text(header.group(1))
        circuit = text(header.group(2))

        if ", " in circuit:
            name, locality = circuit.rsplit(", ", 1)
        else:
            name, locality = circuit, ""
        record["circuit"] = name
        record["locality"] = locality
    return record


def session_page(name_match, page) -> dict:
    ids = name_match.groupdict() 
    kind = ids.pop("kind")
    number = ids.pop("number")
    table = SESSION_TABLES.get(kind, kind.replace("-", "_"))
    session = kind if number is None else f"{kind}_{number}"
    rows = [{**ids, "session": session, **row} for row in parse_table(page)]

    header = event_header(page)
    header["official_name"] = re.sub(r"\s+-\s+[^-]+$", "", header["official_name"])  # drop " - RACE RESULT"
    return {table: rows, "grand_prix": [{**ids, **header}]}


def season_page(name_match, page) -> dict:
    ids = name_match.groupdict()  
    table = SEASON_TABLES[ids.pop("kind")]
    return {table: [{**ids, **row} for row in parse_table(page)]}


def award_page(name_match, page) -> dict:
    ids = name_match.groupdict()  
    return {"awards": [{**ids, **row} for row in parse_table(page)]}


def driver_season_page(name_match, page) -> dict:
    ids = name_match.groupdict()  # season, driver_id, driver_slug
    return {"driver_season_results": [{**ids, **row} for row in parse_table(page)]}


def team_season_page(name_match, page) -> dict:

    ids = name_match.groupdict()  
    return {"team_season_results": [{**ids, **row} for row in parse_table(page)]}


def parse_schedule_item(item: str) -> dict | None:

    texts = [text(s) for s in re.findall(r">([^<]+)<", item)]
    tokens = [t for t in texts if t]  
    names = [t for t in tokens if SESSION_NAME.match(t)]
    if not names or len(tokens) < 2:
        return None

    times = TIME.findall(item)
    start = times[0] if len(times) > 0 else ""
    end = times[1] if len(times) > 1 else ""
    return {"session": names[0], "day": tokens[0], "month": tokens[1], "start": start, "end": end}


def event_page(name_match, page) -> dict:

    ids = name_match.groupdict()  
    circuit = {**ids, "event_name": find_text(H1, page)}
    circuit.update(parse_stats(section(page, "Circuit")))
    circuit["about"] = section_text(page, "About")

    schedule = []
    for item in SCHEDULE_ITEM.findall(section(page, "Schedule")):
        session = parse_schedule_item(item)
        if session:
            schedule.append({**ids, **session})
    return {"circuits": [circuit], "schedule": schedule}


def driver_page(name_match, page) -> dict:
    ids = name_match.groupdict()  
    record = {**ids, "first_name": "", "last_name": "", "country": "", "team": "", "number": ""}

    title = H1.search(page)
    if title:
        # <h1><span>Max</span> <span>Verstappen</span></h1>
        names = [text(s) for s in re.findall(r"<span\b[^>]*>([^<]+)</span>", title.group(1))]
        if names:
            record["first_name"] = names[0]
            record["last_name"] = " ".join(names[1:])
        after_title = page[title.end():title.end() + 3000]
        paragraphs = [text(p) for p in PARAGRAPH.findall(after_title)]
        for field, value in zip(("country", "team", "number"), paragraphs):
            record[field] = value

    record.update(parse_stats(page))
    record["biography"] = section_text(page, "Biography")
    return {"drivers": [record]}


def team_page(name_match, page) -> dict:
    ids = name_match.groupdict()  # team_slug
    record = {**ids, "team": find_text(H1, page)}
    record["drivers"] = [f"{text(a)} {text(b)}" for a, b in re.findall(
        r"<p\b[^>]*>([^<]+)</p>\s*<p\b[^>]*>([^<]+)</p>\s*<div\b[^>]*>\s*<p\b", section(page, "DRIVERS"))]
    record.update(parse_stats(page))
    record["profile"] = section_text(page, "Team Profile")
    return {"teams": [record]}


HANDLERS = [
    (SESSION_PAGE, session_page),
    (SEASON_PAGE, season_page),
    (AWARD_PAGE, award_page),
    (DRIVER_SEASON_PAGE, driver_season_page),
    (TEAM_SEASON_PAGE, team_season_page),
    (EVENT_PAGE, event_page),
    (DRIVER_PAGE, driver_page),
    (TEAM_PAGE, team_page),
]


def extract_file(filename: str) -> dict:
    if filename in SKIP_PAGES:
        return {}
    for pattern, handler in HANDLERS:
        name_match = pattern.match(filename)
        if not name_match:
            continue
        with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
            page = NOISE.sub("", f.read())
        try:
            return handler(name_match, page)
        except Exception as e:
            logging.error(f"{filename}: {e}")
            return {}
    return {}



def typed(value):

    if isinstance(value, str) and NUMBER.match(value):
        return float(value) if "." in value else int(value)
    return value


def write_jsonl(name: str, rows: list[dict]) -> None:
   
    with open(os.path.join(OUT_DIR, name + ".jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            record = {k: typed(v) for k, v in row.items() if v not in ("", [])}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def merge_grand_prix(rows: list[dict]) -> list[dict]:
    
    races = {}
    for row in rows:
        races.setdefault((row["season"], row["race_id"]), {}).update({k: v for k, v in row.items() if v})
    return sorted(races.values(), key=lambda r: (r["season"], int(r["race_id"])))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".html"))
    logging.info(f"Extracting {len(files)} pages")

    tables = defaultdict(list)
    with ProcessPoolExecutor(WORKERS) as pool:
        for i, result in enumerate(pool.map(extract_file, files, chunksize=32), 1):
            for name, rows in result.items():
                tables[name].extend(rows)
            if i % 1000 == 0:
                logging.info(f"{i}/{len(files)} pages")

    tables["grand_prix"] = merge_grand_prix(tables["grand_prix"])
    for name, rows in sorted(tables.items()):
        write_jsonl(name, rows)
        logging.info(f"{name}.jsonl: {len(rows)} rows")


if __name__ == "__main__":
    main()
