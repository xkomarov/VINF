from __future__ import annotations

import re
import json
import argparse

from pyspark.sql import functions as F
from pyspark.sql import Row

import wiki_regex
from wiki_regex import join_key
from spark_common import get_spark, write_jsonl, write_text, read_jsonl, path_exists

ENTITY_TYPES = ("driver", "grand_prix", "circuit", "team")

WIKI_ATTRIBUTES = {
    "driver": {
        "birth_date": ["birth_date"], "birth_place": ["birth_place"], "death_date": ["death_date"],
        "death_place": ["death_place"], "nationality": ["nationality"],
        "championships": ["championships", "titles"], "wins": ["wins"], "podiums": ["podiums"],
        "poles": ["poles"], "fastest_laps": ["fastest_laps"], "points": ["points"],
        "races": ["races", "starts"], "first_race": ["first_race"], "first_win": ["first_win"],
        "last_race": ["last_race"], "teams": ["teams", "former_teams"], "years": ["years", "years_active"],
    },
    "grand_prix": {
        "date": ["date", "fulldate"], "official_name": ["official_name"], "location": ["location"],
        "course": ["course"], "course_km": ["course_km"], "distance_laps": ["distance_laps"],
        "distance_km": ["distance_km"], "weather": ["weather"], "attendance": ["attendance"],
        "pole_driver": ["pole_driver"], "pole_time": ["pole_time"], "fastest_lap_driver": ["fast_driver"],
        "fastest_lap_time": ["fast_time"], "winner": ["first_driver", "winner"], "winner_team": ["first_team"],
        "round": ["race_no"],
    },
    "circuit": {
        "location": ["location"], "coordinates": ["coordinates"], "opened": ["opened"], "closed": ["closed"],
        "former_names": ["former_names"], "length_km": ["length_km"], "turns": ["turns"],
        "capacity": ["capacity"], "owner": ["owner"], "events": ["events"], "fia_grade": ["fia_grade"],
        "record_time": ["record_time"], "record_driver": ["record_driver"], "record_year": ["record_year"],
    },
    "team": {
        "full_name": ["long_name", "official_name"], "base": ["base"], "founders": ["founders"],
        "principal": ["principal", "director"], "engines": ["engines"], "debut": ["debut"],
        "last_race": ["last_race", "final_race"], "races": ["races"], "constructors_titles": ["cons_champ"],
        "drivers_titles": ["drivers_champ"], "wins": ["wins"], "poles": ["poles"],
    },
}


GENERIC_CIRCUIT_WORDS = re.compile(
    r"\b(circuit|circuito|circuit de|autodromo|autodrome|raceway|international|internacional|internazionale|"
    r"nazionale|speedway|street|park|grand prix|motor|racing|ring)\b")
GP_TITLE_YEAR = re.compile(r"^(\d{4})(?= )")
DRIVER_CODE_SUFFIX = re.compile(r"\s+[A-Z]'?[A-Z]{1,2}$")
FLAG_CODE_PREFIX = re.compile(r"^[A-Z]{3}\s+")
TEAM_WORDS = re.compile(r"\b(scuderia|team|racing|f1|in formula one|formula one|grand prix|international|"
                        r"engineering|car company|cars|motorsports?|organisation|ltd|limited)\b")
TEAM_TYPE = "team"
MULTI_PAGE_TYPES = ("team", "driver")
CAREER_FIELDS = {"team": ("debut",), "driver": ("years", "years_active", "first_year", "first_race", "last_race")}
MIN_RACING_AGE = 17
SEASON_GAP = 10



def table(sc, extracted: str, name: str):
    base = extracted.rstrip("/")
    for path in ("{}/{}".format(base, name), "{}/{}.jsonl".format(base, name)):
        if path_exists(sc, path):
            return read_jsonl(sc, path)
    return sc.emptyRDD()



def name_variants(name: str) -> list:
    key = join_key(name)
    tokens = key.split()
    keys = [(0, key)]
    if len(tokens) > 2:
        keys.append((1, "{} {}".format(tokens[0], tokens[-1])))
    stripped = DRIVER_CODE_SUFFIX.sub("", name)
    if stripped != name:
        keys.append((2, join_key(stripped)))
    return keys


