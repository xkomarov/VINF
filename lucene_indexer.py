from __future__ import annotations

import os
import sys
import json
import glob
import time
import shutil
import argparse

import lucene_schema as schema

from org.apache.lucene.document import (Document, Field, StringField, TextField, StoredField, IntPoint,
                                        NumericDocValuesField)
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
from org.apache.lucene.store import FSDirectory
from java.nio.file import Paths


def read_documents(path: str):
    files = sorted(glob.glob(os.path.join(path, "part-*"))) if os.path.isdir(path) else [path]
    for name in files:
        with open(name, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def to_lucene(doc: dict) -> Document:
    out = Document()
    has_wiki = "true" if doc.get("wiki_title") else "false"
    for field in schema.KEYWORD_FIELDS:
        value = has_wiki if field == "has_wiki" else doc.get(field)
        if value:
            out.add(StringField(field, str(value), Field.Store.NO if field == "has_wiki" else Field.Store.YES))
    for field in schema.TEXT_FIELDS:
        if doc.get(field):
            out.add(TextField(field, str(doc[field]), Field.Store.YES))
    for field in schema.INT_FIELDS:
        value = doc.get(field)
        if isinstance(value, int):
            out.add(IntPoint(field, value))
            out.add(StoredField(field, value))
            if field in schema.SORT_FIELDS:
                out.add(NumericDocValuesField(field, value))
    for field in schema.STORED_JSON_FIELDS:
        if doc.get(field):
            out.add(StoredField(field, json.dumps(doc[field], ensure_ascii=False)))
    for field in schema.STORED_ONLY_FIELDS:
        if doc.get(field):
            out.add(StoredField(field, doc[field]))
    return out


def dir_size(path: str) -> int:
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)


def build(source: str, target: str, similarity: str = "bm25") -> dict:
    start = time.perf_counter()
    if os.path.exists(target):
        shutil.rmtree(target)
    config = IndexWriterConfig(schema.analyzer())
    config.setSimilarity(schema.similarity(similarity))
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    writer = IndexWriter(FSDirectory.open(Paths.get(os.path.abspath(target))), config)
    count = 0
    for doc in read_documents(source):
        writer.addDocument(to_lucene(doc))
        count += 1
    writer.forceMerge(1)
    writer.commit()
    writer.close()
    stats = {"documents": count, "seconds": round(time.perf_counter() - start, 2),
             "bytes": dir_size(target), "path": target}
    return stats


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the Lucene index from out/documents (spark_documents.py)")
    parser.add_argument("source", nargs="?", default=os.path.join(schema.BASE_DIR, "out", "documents"))
    parser.add_argument("target", nargs="?", default=schema.LUCENE_DIR)
    args = parser.parse_args()
    stats = build(args.source, args.target)
    print("{documents} documents indexed in {seconds} s, {bytes} bytes -> {path}".format(**stats))


if __name__ == "__main__":
    main()
