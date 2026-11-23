
# "winner:\"ayrton senna\" AND season:[1985 TO 1990]"        
# "raikonen~2" --field title --sort season
# "world champion ferrari" --engine old-bm25                 
# "ferrari AND (monaco OR monza)" --engine all


from __future__ import annotations

import os
import re
import sys
import json
import time
import argparse
from collections import Counter

import indexer

LUCENE_ENGINES = {"l-bm25": "bm25", "l-tf": "classic"}
OLD_ENGINES = ("old-bm25", "old-tf")
ENGINES = tuple(LUCENE_ENGINES) + OLD_ENGINES


class Unsupported(Exception):
    pass


class Result:
    def __init__(self, hits: list, total: int, ms: float, query: str):
        self.hits, self.total, self.ms, self.query = hits, total, ms, query

    @property
    def ids(self) -> list:
        return [doc["id"] for _, doc in self.hits]


UNSUPPORTED = [
    (re.compile(r'"'), "phrase query: the old index stores no term positions"),
    (re.compile(r"~"), "fuzzy query: the old index has no term dictionary walk / edit distance"),
    (re.compile(r"[\[\]{}]|\bTO\b"), "range query: the old index has no numeric fields"),
    (re.compile(r"[*?]"), "wildcard query: the old index has no sorted term dictionary"),
    (re.compile(r"\b\w+:"), "field query: the old index has one field (title x3 + text)"),
    (re.compile(r"\^"), "boost: not supported by the old query syntax"),
]
BOOL_TOKEN = re.compile(r"\(|\)|[^\s()]+")


class OldEngine:
    def __init__(self, method: str):
        self.method = method.split("-", 1)[1]
        self.searcher = indexer.Searcher()
        self.all_docs = set(range(len(self.searcher.docs)))

    @staticmethod
    def check(query: str) -> None:
        for pattern, reason in UNSUPPORTED:
            if pattern.search(query):
                raise Unsupported(reason)

    def postings(self, word: str) -> tuple[set, list]:
        terms = indexer.tokenize(word)
        if not terms:
            return None, []
        docs = set()
        for term in terms:
            docs |= {d for d, _ in self.searcher.index["postings"].get(term, [])}
        return docs, terms

    def boolean(self, query: str) -> tuple[set, list]:
        tokens = BOOL_TOKEN.findall(query)
        pos = 0

        def peek():
            return tokens[pos] if pos < len(tokens) else None

        def take():
            nonlocal pos
            pos += 1
            return tokens[pos - 1]

        def combine(a, b, op):
            if a is None:
                return b
            if b is None:
                return a
            return a & b if op == "AND" else a | b

        def factor():
            tok = take()
            if tok == "NOT":
                docs, _ = factor()
                return (self.all_docs - docs if docs is not None else None), []
            if tok == "(":
                result = expr()
                if peek() == ")":
                    take()
                return result
            return self.postings(tok)

        def and_expr():
            docs, terms = factor()
            while peek() in ("AND", "NOT"):
                if take() == "AND":
                    right, more = factor()
                    docs, terms = combine(docs, right, "AND"), terms + more
                else:
                    right, _ = factor()
                    docs = docs - right if docs is not None and right is not None else docs
            return docs, terms

        def expr():
            docs, terms = and_expr()
            while peek() not in (None, ")"):
                if peek() == "OR":
                    take()
                right, more = and_expr()
                docs, terms = combine(docs, right, "OR"), terms + more
            return docs, terms

        docs, terms = expr()
        return (docs if docs is not None else set()), terms

    def search(self, query: str, top: int = 10, field: str | None = None, sort: str | None = None) -> Result:
        self.check(query)
        if field:
            raise Unsupported("--field: the old index has one field")
        if sort:
            raise Unsupported("--sort: the old index stores no sortable values")
        start = time.perf_counter()
        if not re.search(r"\b(AND|OR|NOT)\b|[()]", query):
            scores = self.score(Counter(indexer.tokenize(query)))
            total = len(scores)
        else:
            allowed, terms = self.boolean(query)
            scores = self.score(Counter(terms))
            scores = {d: scores.get(d, 0.0) for d in allowed}
            total = len(allowed)
        best = sorted(scores.items(), key=lambda item: -item[1])[:top]
        hits = [(score, self.searcher.docs[d]) for d, score in best]
        return Result(hits, total, (time.perf_counter() - start) * 1000, query)

    def score(self, terms: Counter) -> dict:
        if self.method == "bm25":
            return self.searcher.score_bm25(terms)
        return self.searcher.score_tfidf(terms)


