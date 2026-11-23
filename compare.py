from __future__ import annotations

import os
import re
import sys
import json
import time
import shutil
import argparse
import tempfile
import statistics
import unicodedata

import indexer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUERIES = os.path.join(BASE_DIR, "compare_queries.json")
DOCUMENTS = os.path.join(BASE_DIR, "out", "documents")
OUT_DIR = os.path.join(BASE_DIR, "out", "compare")
K = 5
RUNS = 5
KEYWORD_ENGINES = (("old-tfidf", "old-tf"), ("old-bm25", "old-bm25"),
                   ("lucene-bm25", "l-bm25"), ("lucene-classic", "l-tf"))


def fold(value) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(c for c in value if not unicodedata.combining(c))


CURRENT_SEASON = 2026


def champion(d) -> bool:
    return d.get("position") == 1 and d.get("season", 0) < CURRENT_SEASON


def birth_city(d) -> str:
    return fold(d.get("birth_place")).split(",")[0].strip()


GOLD = {
    "q1": lambda d: d["type"] == "grand_prix" and d.get("season") == 1988 and "monaco" in fold(d.get("circuit")),
    "q2": lambda d: d["type"] == "driver_season" and champion(d) and "ferrari" in fold(d.get("team")),
    "q3": lambda d: d["type"] == "grand_prix" and d.get("winner") == "Lewis Hamilton"
    and "silverstone" in fold(d.get("circuit")),
    "q4": lambda d: d["id"] == "driver/max-verstappen" or (d["type"] == "driver_season"
                                                           and d["title"].startswith("Max Verstappen - ")),
    "q5": lambda d: d["type"] == "team_season" and champion(d) and d["title"].startswith("Red Bull Racing"),
    "q6": lambda d: d["type"] == "driver_season" and d.get("season") == 2007 and d["title"].startswith("Kimi R"),
    "q7": lambda d: d["type"] == "grand_prix" and 1950 <= d.get("season", 0) <= 1959 and "monza" in fold(d.get("circuit")),
    "q8": lambda d: d["type"] in ("driver", "driver_season") and birth_city(d) == "sao paulo",
    "q9": lambda d: d["type"] == "circuit" and d.get("length_m", 0) >= 6000,
    "q10": lambda d: d["type"] == "grand_prix" and re.search(r"rain|wet", fold(d.get("weather")))
    and "ferrari" in fold(d.get("winner_team")),
}
GOLD_EXTRA = {"q5": ["team/2010/RBR-Renault"]}


def read_documents(path: str = DOCUMENTS) -> list:
    docs = []
    for name in sorted(os.listdir(path)):
        if name.startswith("part-"):
            with open(os.path.join(path, name), encoding="utf-8") as f:
                docs.extend(json.loads(line) for line in f if line.strip())
    return docs


def old_ids() -> set:
    with open(indexer.DOCS_FILE, encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f}


def gold_sets(queries: list, docs: list, shared: set) -> dict:
    golds = {}
    for q in queries:
        ids_ = [d["id"] for d in docs if d["id"] in shared and GOLD[q["id"]](d)]
        golds[q["id"]] = sorted(set(ids_) | {i for i in GOLD_EXTRA.get(q["id"], []) if i in shared})
    return golds


def metrics(ranked: list, gold: set) -> dict:
    k = min(K, len(gold)) or K
    rr = next((1.0 / i for i, doc_id in enumerate(ranked[:10], 1) if doc_id in gold), 0.0)
    return {"k": k, "p_at_k": sum(1 for d in ranked[:k] if d in gold) / k, "mrr": rr}


def timed(engine, query: str, top: int = 10):
    engine.search(query, top)
    times, result = [], None
    for _ in range(RUNS):
        result = engine.search(query, top)
        times.append(result.ms)
    return result, statistics.median(times)


def dir_size(path: str) -> int:
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, files in os.walk(path) for f in files)


