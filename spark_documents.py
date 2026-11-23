from __future__ import annotations

import re
import json
import argparse

from pyspark.sql import functions as F
from pyspark.sql import Row

import indexer
from wiki_regex import join_key
from join import table
from spark_common import get_spark, write_jsonl, write_text, read_jsonl, path_exists

DOC_TYPES = ("grand_prix", "circuit", "driver_season", "team_season", "driver", "team")
ENTITY_OF_DOC = {"grand_prix": "grand_prix", "circuit": "circuit", "driver_season": "driver",
                 "driver": "driver", "team_season": "team", "team": "team"}
INT_FIELDS = ("season", "position", "round", "birth_year", "wins", "poles", "championships",
              "attendance", "length_m", "turns", "opened_year")
TEXT_FIELDS = ("winner", "winner_team", "pole", "fastest_lap", "team", "circuit", "location",
               "birth_place", "weather")
KEYWORD_FIELDS = ("driver_code", "nationality")


INT = re.compile(r"\d[\d,]*")
DECIMAL = re.compile(r"\d+(?:\.\d+)?")
YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
NATIONALITY_CODE = re.compile(r"^([A-Z]{3})\b")

RANGES = {"season": (1950, 2100), "position": (1, 200), "round": (1, 30), "birth_year": (1850, 2015),
          "wins": (0, 500), "poles": (0, 500), "championships": (0, 20), "attendance": (100, 2000000),
          "length_m": (1000, 30000), "turns": (1, 100), "opened_year": (1880, 2100)}


def first_int(value) -> int | None:
    if isinstance(value, int):
        return value
    m = INT.search(str(value or ""))
    return int(m.group(0).replace(",", "")) if m else None


def first_year(value) -> int | None:
    m = YEAR.search(str(value or ""))
    return int(m.group(1)) if m else None


def km_to_m(value) -> int | None:
    m = DECIMAL.search(str(value or ""))
    return int(round(float(m.group(0)) * 1000)) if m else None


def nationality_code(value) -> str | None:
    m = NATIONALITY_CODE.match(str(value or "").strip())
    return m.group(1) if m else None


def in_range(field: str, value) -> int | None:
    if value is None:
        return None
    low, high = RANGES[field]
    return value if low <= value <= high else None


def wiki_fields(entity_type: str, wiki: dict) -> dict:
    out = {}
    if entity_type == "driver":
        out.update(birth_year=first_year(wiki.get("birth_date")), wins=first_int(wiki.get("wins")),
                   poles=first_int(wiki.get("poles")), championships=first_int(wiki.get("championships")),
                   birth_place=wiki.get("birth_place"), nationality=nationality_code(wiki.get("nationality")))
    elif entity_type == "grand_prix":
        out.update(round=first_int(wiki.get("round")), attendance=first_int(wiki.get("attendance")),
                   length_m=km_to_m(wiki.get("course_km")), weather=wiki.get("weather"),
                   location=wiki.get("location"))
    elif entity_type == "circuit":
        out.update(length_m=km_to_m(wiki.get("length_km")), turns=first_int(wiki.get("turns")),
                   opened_year=first_year(wiki.get("opened")), location=wiki.get("location"))
    elif entity_type == "team":
        out.update(wins=first_int(wiki.get("wins")), poles=first_int(wiki.get("poles")),
                   championships=first_int(wiki.get("constructors_titles")))
    return out