def gp_name_variants(season: int, official_key: str) -> list:
    m = re.search(r"^(.*?)\bgrand prix\b", official_key)
    if not m:
        return []
    words = [w for w in m.group(1).split() if not w.isdigit()]
    return [(5 - n, "{} {} grand prix".format(season, " ".join(words[-n:])))
            for n in (3, 2, 1) if len(words) >= n]


def team_variants(name: str) -> list:
    tokens = join_key(name).split()
    return [(len(tokens) - n, " ".join(tokens[:n])) for n in range(len(tokens), 0, -1)]


def circuit_variants(name: str, gp_venue: str | None = None) -> list:
    keys = [(0, join_key(name))]
    for rank, key in ((1, " ".join(GENERIC_CIRCUIT_WORDS.sub(" ", keys[0][1]).split())), (2, gp_venue)):
        if key and key not in {k for _, k in keys}:
            keys.append((rank, key))
    return keys


def merge_entity(a: dict, b: dict) -> dict:
    merged = dict(a)
    merged["first_season"] = min(a["first_season"], b["first_season"])
    merged["last_season"] = max(a["last_season"], b["last_season"])
    merged["entries"] = a["entries"] + b["entries"]
    for field in ("codes", "slugs", "nationalities", "names", "seasons"):
        merged[field] = sorted(set(a.get(field, [])) | set(b.get(field, [])))
    return merged


def entity_seed(name: str, season, **sets) -> dict:
    season = int(season)
    seed = {"name": name, "first_season": season, "last_season": season, "entries": 1}
    for field, value in sets.items():
        seed[field] = [value] if value else []
    return seed


def drivers_rdd(sc, extracted: str):
    results = table(sc, extracted, "race_results").filter(lambda r: r.get("driver")) \
        .map(lambda r: (join_key(r["driver"]), entity_seed(r["driver"], r["season"], codes=r.get("driver_code"),
                                                           seasons=int(r["season"]))))
    standings = table(sc, extracted, "driver_standings").filter(lambda r: r.get("driver")) \
        .map(lambda r: (join_key(r["driver"]), entity_seed(
            r["driver"], r["season"], codes=r.get("driver_code"), slugs=r.get("driver_slug"),
            nationalities=r.get("nationality"), seasons=int(r["season"]))))
    profiles = table(sc, extracted, "drivers") \
        .map(lambda r: ("{} {}".format(r.get("first_name", ""), r.get("last_name", "")).strip(), r)) \
        .filter(lambda kv: kv[0]) \
        .map(lambda kv: (join_key(kv[0]), entity_seed(kv[0], kv[1].get("season", 9999), slugs=kv[1].get("driver_slug"))))
    return results.union(standings).union(profiles).reduceByKey(merge_entity) \
        .map(lambda kv: dict(kv[1], entity_type="driver", entity_id=kv[0],
                             candidates=name_variants(kv[1]["name"])))


def season_rounds(kv):
    season, rows = kv
    seen, n = set(), 0
    for _, race in sorted(rows, key=lambda x: x[0]):
        race_id = int(race["race_id"])
        if race_id in seen:
            continue
        seen.add(race_id)
        n += 1
        yield (season, race_id), (n, race.get("grand_prix", ""))


def grand_prix_rdd(sc, extracted: str):
    races = table(sc, extracted, "races").zipWithIndex() \
        .map(lambda ri: (int(ri[0]["season"]), (ri[1], ri[0]))).groupByKey().flatMap(season_rounds)

    def entity(kv):
        (season, race_id), (gp, race) = kv
        official = gp.get("official_name", "")
        name = race[1] if race else gp.get("gp_slug", "")
        keys = []
        key = join_key(official)
        if re.match(r"^\d{4} .+ (grand prix|500)$", key) and key.startswith(str(season)):
            keys.append((0, key))
        if race:
            keys.append((1, "round {} {}".format(season, race[0])))
        keys.extend(k for k in gp_name_variants(season, key) if k[1] not in {x[1] for x in keys})
        return {"entity_type": "grand_prix", "entity_id": "{}/{}".format(season, race_id),
                "name": official or "{} {}".format(season, name), "season": season, "race_id": race_id,
                "grand_prix": name, "circuit": gp.get("circuit", ""), "dates": gp.get("dates", ""),
                "candidates": keys}

    gps = table(sc, extracted, "grand_prix").map(lambda r: ((int(r["season"]), int(r["race_id"])), r))
    return gps.leftOuterJoin(races).map(entity)