class LuceneEngine:
    def __init__(self, index_dir: str | None = None, similarity: str = "bm25"):
        import lucene_schema as schema
        from java.nio.file import Paths
        from org.apache.lucene.index import DirectoryReader
        from org.apache.lucene.search import IndexSearcher
        from org.apache.lucene.store import FSDirectory
        self.schema = schema
        self.reader = DirectoryReader.open(FSDirectory.open(Paths.get(os.path.abspath(index_dir or schema.LUCENE_DIR))))
        self.searcher = IndexSearcher(self.reader)
        self.searcher.setSimilarity(schema.similarity(similarity))
        self.analyzer = schema.analyzer()
        self.parsers = {}

    def parse(self, query: str, field: str | None = None):
        key = field or ""
        if key not in self.parsers:
            self.parsers[key] = self.schema.query_parser(self.analyzer, [field] if field else None)
        return self.parsers[key].parse(query, None)

    def search(self, query: str, top: int = 10, field: str | None = None, sort: str | None = None) -> Result:
        from org.apache.lucene.search import Sort, SortField
        self.schema.init_vm()
        start = time.perf_counter()
        parsed = self.parse(query, field)
        if sort:
            name, desc = sort.rstrip("-"), sort.endswith("-")
            if name not in self.schema.SORT_FIELDS:
                raise ValueError("sortable fields: " + ", ".join(self.schema.SORT_FIELDS))
            top_docs = self.searcher.search(parsed, top, Sort(SortField(name, SortField.Type.INT, desc)), True)
        else:
            top_docs = self.searcher.search(parsed, top)
        total = self.searcher.count(parsed)
        stored = self.searcher.storedFields()
        hits = [(sd.score, self.document(stored.document(sd.doc))) for sd in top_docs.scoreDocs]
        ms = (time.perf_counter() - start) * 1000
        return Result(hits, total, ms, parsed.toString())

    def document(self, doc) -> dict:
        out = {"id": doc.get("id"), "type": doc.get("type"), "title": doc.get("title")}
        for field in self.schema.STORED_JSON_FIELDS:
            if doc.get(field):
                out[field] = json.loads(doc.get(field))
        for field in self.schema.INT_FIELDS:
            value = doc.getField(field)
            if value is not None:
                out.setdefault("fields", {})[field] = value.numericValue().intValue()
        if doc.get("wiki_title"):
            out["wiki_title"] = doc.get("wiki_title")
        return out


def make_engine(name: str, index_dir: str | None = None):
    if name in LUCENE_ENGINES:
        return LuceneEngine(index_dir, LUCENE_ENGINES[name])
    return OldEngine(name)


def print_result(label: str, result: Result, rows: int) -> None:
    print("\n--- {} --- {} hits, {:.2f} ms".format(label, result.total, result.ms))
    if label in LUCENE_ENGINES:
        print("    parsed: " + result.query)
    if not result.hits:
        print("no results")
    for rank, (score, doc) in enumerate(result.hits, 1):
        extra = ""
        if doc.get("fields"):
            extra = "  {" + ", ".join("{}={}".format(k, v) for k, v in doc["fields"].items()) + "}"
        if doc.get("wiki_title"):
            extra += "  wiki: " + doc["wiki_title"]
        print("{:2}. {:8.4f}  [{}] {}  ({}){}".format(rank, score, doc["type"], doc["title"], doc["id"], extra))
        if rows and doc.get("results") and doc["type"] in indexer.RESULT_HEADERS:
            indexer.print_table(indexer.RESULT_HEADERS[doc["type"]], doc["results"], rows)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Search the F1 indexes (Lucene or the old TF-IDF/BM25 index)",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("query", nargs="?", help="query; interactive prompt without it")
    parser.add_argument("-e", "--engine", choices=ENGINES + ("all",), default="l-bm25",
                        help="l-bm25 / l-tf = Lucene with BM25 / ClassicSimilarity (TF-IDF), "
                             "old-bm25 / old-tf = old index with BM25 / TF-IDF, all = all four")
    parser.add_argument("-f", "--field", help="default field (Lucene only); default: title^3 text wiki_text")
    parser.add_argument("--sort", help="sort by a numeric field instead of score: season (asc) or season- (desc)")
    parser.add_argument("-n", "--top", type=int, default=10)
    parser.add_argument("-r", "--rows", type=int, default=0, help="result table rows per document")
    parser.add_argument("--index", help="Lucene index directory (default lucene_index/)")
    args = parser.parse_args()

    names = ENGINES if args.engine == "all" else (args.engine,)
    engines = {n: make_engine(n, args.index) for n in names}
    queries = [args.query] if args.query else iter(lambda: input("\nquery> ").strip(), "")
    for q in queries:
        for label, engine in engines.items():
            try:
                print_result(label, engine.search(q, args.top, args.field, args.sort), args.rows)
            except Unsupported as e:
                print("\n--- {} --- not supported: {}".format(label, e))
            except Exception as e:
                print("\n--- {} --- error: {}".format(label, getattr(e, "getMessage", lambda: e)()))


if __name__ == "__main__":
    main()