def clean_fields(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k in RANGES:
            v = in_range(k, v)
        if v not in (None, "", []):
            out[k] = v
    return out


def merge_fields(own: dict, wiki: dict) -> dict:
    merged = dict(wiki)
    for k, v in own.items():
        if k == "location" and wiki.get("location"):
            merged[k] = "{}; {}".format(v, wiki["location"])
        else:
            merged[k] = v
    return merged


def wiki_text(wiki: dict, abstract: str | None) -> str:
    return " ".join([abstract or ""] + ["{} {}".format(k.replace("_", " "), v) for k, v in wiki.items()]).strip()


def indexed(sc, extracted: str, name: str):
    return table(sc, extracted, name).zipWithIndex()


def in_order(rows) -> list:
    return [r for r, _ in sorted(rows, key=lambda ri: ri[1])]


def first_row(rows) -> dict:
    rows = in_order(rows)
    return rows[0] if rows else {}


def pos_int(value) -> int | None:
    return value if isinstance(value, int) else None


def grand_prix_docs(sc, extracted: str):
    def key(ri):
        return (ri[0]["season"], ri[0]["race_id"]), ri

    gps, races, results, grids, fastest = (indexed(sc, extracted, n).map(key) for n in
                                           ("grand_prix", "races", "race_results", "starting_grid", "fastest_laps"))

    def build(kv):
        (season, race_id), (gp_rows, race_rows, result_rows, grid_rows, lap_rows) = kv
        race = first_row(race_rows)
        grids_, laps = in_order(grid_rows), in_order(lap_rows)
        pole = next((r for r in grids_ if r.get("pos") == 1), {})
        lap = next((r for r in laps if r.get("pos") == 1), {})
        for gp in in_order(gp_rows):
            doc = indexer.grand_prix_doc(gp, race, in_order(result_rows), grids_, laps)
            fields = {"season": season, "winner": race.get("winner"), "winner_team": race.get("team"),
                      "pole": pole.get("driver"), "fastest_lap": lap.get("driver"), "circuit": gp.get("circuit"),
                      "location": gp.get("locality")}
            yield doc, "{}/{}".format(season, race_id), fields

    return gps.groupWith(races, results, grids, fastest).flatMap(build)


def circuit_docs(sc, extracted: str):
    races = indexed(sc, extracted, "races").map(lambda ri: ((ri[0]["season"], ri[0]["race_id"]), ri)) \
        .reduceByKey(lambda a, b: a if a[1] <= b[1] else b).mapValues(lambda ri: ri[0])
    events = indexed(sc, extracted, "circuits") \
        .map(lambda ri: (indexer.race_key(ri[0]["season"], ri[0]["gp_slug"]), ri)) \
        .reduceByKey(lambda a, b: a if a[1] >= b[1] else b).mapValues(lambda ri: ri[0])
    held = table(sc, extracted, "grand_prix").map(lambda gp: ((gp["season"], gp["race_id"]), gp)) \
        .leftOuterJoin(races) \
        .map(lambda kv: (indexer.race_key(kv[1][0]["season"], kv[1][0]["gp_slug"]), (kv[1][0], kv[1][1] or {}))) \
        .leftOuterJoin(events) \
        .map(lambda kv: (kv[1][0][0].get("circuit"), (kv[1][0][0], kv[1][0][1], kv[1][1])))

    def build(kv):
        name, rows = kv
        rows = sorted(rows, key=lambda r: (r[0]["season"], r[0]["race_id"]))
        doc = indexer.circuit_doc(name, rows)
        event = next((e for _, _, e in reversed(rows) if e and e.get("about")), {})
        fields = {"circuit": name, "location": " ".join(indexer.unique(gp.get("locality") for gp, _, _ in rows)),
                  "length_m": km_to_m(event.get("circuit_length"))}
        return doc, join_key(name), fields

    return held.groupByKey().map(build)


def driver_season_docs(sc, extracted: str):
    def key(ri):
        return (ri[0]["season"], ri[0].get("driver_slug")), ri

    standings = indexed(sc, extracted, "driver_standings").map(key)
    entries = indexed(sc, extracted, "driver_season_results").map(key)

    def build(kv):
        (season, _), (rows, entry_rows) = kv
        entries_ = in_order(entry_rows)
        for row in in_order(rows):
            doc = indexer.driver_season_doc(row, entries_)
            fields = {"season": season, "position": pos_int(row.get("pos")),
                      "team": " / ".join(indexer.unique(r.get("team") for r in entries_)) or row.get("team"),
                      "driver_code": row.get("driver_code"), "nationality": nationality_code(row.get("nationality"))}
            yield doc, join_key(row.get("driver") or ""), fields

    return standings.groupWith(entries).flatMap(build)


def team_season_docs(sc, extracted: str):
    def key(ri):
        return (ri[0]["season"], ri[0].get("team_slug")), ri

    standings = indexed(sc, extracted, "team_standings").map(key)
    races = indexed(sc, extracted, "team_season_results").map(key)
    drivers = indexed(sc, extracted, "driver_standings").map(key)

    def build(kv):
        (season, _), (rows, race_rows, driver_rows) = kv
        for row in in_order(rows):
            doc = indexer.team_season_doc(row, in_order(race_rows), in_order(driver_rows))
            fields = {"season": season, "position": pos_int(row.get("pos")), "team": row.get("team")}
            yield doc, join_key(row.get("team") or ""), fields

    return standings.groupWith(races, drivers).flatMap(build)


def profile_docs(sc, extracted: str):
    def driver(row):
        doc = indexer.driver_profile_doc(row)
        return doc, join_key(doc["title"]), {"team": row.get("team"), "location": row.get("country")}

    def team(row):
        doc = indexer.team_profile_doc(row)
        return doc, join_key(doc["title"]), {"team": row.get("team")}

    return table(sc, extracted, "drivers").map(driver).union(table(sc, extracted, "teams").map(team))


def all_docs(sc, extracted: str):
    return sc.union([grand_prix_docs(sc, extracted), circuit_docs(sc, extracted), driver_season_docs(sc, extracted),
                     team_season_docs(sc, extracted), profile_docs(sc, extracted)])


def enrich(row) -> dict:
    doc = json.loads(row.doc)
    own = json.loads(row.fields)
    wiki = json.loads(row.wiki) if row.wiki else {}
    fields = merge_fields(clean_fields(own), clean_fields(wiki_fields(row.entity_type, wiki)))
    doc.update(fields)
    doc["entity_key"] = row.entity_key
    if row.wiki_title:
        doc["wiki_title"] = row.wiki_title
        doc["wiki_text"] = wiki_text(wiki, row.abstract)
        if row.abstract:
            doc["abstract"] = row.abstract
    return doc


def with_f1_length(docs):
    longest = docs.filter(lambda d: d["type"] == "grand_prix" and d.get("length_m") and d.get("circuit")) \
        .map(lambda d: (join_key(d["circuit"]), d["length_m"])).reduceByKey(max)

    def apply(kv):
        _, (doc, length) = kv
        doc = dict(doc)
        if length:
            doc["length_m"], doc["length_source"] = length, "max course_km of its F1 race reports (wiki)"
        elif doc.get("length_m"):
            doc["length_source"] = "circuit page (current layout)"
        return doc

    circuits = docs.filter(lambda d: d["type"] == "circuit").map(lambda d: (d["entity_key"], d)) \
        .leftOuterJoin(longest).map(apply)
    return docs.filter(lambda d: d["type"] != "circuit").union(circuits)


def check_old(sc, docs, old_path: str) -> dict:
    base_keys = ("id", "type", "title", "text", "results", "stats")
    canon = lambda d: json.dumps({k: d[k] for k in base_keys if k in d}, ensure_ascii=False, sort_keys=True)
    ours = docs.map(lambda d: (d["id"], canon(d)))
    old = read_jsonl(sc, old_path).map(lambda d: (d["id"], canon(d)))
    pairs = ours.fullOuterJoin(old).cache()
    only_new = pairs.filter(lambda kv: kv[1][1] is None).keys().collect()
    only_old = pairs.filter(lambda kv: kv[1][0] is None).keys().collect()
    differ = pairs.filter(lambda kv: kv[1][0] and kv[1][1] and kv[1][0] != kv[1][1]).keys().collect()
    return {"old_file": old_path, "spark_docs": docs.count(), "old_docs": old.count(),
            "common_ids": pairs.filter(lambda kv: kv[1][0] and kv[1][1]).count(),
            "identical_content": pairs.filter(lambda kv: kv[1][0] and kv[1][0] == kv[1][1]).count(),
            "only_spark": sorted(only_new)[:50], "only_spark_count": len(only_new),
            "only_old": sorted(only_old)[:50], "only_old_count": len(only_old),
            "different_content": sorted(differ)[:50], "different_content_count": len(differ)}


def build_report(spark, docs) -> dict:
    schema = "type string, has_wiki int, " + ", ".join("{} int".format(f) for f in INT_FIELDS)
    rows = docs.map(lambda d: Row(type=d["type"], has_wiki=1 if d.get("wiki_title") else 0,
                                  **{f: d.get(f) for f in INT_FIELDS}))
    df = spark.createDataFrame(rows, schema)
    aggs = [F.count("*").alias("documents"), F.sum("has_wiki").alias("with_wiki")] + \
           [F.count(f).alias(f) for f in INT_FIELDS]
    by_type = {r["type"]: r.asDict() for r in df.groupBy("type").agg(*aggs).collect()}
    total = df.agg(*aggs).collect()[0].asDict()
    unique_wiki = dict(docs.filter(lambda d: d.get("wiki_title"))
                       .map(lambda d: (d["type"], d["wiki_title"])).distinct().countByKey())
    report = {"by_type": {}, "total": {}}
    for t in DOC_TYPES:
        s = by_type.get(t, {"documents": 0, "with_wiki": 0})
        report["by_type"][t] = {
            "documents": s["documents"], "with_wiki": s["with_wiki"] or 0,
            "with_wiki_pct": round(100.0 * (s["with_wiki"] or 0) / s["documents"], 1) if s["documents"] else 0.0,
            "unique_wiki_pages": unique_wiki.get(t, 0),
            "numeric_fields": {f: s.get(f, 0) for f in INT_FIELDS if s.get(f)}}
    report["total"] = {"documents": total["documents"], "with_wiki": total["with_wiki"],
                       "with_wiki_pct": round(100.0 * total["with_wiki"] / total["documents"], 1),
                       "unique_wiki_pages": docs.filter(lambda d: d.get("wiki_title"))
                       .map(lambda d: d["wiki_title"]).distinct().count(),
                       "numeric_fields": {f: total[f] for f in INT_FIELDS}}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build search documents enriched with Wikipedia (Spark)")
    parser.add_argument("extracted", help="output of spark_extractor.py")
    parser.add_argument("joined", help="output of join.py (<entity>/ directories)")
    parser.add_argument("output", help="output directory: part-* JSON lines + documents_report.json")
    parser.add_argument("--check-old", metavar="FILE", help="index/documents.jsonl of indexer.py to compare with")
    args = parser.parse_args()
    out, joined = args.output.rstrip("/"), args.joined.rstrip("/")

    spark = get_spark("f1-documents")
    sc = spark.sparkContext

    docs = all_docs(sc, args.extracted).map(lambda t: Row(
        id=t[0]["id"], entity_type=ENTITY_OF_DOC[t[0]["type"]], entity_key=t[1],
        doc=json.dumps(t[0], ensure_ascii=False), fields=json.dumps(t[2], ensure_ascii=False)))
    docs_df = spark.createDataFrame(docs, "id string, entity_type string, entity_key string, doc string, fields string")

    entities = sc.union([read_jsonl(sc, "{}/{}".format(joined, t)) for t in ("driver", "grand_prix", "circuit", "team")
                         if path_exists(sc, "{}/{}".format(joined, t))]) \
        .filter(lambda e: e.get("wiki_title")) \
        .map(lambda e: Row(e_type=e["entity_type"], e_id=e["entity_id"], wiki_title=e["wiki_title"],
                           wiki=json.dumps(e.get("wiki", {}), ensure_ascii=False), abstract=e.get("abstract")))
    wiki_df = spark.createDataFrame(entities, "e_type string, e_id string, wiki_title string, wiki string, "
                                              "abstract string")

    enriched = docs_df.join(F.broadcast(wiki_df), (docs_df.entity_type == wiki_df.e_type) &
                            (docs_df.entity_key == wiki_df.e_id), "left").rdd.map(enrich)
    enriched = with_f1_length(enriched).sortBy(lambda d: (DOC_TYPES.index(d["type"]), d["id"])).cache()

    write_jsonl(enriched, out)
    report = build_report(spark, enriched)
    report["circuit_length_source"] = dict(enriched.filter(lambda d: d["type"] == "circuit")
                                           .map(lambda d: d.get("length_source", "none")).countByValue())
    if args.check_old:
        report["check_old"] = check_old(sc, enriched, args.check_old)
    write_text(sc, out + "_report.json", json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["total"], indent=2))
    for t, s in report["by_type"].items():
        print("{:<14} {:>6} docs  {:>6} with wiki ({}%)".format(t, s["documents"], s["with_wiki"], s["with_wiki_pct"]))
    if args.check_old:
        c = report["check_old"]
        print("vs old index: common {}, identical {}, only spark {}, only old {}, different {}".format(
            c["common_ids"], c["identical_content"], c["only_spark_count"], c["only_old_count"],
            c["different_content_count"]))
    spark.stop()


if __name__ == "__main__":
    main()