def circuits_rdd(sc, extracted: str, gp_locations):
    venue = gp_locations.map(lambda kv: (kv, 1)).reduceByKey(lambda a, b: a + b) \
        .map(lambda kv: (kv[0][0], (-kv[1], kv[0][1]))).reduceByKey(min).mapValues(lambda v: v[1])
    return table(sc, extracted, "grand_prix").filter(lambda r: r.get("circuit")) \
        .map(lambda r: (join_key(r["circuit"]), entity_seed(r["circuit"], r["season"], names=r.get("locality")))) \
        .reduceByKey(merge_entity).leftOuterJoin(venue) \
        .map(lambda kv: dict(kv[1][0], entity_type="circuit", entity_id=kv[0], localities=kv[1][0].get("names", []),
                             candidates=circuit_variants(kv[1][0]["name"], kv[1][1])))


def teams_rdd(sc, extracted: str):
    results = table(sc, extracted, "race_results").filter(lambda r: r.get("team")) \
        .map(lambda r: (join_key(r["team"]), entity_seed(r["team"], r["season"])))
    standings = table(sc, extracted, "team_standings").filter(lambda r: r.get("team")) \
        .map(lambda r: (join_key(r["team"]), entity_seed(r["team"], r["season"], slugs=r.get("team_slug"))))
    return results.union(standings).reduceByKey(merge_entity) \
        .map(lambda kv: dict(kv[1], entity_type="team", entity_id=kv[0],
                             candidates=team_variants(kv[1]["name"])))



def read_wiki_pages(sc, wiki: str):
    pages = read_jsonl(sc, wiki + "/wiki_pages").map(lambda p: (p["title"], (1, p)))
    if path_exists(sc, wiki + "/wiki_pages_f1"):
        pages = pages.union(read_jsonl(sc, wiki + "/wiki_pages_f1").map(lambda p: (p["title"], (0, p))))
    return pages.reduceByKey(lambda a, b: a if a[0] <= b[0] else b).map(lambda kv: kv[1][1])


def wiki_page_keys(page: dict):
    title, wiki_type, box = page["title"], page["wiki_type"], page["infobox"]
    yield wiki_type, join_key(title), 0, title
    for field in ("name", "short_name", "constructor_name"):
        if box.get(field):
            yield wiki_type, join_key(FLAG_CODE_PREFIX.sub("", box[field])), 2, title
    if wiki_type == "team":
        short = TEAM_WORDS.sub(" ", join_key(title)).split()
        if short:
            yield wiki_type, " ".join(short), 3, title
            yield wiki_type, short[0], 4, title
    if wiki_type == "grand_prix":
        year = re.match(r"^\d{4}$", box.get("year", "")) or GP_TITLE_YEAR.match(title)
        race_no = re.match(r"^\d+", box.get("race_no", ""))
        if year and race_no and box.get("type", "").upper() in ("F1", "FORMULA ONE"):
            yield wiki_type, "round {} {}".format(year.group(0).strip(), int(race_no.group(0))), 0, title


def gp_circuit_locations(spark, keys_df, pages, extracted: str):
    sc = spark.sparkContext
    gps = grand_prix_rdd(sc, extracted).cache()
    best = best_matches(spark, gps, keys_df).rdd.map(lambda m: (m.wiki_title, m.entity_id))
    locations = pages.filter(lambda p: p["wiki_type"] == "grand_prix" and p["infobox"].get("location")) \
        .map(lambda p: (p["title"], join_key(p["infobox"]["location"].split(",")[0])))
    circuit_of = gps.map(lambda g: (g["entity_id"], join_key(g["circuit"])))
    return best.join(locations).map(lambda kv: kv[1]).join(circuit_of) \
        .map(lambda kv: (kv[1][1], kv[1][0])).filter(lambda kv: kv[0] and kv[1])


