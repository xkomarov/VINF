from __future__ import annotations

import json
import argparse
from collections import Counter

from pyspark import StorageLevel

import wiki_regex
from spark_common import get_spark, write_jsonl, write_text


def read_pages(sc, path: str, min_partitions: int):
    records = sc.newAPIHadoopFile(
        path,
        "org.apache.hadoop.mapreduce.lib.input.TextInputFormat",
        "org.apache.hadoop.io.LongWritable",
        "org.apache.hadoop.io.Text",
        conf={"textinputformat.record.delimiter": "</page>"},
    ).map(lambda kv: kv[1])
    if records.getNumPartitions() < min_partitions:
        records = records.repartition(min_partitions)
    return records.map(wiki_regex.parse_page).filter(lambda p: p is not None)


def page_outputs(page: dict):
    if page["ns"] != 0:
        return []
    if page["redirect"]:
        return [("redirect", {"from_title": page["title"], "to_title": page["redirect"]})]
    outputs = []
    row = wiki_regex.parse_infobox_page(page)
    if row:
        outputs.append(("page", row))
    if wiki_regex.is_f1_article(page["text"]):
        outputs.extend(("census", name) for name in set(wiki_regex.infobox_names(page["text"])))
        outputs.append(("f1_raw", {"title": page["title"], "text": page["text"]}))
    return outputs


def raw_pages(sc, path: str):
    return sc.textFile(path).map(json.loads).map(lambda p: dict(p, ns=0, redirect=None))


def census(pages) -> list:
    return pages.filter(lambda p: p["ns"] == 0 and not p["redirect"] and wiki_regex.is_f1_article(p["text"])) \
                .flatMap(lambda p: set(wiki_regex.infobox_names(p["text"]))) \
                .map(lambda name: (name, 1)).reduceByKey(lambda a, b: a + b) \
                .sortBy(lambda kv: -kv[1]).collect()


def print_census(sc, out: str, counts: list) -> None:
    known = set(wiki_regex.INFOBOX_TYPES) | set(wiki_regex.F1_ONLY_TYPES)
    print("\n".join("{:>7}  {:<45} {}".format(n, name, "known" if name in known else "")
                    for name, n in counts[:80]))
    write_text(sc, out + "/infobox_census.json",
               json.dumps([{"template": name, "f1_pages": n, "known": name in known} for name, n in counts],
                          ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a Wikipedia XML dump with Spark)")
    parser.add_argument("input", help="enwiki-*-pages-articles*.xml[.bz2] (local path or hdfs://)")
    parser.add_argument("output", help="output directory: wiki_pages/, redirects/, wiki_stats.json")
    parser.add_argument("--min-partitions", type=int, default=8)
    parser.add_argument("--census", action="store_true", help="only count Infobox templates on F1 pages")
    parser.add_argument("--from-raw", action="store_true",
                        help="input is f1_raw/ of a previous run: re-parse only F1 pages into wiki_pages_f1/")
    args = parser.parse_args()
    out = args.output.rstrip("/")

    spark = get_spark("f1-wiki-parser")
    sc = spark.sparkContext

    if args.from_raw:
        rows = raw_pages(sc, args.input).map(wiki_regex.parse_infobox_page).filter(lambda r: r is not None)
        write_jsonl(rows, out + "/wiki_pages_f1", partitions=1)
        print(dict(rows.map(lambda r: r["wiki_type"]).countByValue()))
        spark.stop()
        return

    pages = read_pages(sc, args.input, args.min_partitions)
    if args.census:
        print_census(sc, out, census(pages))
        spark.stop()
        return

    outputs = pages.flatMap(page_outputs).persist(StorageLevel.MEMORY_AND_DISK)
    print("outputs:", outputs.count())
    infobox_pages = outputs.filter(lambda kv: kv[0] == "page").values()
    redirects = outputs.filter(lambda kv: kv[0] == "redirect").values()

    write_jsonl(infobox_pages, out + "/wiki_pages", partitions=4)
    write_jsonl(redirects, out + "/redirects", partitions=16)
    write_jsonl(outputs.filter(lambda kv: kv[0] == "f1_raw").values(), out + "/f1_raw", partitions=8)
    print_census(sc, out, outputs.filter(lambda kv: kv[0] == "census").map(lambda kv: (kv[1], 1))
                 .reduceByKey(lambda a, b: a + b).sortBy(lambda kv: -kv[1]).collect())

    types = Counter(dict(infobox_pages.map(lambda r: (r["wiki_type"], 1)).reduceByKey(lambda a, b: a + b).collect()))
    templates = dict(infobox_pages.map(lambda r: (r["template"], 1)).reduceByKey(lambda a, b: a + b).collect())
    stats = {"input": args.input, "infobox_pages": sum(types.values()), "by_type": dict(sorted(types.items())),
             "by_template": dict(sorted(templates.items(), key=lambda kv: -kv[1])),
             "redirects": redirects.count()}
    write_text(sc, out + "/wiki_stats.json", json.dumps(stats, ensure_ascii=False, indent=2))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    spark.stop()


if __name__ == "__main__":
    main()
