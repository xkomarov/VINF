import os
import re
import sys
import json
import math
import logging
import argparse
import unicodedata
from collections import Counter, defaultdict


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(BASE_DIR, "extracted")
INDEX_DIR = os.path.join(BASE_DIR, "index")
DOCS_FILE = os.path.join(INDEX_DIR, "documents.jsonl")
INDEX_FILE = os.path.join(INDEX_DIR, "index.json")

K1 = 1.5
B = 0.75

TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = set("""
a about after all also an and are as at be been before but by can did do for from had has have he her his
how if in into is it its more not of on or out over she so than that the their them then there they this
to up was we were what when where which while who will with would you
""".split())

SLUG_ALIASES = {"united-arab-emirates": "abu-dhabi", "abudhabil": "abu-dhabi", "emiliaromagna": "emilia-romagna"}
SEASON_SLUG_ALIASES = {(2020, "germany"): "eifel"}
CIRCUIT_STATS = ("circuit_length", "number_of_laps", "race_distance", "first_grand_prix",
                 "fastest_lap_time", "fastest_lap_time_note")

TITLE_BOOST = 3

SKIP_FIELDS = re.compile(r"_(id|slug|code)$|^(race_id|no|pos|pts|laps|time.*|q\d|lap|stops|number)$")



def normalize(value: str) -> str:
    """Lowercase and strip diacritics: 'Räikkönen' -> 'raikkonen'."""
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def tokenize(value: str) -> list[str]:
    return [t for t in TOKEN.findall(normalize(value)) if t not in STOPWORDS]