def wiki_keys_df(spark, pages, redirects):
    sc = spark.sparkContext
    page_types = sc.broadcast(dict(pages.map(lambda p: (p["title"], p["wiki_type"])).collect()))
    redirect_keys = redirects.filter(lambda r: r["to_title"] in page_types.value) \
        .map(lambda r: (page_types.value[r["to_title"]], join_key(r["from_title"]), 1, r["to_title"]))
    spans = sc.broadcast(dict(pages.filter(lambda p: p["wiki_type"] in CAREER_FIELDS).map(
        lambda p: (p["title"], career_span(p["wiki_type"], p["infobox"]))).collect()))
    keys = pages.flatMap(wiki_page_keys).union(redirect_keys).filter(lambda k: k[1]) \
        .map(lambda k: ((k[0], k[1], k[3] if k[0] in MULTI_PAGE_TYPES else ""), (k[2], k[3]))).reduceByKey(min) \
        .map(lambda kv: ((kv[0][0], kv[0][1]), kv[1])) \
        .groupByKey().flatMap(lambda kv: [Row(wiki_type=kv[0][0], key=kv[0][1], priority=p, wiki_title=t,
                                              span_from=spans.value.get(t, (0, 0))[0],
                                              span_to=spans.value.get(t, (0, 0))[1])
                                          for p, t in (kv[1] if kv[0][0] in MULTI_PAGE_TYPES else [min(kv[1])])])
    return spark.createDataFrame(
        keys, "wiki_type string, key string, priority int, wiki_title string, span_from int, span_to int")


