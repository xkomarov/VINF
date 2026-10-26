import os
import sys
import json
import logging
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import tiktoken

import extractor
import indexer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(BASE_DIR, "stats.json")
WORKERS = os.cpu_count() or 4
ENCODING = "o200k_base" 

PAGE_TYPES = {
    extractor.session_page: "session results",
    extractor.season_page: "season standings",
    extractor.award_page: "awards",
    extractor.driver_season_page: "driver season",
    extractor.team_season_page: "team season",
    extractor.event_page: "event / circuit",
    extractor.driver_page: "driver profile",
    extractor.team_page: "team profile",
}

encoder = None


def count_tokens(value: str) -> int:
    global encoder
    if encoder is None:
        encoder = tiktoken.get_encoding(ENCODING)
    return len(encoder.encode(value, disallowed_special=()))


def page_type(filename: str) -> str:
    for pattern, handler in extractor.HANDLERS:
        if pattern.match(filename):
            return PAGE_TYPES[handler]
    return "other"


def init_worker(encoding: str) -> None:
    global ENCODING
    ENCODING = encoding



def page_stats(filename: str) -> tuple[str, int, int, int]:
    """(page type, bytes, tokens of the raw HTML, tokens of the visible text)."""
    path = os.path.join(extractor.DATA_DIR, filename)
    with open(path, encoding="utf-8") as f:
        page = f.read()
    return (page_type(filename), os.path.getsize(path),
            count_tokens(page), count_tokens(extractor.text(extractor.clean(page))))


def corpus_stats(workers: int) -> dict:
    files = sorted(f for f in os.listdir(extractor.DATA_DIR) if f.endswith(".html"))
    logging.info(f"Counting tokens in {len(files)} pages ({ENCODING})")
    stats = defaultdict(lambda: {"pages": 0, "bytes": 0, "html_tokens": 0, "text_tokens": 0})
    with ProcessPoolExecutor(workers, initializer=init_worker, initargs=(ENCODING,)) as pool:
        for i, (kind, size, html_tokens, text_tokens) in enumerate(pool.map(page_stats, files, chunksize=16), 1):
            row = stats[kind]
            row["pages"] += 1
            row["bytes"] += size
            row["html_tokens"] += html_tokens
            row["text_tokens"] += text_tokens
            if i % 1000 == 0:
                logging.info(f"{i}/{len(files)} pages")
    return dict(sorted(stats.items()))



def table_stats() -> dict:
    stats = {}
    for name in sorted(f for f in os.listdir(extractor.OUT_DIR) if f.endswith(".jsonl")):
        path = os.path.join(extractor.OUT_DIR, name)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        stats[name.removesuffix(".jsonl")] = {"rows": content.count("\n"), "bytes": os.path.getsize(path),
                                              "tokens": count_tokens(content)}
    return stats



def index_stats() -> dict:
    """Index documents by type: own tokenizer (index terms) vs. tiktoken tokens of title + text."""
    if not os.path.exists(indexer.DOCS_FILE):
        return {}
    stats = defaultdict(lambda: {"documents": 0, "index_terms": 0, "tokens": 0})
    with open(indexer.DOCS_FILE, encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            content = doc["title"] + " " + doc["text"]
            row = stats[doc["type"]]
            row["documents"] += 1
            row["index_terms"] += len(indexer.tokenize(content))
            row["tokens"] += count_tokens(content)
    with open(indexer.INDEX_FILE, encoding="utf-8") as f:
        vocabulary = len(json.load(f)["postings"])
    return {"vocabulary": vocabulary, "types": dict(sorted(stats.items()))}



def mb(size: int) -> str:
    return f"{size / 2**20:.1f}"


def print_table(title: str, headers: tuple, rows: list[list]) -> None:
    rows = [[f"{v:,}" if isinstance(v, int) else str(v) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print(f"\n{title}")
    for i, row in enumerate([list(headers), *rows]):
        if i == len(rows):
            print("  " + "  ".join("-" * w for w in widths))
        print("  " + "  ".join(v.ljust(w) if j == 0 else v.rjust(w) for j, (v, w) in enumerate(zip(row, widths))))


def with_total(rows: list[list]) -> list[list]:
    total = ["TOTAL"] + [sum(r[i] for r in rows) if isinstance(rows[0][i], int) else "" for i in range(1, len(rows[0]))]
    return rows + [total]


def report(stats: dict) -> None:
    print(f"tiktoken encoding: {stats['encoding']}")
    if corpus := stats.get("corpus"):
        rows = with_total([[k, v["pages"], v["bytes"], v["html_tokens"], v["text_tokens"]] for k, v in corpus.items()])
        print_table("Crawled pages (data/)", ("Page type", "Pages", "MB", "HTML tokens", "Text tokens"),
                    [[r[0], r[1], mb(r[2]), r[3], r[4]] for r in rows])

    if tables := stats.get("tables"):
        rows = with_total([[k, v["rows"], v["bytes"], v["tokens"]] for k, v in tables.items()])
        print_table("Extracted tables (extracted/)", ("Table", "Rows", "MB", "Tokens"),
                    [[r[0], r[1], mb(r[2]), r[3]] for r in rows])

    if index := stats.get("index"):
        rows = with_total([[k, v["documents"], v["index_terms"], v["tokens"]] for k, v in index["types"].items()])
        print_table(f"Index documents (index/), vocabulary {index['vocabulary']:,} terms",
                    ("Document type", "Documents", "Index terms", "Tokens"), rows)


def main() -> None:
    global ENCODING
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Dataset statistics incl. tiktoken token counts")
    parser.add_argument("-e", "--encoding", default=ENCODING, help="tiktoken encoding (default %(default)s)")
    parser.add_argument("--skip-pages", action="store_true", help="skip the (slow) pass over raw HTML pages")
    parser.add_argument("-w", "--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    ENCODING = args.encoding

    stats = {"encoding": ENCODING}
    if not args.skip_pages:
        stats["corpus"] = corpus_stats(args.workers)
    stats["tables"] = table_stats()
    stats["index"] = index_stats()
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    report(stats)
    logging.info(f"-> {STATS_FILE}")


if __name__ == "__main__":
    main()
