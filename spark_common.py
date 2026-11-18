from __future__ import annotations

import json

from pyspark.sql import SparkSession


def get_spark(app_name: str) -> SparkSession:
    return SparkSession.builder.appName(app_name).getOrCreate()


def hadoop_path(sc, path: str):
    jvm = sc._jvm
    hpath = jvm.org.apache.hadoop.fs.Path(path)
    return hpath.getFileSystem(sc._jsc.hadoopConfiguration()), hpath


def remove_dir(sc, path: str) -> None:
    fs, hpath = hadoop_path(sc, path)
    if fs.exists(hpath):
        fs.delete(hpath, True)


def path_exists(sc, path: str) -> bool:
    fs, hpath = hadoop_path(sc, path)
    return fs.exists(hpath)


def write_jsonl(rdd, path: str, partitions: int = 1) -> None:
    remove_dir(rdd.context, path)
    rdd.map(lambda row: json.dumps(row, ensure_ascii=False)) \
       .coalesce(partitions).saveAsTextFile(path)


def read_jsonl(sc, path: str):
    return sc.textFile(path).map(json.loads)


def write_text(sc, path: str, content: str) -> None:
    fs, hpath = hadoop_path(sc, path)
    stream = fs.create(hpath, True)
    stream.write(bytearray(content.encode("utf-8")))
    stream.close()