def load(name: str) -> list[dict]:
    path = os.path.join(IN_DIR, name + ".jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def group(rows: list[dict], *keys: str) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(k) for k in keys)].append(row)
    return groups


def race_key(season: int, gp_slug: str) -> tuple:
    gp_slug = SEASON_SLUG_ALIASES.get((season, gp_slug), gp_slug)
    return season, SLUG_ALIASES.get(gp_slug, gp_slug)


def row_text(row: dict) -> str:
    return " ".join(str(v) for k, v in row.items() if not SKIP_FIELDS.search(k) and isinstance(v, (str, int)))


def unique(values) -> list[str]:
    return list(dict.fromkeys(str(v) for v in values if v))


def grand_prix_docs() -> list[dict]:
    """One document per race: header, winner, participants, pole, fastest lap."""
    races = group(load("races"), "season", "race_id")
    results = group(load("race_results"), "season", "race_id")
    grids = group(load("starting_grid"), "season", "race_id")
    fastest = group(load("fastest_laps"), "season", "race_id")
    docs = []
    for gp in load("grand_prix"):
        key = (gp["season"], gp["race_id"])
        race = races.get(key, [{}])[0]
        pole = next((r for r in grids.get(key, []) if r.get("pos") == 1), {})
        lap = next((r for r in fastest.get(key, []) if r.get("pos") == 1), {})
        parts = [
            row_text(gp), race.get("grand_prix", ""),
            f"winner {race.get('winner', '')} {race.get('team', '')}",
            f"pole position {pole.get('driver', '')}" if pole else "",
            f"fastest lap {lap.get('driver', '')}" if lap else "",
            " ".join(unique(r.get("driver") for r in results.get(key, []))),
            " ".join(unique(r.get("team") for r in results.get(key, []))),
        ]
        classification = [[r.get("pos", ""), r.get("driver", ""), r.get("team", ""),
                           r.get("time_retired", ""), r.get("pts", "")] for r in results.get(key, [])]
        docs.append({"id": f"gp/{gp['season']}/{gp['race_id']}", "type": "grand_prix",
                     "title": gp.get("official_name") or f"{gp['season']} {gp['gp_slug']}",
                     "text": " ".join(p for p in parts if p),
                     "results": classification})
    return docs


def circuit_docs() -> list[dict]:
    """One document per circuit: Grand Prix names held there, track stats and description of the latest event."""
    races = group(load("races"), "season", "race_id")
    by_circuit = group(sorted(load("grand_prix"), key=lambda r: (r["season"], r["race_id"])), "circuit")
    events = {race_key(r["season"], r["gp_slug"]): r for r in load("circuits")}
    docs = []
    for (name,), gps in by_circuit.items():
        held = [(gp, races.get((gp["season"], gp["race_id"]), [{}])[0]) for gp in gps]
        # newest event page with a description; older races have no event page at all
        event = next((e for gp in reversed(gps) if (e := events.get(race_key(gp["season"], gp["gp_slug"])))
                      and e.get("about")), {})
        parts = [
            name, " ".join(unique(gp.get("locality") for gp in gps)),
            " ".join(unique(race.get("grand_prix") for _, race in held)),
            event.get("event_name", ""),
            f"lap record {event['fastest_lap_time_note']}" if event.get("fastest_lap_time_note") else "",
            event.get("about", ""),
        ]
        winners = [[gp["season"], race.get("grand_prix", gp.get("gp_slug", "")), race.get("winner", ""),
                    race.get("team", "")] for gp, race in held]
        docs.append({"id": f"circuit/{'-'.join(tokenize(name))}", "type": "circuit", "title": name,
                     "text": " ".join(p for p in parts if p),
                     "stats": {k: v for k, v in event.items() if k in CIRCUIT_STATS},
                     "results": winners})
    return docs


def driver_season_docs() -> list[dict]:
    """One document per driver and season: standing, teams and the races entered."""
    races = group(load("driver_season_results"), "season", "driver_slug")
    docs = []
    for row in load("driver_standings"):
        entries = races.get((row["season"], row.get("driver_slug")), [])
        champion = "world champion" if row.get("pos") == 1 else ""
        parts = [row_text(row), champion, f"position {row.get('pos', '')}",
                 " ".join(unique(r.get("team") for r in entries)),
                 " ".join(unique(r.get("grand_prix") for r in entries))]
        races_entered = [[r.get("grand_prix", ""), r.get("team", ""), r.get("race_pos", ""), r.get("pts", "")]
                         for r in entries]
        docs.append({"id": f"driver/{row['season']}/{row.get('driver_slug')}", "type": "driver_season",
                     "title": f"{row.get('driver', '')} - {row['season']} season",
                     "text": " ".join(p for p in parts if p),
                     "results": races_entered})
    return docs


def team_season_docs() -> list[dict]:
    """One document per team and season: standing, drivers and the races entered."""
    races = group(load("team_season_results"), "season", "team_slug")
    drivers = group(load("driver_standings"), "season", "team_slug")
    docs = []
    for row in load("team_standings"):
        key = (row["season"], row.get("team_slug"))
        champion = "constructors champion" if row.get("pos") == 1 else ""
        parts = [row_text(row), champion, f"position {row.get('pos', '')}",
                 " ".join(unique(r.get("driver") for r in drivers.get(key, []))),
                 " ".join(unique(r.get("grand_prix") for r in races.get(key, [])))]
        docs.append({"id": f"team/{row['season']}/{row.get('team_slug')}", "type": "team_season",
                     "title": f"{row.get('team', '')} - {row['season']} season",
                     "text": " ".join(p for p in parts if p)})
    return docs


def profile_docs() -> list[dict]:
    docs = []
    for row in load("drivers"):
        docs.append({"id": f"driver/{row['driver_slug']}", "type": "driver",
                     "title": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                     "text": row_text({k: v for k, v in row.items() if isinstance(v, str)})})
    for row in load("teams"):
        values = {k: " ".join(v) if isinstance(v, list) else v for k, v in row.items()}
        docs.append({"id": f"team/{row['team_slug']}", "type": "team", "title": row.get("team", ""),
                     "text": row_text({k: v for k, v in values.items() if isinstance(v, str)})})
    return docs


def build_documents() -> list[dict]:
    return grand_prix_docs() + circuit_docs() + driver_season_docs() + team_season_docs() + profile_docs()


# ---------------------------------------------------------------- index

def build_index(docs: list[dict]) -> dict:
    """Inverted index: term -> [[doc, tf], ...] plus document lengths for BM25 / cosine."""
    postings = defaultdict(list)
    lengths = []
    for doc_id, doc in enumerate(docs):
        terms = Counter(tokenize(" ".join([doc["title"]] * TITLE_BOOST + [doc["text"]])))
        lengths.append(sum(terms.values()))
        for term, tf in terms.items():
            postings[term].append([doc_id, tf])
    return {"n": len(docs), "avgdl": sum(lengths) / max(len(lengths), 1),
            "lengths": lengths, "postings": dict(postings)}


def idf_tfidf(n: int, df: int) -> float:
    """Classic IDF: log(N / df)."""
    return math.log(n / df)


def idf_bm25(n: int, df: int) -> float:
    """Robertson-Sparck Jones IDF with +1 so very common terms never go negative."""
    return math.log((n - df + 0.5) / (df + 0.5) + 1)


IDF = {"tfidf": idf_tfidf, "bm25": idf_bm25}


def tfidf_norms(index: dict) -> list[float]:
    """Euclidean length of every document's (1 + log tf) * idf vector, for cosine similarity."""
    norms = [0.0] * index["n"]
    for postings in index["postings"].values():
        idf = idf_tfidf(index["n"], len(postings))
        for doc, tf in postings:
            norms[doc] += ((1 + math.log(tf)) * idf) ** 2
    return [math.sqrt(x) for x in norms]


def save(docs: list[dict], index: dict) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(DOCS_FILE, "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)



class Searcher:
    def __init__(self):
        with open(DOCS_FILE, encoding="utf-8") as f:
            self.docs = [json.loads(line) for line in f]
        with open(INDEX_FILE, encoding="utf-8") as f:
            self.index = json.load(f)
        self.norms = tfidf_norms(self.index)

    def score_tfidf(self, terms: Counter) -> dict:
        """Cosine similarity of (1 + log tf) * idf vectors."""
        n, scores, query_norm = self.index["n"], defaultdict(float), 0.0
        for term, qtf in terms.items():
            postings = self.index["postings"].get(term)
            if not postings:
                continue
            idf = idf_tfidf(n, len(postings))
            weight = (1 + math.log(qtf)) * idf
            query_norm += weight ** 2
            for doc, tf in postings:
                scores[doc] += weight * (1 + math.log(tf)) * idf
        query_norm = math.sqrt(query_norm) or 1.0
        return {doc: s / (self.norms[doc] * query_norm) for doc, s in scores.items() if self.norms[doc]}

    def score_bm25(self, terms: Counter) -> dict:
        n, avgdl, lengths = self.index["n"], self.index["avgdl"], self.index["lengths"]
        scores = defaultdict(float)
        for term, qtf in terms.items():
            postings = self.index["postings"].get(term)
            if not postings:
                continue
            idf = idf_bm25(n, len(postings))
            for doc, tf in postings:
                norm = K1 * (1 - B + B * lengths[doc] / avgdl)
                scores[doc] += qtf * idf * tf * (K1 + 1) / (tf + norm)
        return scores

    def search(self, query: str, method: str = "bm25", top: int = 10) -> list[tuple[float, dict]]:
        terms = Counter(tokenize(query))
        scores = self.score_bm25(terms) if method == "bm25" else self.score_tfidf(terms)
        best = sorted(scores.items(), key=lambda item: -item[1])[:top]
        return [(score, self.docs[doc]) for doc, score in best]


RESULT_HEADERS = {
    "grand_prix": ("Pos", "Driver", "Team", "Time/Retired", "Pts"),
    "driver_season": ("Grand Prix", "Team", "Pos", "Pts"),
    "circuit": ("Season", "Grand Prix", "Winner", "Team"),
}


def print_table(headers: tuple, rows: list[list], limit: int) -> None:
    rows = [[str(v) for v in row] for row in rows[:limit]]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    for row in [list(headers), *rows]:
        print("        " + "  ".join(v.ljust(w) for v, w in zip(row, widths)))


def print_results(method: str, results: list, rows: int) -> None:
    print(f"\n--- {method.upper()} ---")
    if not results:
        print("no results")
    for rank, (score, doc) in enumerate(results, 1):
        print(f"{rank:2}. {score:8.4f}  [{doc['type']}] {doc['title']}  ({doc['id']})")
        if doc.get("stats"):
            print("        " + ", ".join(f"{k}: {v}" for k, v in doc["stats"].items()))
        if rows and doc.get("results"):
            print_table(RESULT_HEADERS[doc["type"]], doc["results"], rows)
            print()



def build() -> None:
    docs = build_documents()
    logging.info(f"{len(docs)} documents")
    index = build_index(docs)
    save(docs, index)
    logging.info(f"{len(index['postings'])} terms, avg document length {index['avgdl']:.1f} -> {INDEX_DIR}")


def search(query: str | None, method: str, top: int, rows: int) -> None:
    searcher = Searcher()
    methods = ["tfidf", "bm25"] if method == "both" else [method]
    queries = [query] if query else iter(lambda: input("\nquery> ").strip(), "")
    for q in queries:
        for m in methods:
            print_results(m, searcher.search(q, m, top), rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F1 inverted index with TF-IDF and BM25 ranking")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="build the index from extracted/*.jsonl")
    query = commands.add_parser("search", help="search the index (interactive without a query)")
    query.add_argument("query", nargs="?")
    query.add_argument("-m", "--method", choices=["tfidf", "bm25", "both"], default="both")
    query.add_argument("-n", "--top", type=int, default=10)
    query.add_argument("-r", "--rows", type=int, default=10, help="result rows shown per document, 0 = none")
    args = parser.parse_args()

    if args.command == "build":
        build()
    else:
        search(args.query, args.method, args.top, args.rows)


if __name__ == "__main__":
    main()
