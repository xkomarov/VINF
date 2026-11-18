from __future__ import annotations

import json
import argparse

from pyspark import StorageLevel

import extractor
from spark_common import get_spark, write_jsonl, path_exists


def page_rows(item):
    path, page = item
    filename = path.rsplit("/", 1)[-1]
    for table, rows in extractor.extract_page(filename, page).items():
        for row in rows:
            yield table, row


def typed_row(row: dict) -> dict:
    return {k: extractor.typed(v) for k, v in row.items() if v not in ("", [])}


def merge_rows(a: dict, b: dict) -> dict:
    merged = dict(a)
    merged.update({k: v for k, v in b.items() if v})
    return merged


def canonical(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def compare(sc, tables: dict, counts: dict, reference_dir: str) -> dict:
    result = {}
    for table in sorted(counts):
        path = "{}/{}.jsonl".format(reference_dir.rstrip("/"), table)
        if not path_exists(sc, path):
            result[table] = {"spark": counts[table], "reference": None, "same_rows": False}
            continue
        reference = sc.textFile(path).map(lambda line: canonical(json.loads(line)))
        ours = tables[table].map(canonical)
        missing = reference.subtract(ours).count()
        extra = ours.subtract(reference).count()
        result[table] = {"spark": counts[table], "reference": reference.count(),
                         "same_rows": missing == 0 and extra == 0, "missing": missing, "extra": extra}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract formula1.com tables with Spark")
    parser.add_argument("input", help="directory with crawled *.html (local path or hdfs://)")
    parser.add_argument("output", help="output directory, one subdirectory per table")
    parser.add_argument("--min-partitions", type=int, default=64)
    parser.add_argument("--compare", metavar="DIR", help="extracted/ of the old extractor.py to compare with")
    args = parser.parse_args()

    spark = get_spark("f1-extractor")
    sc = spark.sparkContext
    pages = sc.wholeTextFiles(args.input.rstrip("/") + "/*.html", minPartitions=args.min_partitions)
    pairs = pages.flatMap(page_rows).persist(StorageLevel.MEMORY_AND_DISK)

    names = sorted(pairs.keys().distinct().collect())
    tables = {}
    for name in names:
        rows = pairs.filter(lambda kv, name=name: kv[0] == name).values()
        if name == "grand_prix":
            rows = rows.keyBy(lambda r: (r["season"], r["race_id"])).reduceByKey(merge_rows).values() \
                       .sortBy(lambda r: (int(r["season"]), int(r["race_id"])))
        tables[name] = rows.map(typed_row).persist(StorageLevel.MEMORY_AND_DISK)

    counts = {}
    for name in names:
        write_jsonl(tables[name], "{}/{}".format(args.output.rstrip("/"), name))
        counts[name] = tables[name].count()
        print("{:<24} {:>8} rows".format(name, counts[name]))
    print("{:<24} {:>8} rows".format("TOTAL", sum(counts.values())))

    if args.compare:
        print("\nComparison with", args.compare)
        result = compare(sc, tables, counts, args.compare)
        for name, r in result.items():
            status = "OK" if r["same_rows"] else "DIFF"
            print("{:<24} spark {:>8}  reference {:>8}  {}".format(name, r["spark"], str(r["reference"]), status))
        ok = all(r["same_rows"] for r in result.values())
        print("ALL TABLES IDENTICAL" if ok else "SOME TABLES DIFFER")
    spark.stop()


if __name__ == "__main__":
    main()