def build_costs() -> dict:
    import lucene_indexer
    tmp = tempfile.mkdtemp(prefix="f1-build-")
    try:
        saved = indexer.INDEX_DIR, indexer.DOCS_FILE, indexer.INDEX_FILE
        indexer.INDEX_DIR = os.path.join(tmp, "old")
        indexer.DOCS_FILE = os.path.join(indexer.INDEX_DIR, "documents.jsonl")
        indexer.INDEX_FILE = os.path.join(indexer.INDEX_DIR, "index.json")
        start = time.perf_counter()
        indexer.build()
        old_seconds = time.perf_counter() - start
        old = {"seconds": round(old_seconds, 2), "bytes": dir_size(indexer.INDEX_DIR),
               "index_json_bytes": os.path.getsize(indexer.INDEX_FILE)}
        indexer.INDEX_DIR, indexer.DOCS_FILE, indexer.INDEX_FILE = saved
        new = lucene_indexer.build(DOCUMENTS, os.path.join(tmp, "lucene"))
        return {"old": old, "lucene": {"seconds": new["seconds"], "bytes": new["bytes"]}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def table(headers: list, rows: list) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [line, "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |", line]
    out += ["| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |" for r in rows]
    return "\n".join(out + [line])


def short(doc_id: str, titles: dict, gold: set) -> str:
    mark = "*" if doc_id in gold else " "
    return "{}{} ({})".format(mark, titles.get(doc_id, "?")[:42], doc_id)


def print_gold(queries, golds, titles) -> None:
    for q in queries:
        ids_ = golds[q["id"]]
        print("\n{} {}  |gold| = {}".format(q["id"], q["question"], len(ids_)))
        for doc_id in ids_:
            print("   - {}  {}".format(doc_id, titles[doc_id]))


def run(queries, golds, titles, shared) -> dict:
    import search
    engines = {label: search.make_engine(name) for label, name in KEYWORD_ENGINES}
    report = {"queries": [], "build": build_costs()}
    for q in queries:
        gold = set(golds[q["id"]])
        entry = {"id": q["id"], "question": q["question"], "gold": len(gold), "old_index_query": {}, "lucene_query": {}}
        for label in engines:
            result, ms = timed(engines[label], q["old_index_query"])
            ranked = [i for i in result.ids if i in shared]
            entry["old_index_query"][label] = dict(metrics(ranked, gold), ms=round(ms, 3), top=ranked[:K], hits=result.total)
        base = set(entry["old_index_query"]["old-bm25"]["top"])
        for label, e in entry["old_index_query"].items():
            e["overlap_old_bm25"] = len(base & set(e["top"]))
        result, ms = timed(engines["lucene-bm25"], q["lucene_query"])
        entry["lucene_query"] = {"query": q["lucene_query"], "parsed": result.query, "hits": result.total,
                                 "ms": round(ms, 3), "top": result.ids[:K]}
        try:
            engines["old-bm25"].search(q["lucene_query"])
            entry["lucene_query"]["old"] = "supported"
        except search.Unsupported as e:
            entry["lucene_query"]["old"] = "unsupported: {}".format(e)
        report["queries"].append(entry)
    report["ablation"] = ablation(engines["lucene-bm25"], queries, golds)
    return report


ABLATIONS = (("title^3 text wiki_text (default)", ("title", "text", "wiki_text"), {"title": 3.0}),
             ("title^1 text wiki_text", ("title", "text", "wiki_text"), {}),
             ("title^3 text", ("title", "text"), {"title": 3.0}),
             ("text wiki_text", ("text", "wiki_text"), {}),
             ("text", ("text",), {}))


def ablation(engine, queries, golds) -> list:
    import lucene_schema as schema
    rows = []
    saved = dict(engine.parsers)
    for label, fields, boosts in ABLATIONS:
        engine.parsers[""] = schema.query_parser(engine.analyzer, list(fields), boosts)
        per = {q["id"]: metrics(engine.search(q["old_index_query"], 10).ids, set(golds[q["id"]])) for q in queries}
        rows.append({"config": label, "per_question_p_at_k": {k: m["p_at_k"] for k, m in per.items()},
                     "p_at_k": statistics.mean(m["p_at_k"] for m in per.values()),
                     "mrr": statistics.mean(m["mrr"] for m in per.values())})
    engine.parsers.clear()
    engine.parsers.update(saved)
    return rows


def render(report, queries, golds, titles, shared_info) -> str:
    out = ["OLD INDEX (indexer.py) vs LUCENE (lucene_indexer.py)",
           "metrics on `old_index_query`: P@k (k = min(5, |gold|)), MRR (top 10), overlap of top-5 with old-bm25,",
           "median time of {} warm runs (ms, index already loaded). * = document in the gold standard.".format(RUNS),
           shared_info, ""]
    b = report["build"]
    out += ["BUILD", table(["index", "build s", "size on disk (bytes)", "note"], [
        ["old (indexer.py)", b["old"]["seconds"], b["old"]["bytes"],
         "documents.jsonl + index.json (postings {} B)".format(b["old"]["index_json_bytes"])],
        ["lucene", b["lucene"]["seconds"], b["lucene"]["bytes"], "forceMerge(1), stored fields incl. results"]]), ""]

    rows = []
    for e in report["queries"]:
        for label, m in e["old_index_query"].items():
            rows.append([e["id"], e["gold"], label, "{:.2f}".format(m["p_at_k"]) + "@{}".format(m["k"]),
                         "{:.2f}".format(m["mrr"]), m["overlap_old_bm25"],
                         m["hits"], "{:.2f}".format(m["ms"])])
    out += ["OLD_INDEX_QUERY (same text for all engines)", table(
        ["q", "|gold|", "engine", "P@k", "MRR", "overlap@5", "hits", "ms"], rows), ""]

    avg = []
    for label, _ in KEYWORD_ENGINES:
        ms = [e["old_index_query"][label] for e in report["queries"]]
        avg.append([label, "{:.3f}".format(statistics.mean(m["p_at_k"] for m in ms)),
                    "{:.3f}".format(statistics.mean(m["mrr"] for m in ms)),
                    "{:.2f}".format(statistics.median(m["ms"] for m in ms))])
    out += ["OLD_INDEX_QUERY - mean over {} questions".format(len(report["queries"])),
            table(["engine", "mean P@k", "MRR", "median ms"], avg), ""]

    rows = [[e["id"], e["lucene_query"]["query"], e["lucene_query"]["hits"],
             "{:.2f}".format(e["lucene_query"]["ms"]), e["lucene_query"]["old"].split(":")[0]]
            for e in report["queries"]]
    out += ["LUCENE_QUERY (structured; demonstration of query types, no quality metric)",
            table(["q", "query", "hits", "ms", "old index"], rows), ""]

    qids = [e["id"] for e in report["queries"]]
    rows = [[a["config"]] + ["{:.2f}".format(a["per_question_p_at_k"][q]) for q in qids] +
            ["{:.3f}".format(a["p_at_k"]), "{:.3f}".format(a["mrr"])]
            for a in report["ablation"]]
    out += ["ABLATION - lucene-bm25 on old_index_query, P@k per question (diagnosis found on the same questions, not tuning)",
            table(["default fields"] + qids + ["mean P@k", "MRR"], rows), ""]

    out.append("TOP-5 PER QUESTION")
    for e, q in zip(report["queries"], queries):
        gold = set(golds[q["id"]])
        out += ["", "{} {}  |gold| = {}".format(e["id"], e["question"], e["gold"]),
                "   old_index_query: {}".format(q["old_index_query"])]
        for label, m in e["old_index_query"].items():
            out.append("   {:<15}".format(label) + " | ".join(short(i, titles, gold) for i in m["top"]))
        lq = e["lucene_query"]
        out.append("   lucene_query: {}   parsed: {}".format(lq["query"], lq["parsed"]))
        out.append("   {:<15}".format("lucene-bm25") + " | ".join(short(i, titles, gold) for i in lq["top"]))
        out.append("   old index on lucene_query: " + lq["old"])
    return "\n".join(out) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", action="store_true", help="only print the gold standard for review")
    args = parser.parse_args()

    with open(QUERIES, encoding="utf-8") as f:
        queries = json.load(f)
    docs = read_documents()
    titles = {d["id"]: d["title"] for d in docs}
    old = old_ids()
    shared = old & set(titles)
    shared_info = "documents: lucene {}, old {}, shared {} (gold standard and metrics use shared IDs only)".format(
        len(titles), len(old), len(shared))
    golds = gold_sets(queries, docs, shared)
    if args.gold:
        print(shared_info)
        print_gold(queries, golds, titles)
        return

    report = run(queries, golds, titles, shared)
    report["gold"] = golds
    os.makedirs(OUT_DIR, exist_ok=True)
    text = render(report, queries, golds, titles, shared_info)
    with open(os.path.join(OUT_DIR, "compare_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(OUT_DIR, "compare_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(text)


if __name__ == "__main__":
    main()