def years_in(value: str) -> list:
    return [int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", value or "")]


def first_year(value: str) -> int:
    years = years_in(value)
    return years[0] if years else 0


def career_span(wiki_type: str, infobox: dict) -> tuple:
    if wiki_type == TEAM_TYPE:
        return first_year(infobox.get("debut", "")), 0
    years = [y for f in CAREER_FIELDS[wiki_type] for y in years_in(infobox.get(f, ""))]
    if years:
        return min(years), max(years)
    born, died = first_year(infobox.get("birth_date", "")), first_year(infobox.get("death_date", ""))
    return (born + MIN_RACING_AGE if born else 0), died


def season_gaps(seasons: list) -> list:
    seasons = sorted(set(seasons))
    return [(a, b) for a, b in zip(seasons, seasons[1:]) if b - a >= SEASON_GAP]



def select_attributes(entity_type: str, infobox: dict) -> dict:
    selected = {}
    for out_name, fields in WIKI_ATTRIBUTES[entity_type].items():
        value = next((infobox[f] for f in fields if infobox.get(f)), None)
        if value:
            selected[out_name] = value
    return selected


def best_matches(spark, entities, keys_df):
    candidates = entities.flatMap(lambda e: [Row(entity_type=e["entity_type"], entity_id=e["entity_id"],
                                                 rank=rank, key=key, first_season=e.get("first_season", 9999),
                                                 last_season=e.get("last_season", 9999))
                                             for rank, key in e["candidates"]])
    cand_df = spark.createDataFrame(
        candidates, "entity_type string, entity_id string, rank int, key string, first_season int, last_season int")
    before = (F.col("span_from") > 0) & (F.col("span_from") > F.col("last_season"))
    after = (F.col("span_to") > 0) & (F.col("first_season") < 9999) & (F.col("span_to") < F.col("first_season"))
    joined = cand_df.join(F.broadcast(keys_df),
                          (cand_df.entity_type == keys_df.wiki_type) & (cand_df.key == keys_df.key), "inner") \
        .withColumn("era", F.when(before | after, 1).otherwise(0)) \
        .withColumn("qualified", F.when(F.col("wiki_title").contains("("), 1).otherwise(0))
    best = joined.groupBy("entity_type", "entity_id").agg(
        F.min(F.struct("rank", "era", "priority", "qualified", "wiki_title",
                       cand_df.key.alias("matched_key"))).alias("m"))
    return best.select("entity_type", "entity_id", "m.rank", "m.priority", "m.wiki_title", "m.matched_key")


def match_rule(rank: int, priority: int) -> str:
    source = {0: "title", 1: "redirect", 2: "infobox name", 3: "title without generic words",
              4: "first word of title"}[priority]
    return "{} pass, {}".format("first" if rank == 0 else "second (rule {})".format(rank), source)


def unmatched_reason(entity: dict, any_type_keys: dict) -> str:
    for _, key in entity["candidates"]:
        other = any_type_keys.get(key)
        if other:
            return "ключ '{}' найден только у wiki-страницы типа {} ({})".format(key, other[0], other[1])
    if not entity["candidates"]:
        return "нет ключа (нестандартное официальное название и нет номера этапа)"
    return "ни один ключ ({}) не найден среди title/redirect".format(", ".join(k for _, k in entity["candidates"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Join formula1.com entities with Wikipedia pages")
    parser.add_argument("extracted", help="output of spark_extractor.py (or old extracted/)")
    parser.add_argument("wiki", help="output of wiki_parser.py (wiki_pages/, redirects/)")
    parser.add_argument("output", help="output directory: <entity>/, join_report.json, join_report.md")
    parser.add_argument("--label", default="", help="note for the report, e.g. 'sample, full dump pending'")
    args = parser.parse_args()
    out = args.output.rstrip("/")

    spark = get_spark("f1-wiki-join")
    sc = spark.sparkContext
    pages = read_wiki_pages(sc, args.wiki.rstrip("/")).cache()
    redirects = read_jsonl(sc, args.wiki.rstrip("/") + "/redirects")
    keys_df = wiki_keys_df(spark, pages, redirects).cache()

    gp_locations = gp_circuit_locations(spark, keys_df, pages, args.extracted)
    entities = sc.union([drivers_rdd(sc, args.extracted), grand_prix_rdd(sc, args.extracted),
                         circuits_rdd(sc, args.extracted, gp_locations), teams_rdd(sc, args.extracted)]).cache()
    matches = best_matches(spark, entities, keys_df)

    match_rdd = matches.rdd.map(lambda m: ((m.entity_type, m.entity_id), m.asDict()))
    pages_by_title = pages.map(lambda p: (p["title"], p))
    joined = entities.map(lambda e: ((e["entity_type"], e["entity_id"]), e)).leftOuterJoin(match_rdd) \
        .map(lambda kv: (kv[1][1]["wiki_title"] if kv[1][1] else None, kv[1])) \
        .leftOuterJoin(pages_by_title) \
        .map(lambda kv: output_row(kv[1][0][0], kv[1][0][1], kv[1][1])).cache()

    for entity_type in ENTITY_TYPES:
        write_jsonl(joined.filter(lambda r, t=entity_type: r["entity_type"] == t), "{}/{}".format(out, entity_type))

    unmatched_ids = set(joined.filter(lambda r: "wiki_title" not in r)
                        .map(lambda r: (r["entity_type"], r["entity_id"])).collect())
    wanted = set(entities.filter(lambda e: (e["entity_type"], e["entity_id"]) in unmatched_ids)
                 .flatMap(lambda e: [k for _, k in e["candidates"]]).collect())
    any_type_keys = dict(keys_df.rdd.filter(lambda k: k.key in wanted)
                         .map(lambda k: (k.key, (k.wiki_type, k.wiki_title))).collect())
    report = build_report(joined, entities, any_type_keys, pages, args)
    write_text(sc, out + "/join_report.json", json.dumps(report, ensure_ascii=False, indent=2))
    spark.stop()


def output_row(entity: dict, match, page) -> dict:
    row = {k: v for k, v in entity.items() if k != "candidates" and v not in ("", [], None)}
    if match and page:
        row["wiki_title"] = match["wiki_title"]
        row["match_rule"] = match_rule(match["rank"], match["priority"])
        row["matched_key"] = match["matched_key"]
        row["wiki"] = select_attributes(entity["entity_type"], page["infobox"])
        if page.get("abstract"):
            row["abstract"] = page["abstract"]
    return row



def build_report(joined, entities, any_type_keys: dict, pages, args) -> dict:
    stats = dict(joined.map(lambda r: (r["entity_type"], (1, 1 if "wiki_title" in r else 0, 1 if r.get(
        "match_rule", "").startswith("second") else 0))).reduceByKey(lambda a, b: tuple(x + y for x, y in zip(a, b)))
        .collect())
    unique = dict(joined.filter(lambda r: "wiki_title" in r).map(lambda r: (r["entity_type"], r["wiki_title"]))
                  .distinct().countByKey())
    wiki_types = dict(pages.map(lambda p: p["wiki_type"]).countByValue())
    by_type = {}
    for t in ENTITY_TYPES:
        total, matched, second = stats.get(t, (0, 0, 0))
        by_type[t] = {"entities": total, "matched": matched, "matched_second_pass": second,
                      "coverage_pct": round(100.0 * matched / total, 1) if total else 0.0,
                      "unique_wiki_pages": unique.get(t, 0), "wiki_pages_of_type": wiki_types.get(t, 0)}
    total = {k: sum(v[k] for v in by_type.values())
             for k in ("entities", "matched", "matched_second_pass", "unique_wiki_pages")}
    total["coverage_pct"] = round(100.0 * total["matched"] / total["entities"], 1) if total["entities"] else 0.0

    good = joined.filter(lambda r: "wiki_title" in r and r.get("wiki")) \
        .map(lambda r: (r["entity_type"], r)).groupByKey() \
        .flatMap(lambda kv: sorted(kv[1], key=lambda r: (-len(r["wiki"]), r["entity_id"]))[:2]).collect()
    good = sorted(good, key=lambda r: (ENTITY_TYPES.index(r["entity_type"]), r["entity_id"]))[:5]
    bad_entities = entities.map(lambda e: ((e["entity_type"], e["entity_id"]), e)) \
        .join(joined.filter(lambda r: "wiki_title" not in r).map(lambda r: ((r["entity_type"], r["entity_id"]), 1))) \
        .map(lambda kv: kv[1][0]).groupBy(lambda e: e["entity_type"]) \
        .flatMap(lambda kv: sorted(kv[1], key=lambda e: (-e.get("entries", 1), e["entity_id"]))[:3]).collect()
    bad = sorted(bad_entities, key=lambda e: (ENTITY_TYPES.index(e["entity_type"]), -e.get("entries", 1)))[:10]

    return {
        "label": args.label, "extracted": args.extracted, "wiki": args.wiki,
        "by_type": by_type, "total": total,
        "matched_examples": [{"entity_type": r["entity_type"], "name": r["name"], "wiki_title": r["wiki_title"],
                              "match_rule": r["match_rule"], "wiki": r["wiki"]} for r in good],
        "unmatched_examples": [{"entity_type": e["entity_type"], "name": e["name"],
                                "keys": [k for _, k in e["candidates"]],
                                "reason": unmatched_reason(e, any_type_keys)} for e in bad],
        "attributes": {t: {"fields": list(WIKI_ATTRIBUTES[t])} for t in ENTITY_TYPES},
        "possible_merged_drivers": possible_merged_drivers(joined),
    }


def possible_merged_drivers(joined) -> list:
    rows = joined.filter(lambda r: r["entity_type"] == "driver" and season_gaps(r.get("seasons", []))).collect()
    return [{"name": r["name"], "first_season": r["first_season"], "last_season": r["last_season"],
             "season_gaps": season_gaps(r["seasons"]), "slugs": r.get("slugs", []), "codes": r.get("codes", []),
             "wiki_title": r.get("wiki_title"),
             "note": "разрыв в сезонах >= {} лет: возможно, два пилота с одним именем; formula1.com не различает "
                     "их (тот же slug и код), поэтому сущность не делится".format(SEASON_GAP)}
            for r in sorted(rows, key=lambda r: r["name"])]




if __name__ == "__main__":
    main()
