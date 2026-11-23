import os
import json

import pytest

pytest.importorskip("lucene")

import lucene_schema as schema
import lucene_indexer
import search

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "documents_sample.jsonl")
OLD_INDEX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "index", "index.json")


@pytest.fixture(scope="module")
def docs():
    with open(FIXTURE, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="module")
def engine(tmp_path_factory, docs):
    target = str(tmp_path_factory.mktemp("lucene"))
    stats = lucene_indexer.build(FIXTURE, target)
    assert stats["documents"] == len(docs)
    return search.LuceneEngine(target)


def ids(engine, query, field=None, top=100, sort=None):
    return engine.search(query, top, field, sort).ids


def tokens(text, field="title"):
    from org.apache.lucene.analysis.tokenattributes import CharTermAttribute
    stream = schema.analyzer().tokenStream(field, text)
    term = stream.addAttribute(CharTermAttribute.class_)
    stream.reset()
    out = []
    while stream.incrementToken():
        out.append(term.toString())
    stream.end()
    stream.close()
    return out


def test_index_has_all_documents(engine, docs):
    assert engine.reader.numDocs() == len(docs)
    assert engine.search("has_wiki:true", 100).total == sum(1 for d in docs if d.get("wiki_title"))


def test_type_values_are_fixed(engine, docs):
    assert {d["type"] for d in docs} <= set(schema.DOC_TYPES)
    for t in schema.DOC_TYPES:
        assert engine.search("type:" + t, 100).total == sum(1 for d in docs if d["type"] == t)


def test_text_analyzer_folds_lowercases_and_drops_stopwords():
    assert tokens("Kimi Räikkönen and the McLaren") == ["kimi", "raikkonen", "mclaren"]


def test_keyword_fields_are_not_analyzed():
    assert tokens("Red-Bull-Racing Honda", "id") == ["Red-Bull-Racing Honda"]


def test_normalize_for_multiterm_queries():
    assert schema.analyzer().normalize("title", "RÄIKKÖNEN").utf8ToString() == "raikkonen"


def test_fuzzy_and_wildcard_terms_are_normalized(engine):
    kimi = "driver/2007/kimi-r-ikk-nen"
    result = engine.search("räikkönen~1", 10, "title")
    assert "raikkonen~1" in result.query
    assert kimi in result.ids
    assert kimi in ids(engine, "RÄIKK*", "title")


def test_boolean_and_or_not(engine):
    senna = set(ids(engine, "senna"))
    monaco = set(ids(engine, "monaco"))
    assert set(ids(engine, "senna AND monaco")) == senna & monaco
    assert set(ids(engine, "senna OR monaco")) == senna | monaco
    assert set(ids(engine, "senna NOT monaco")) == senna - monaco
    assert "gp/1988/529" in senna & monaco


def test_phrase_distinguishes_word_order(engine):
    phrase = set(ids(engine, 'team:"red bull racing"'))
    words = set(ids(engine, "team:(red bull racing)"))
    assert {"team/2011/Red-Bull-Racing-Renault", "team/2023/Red-Bull-Racing-Honda-RBPT"} <= phrase
    assert "team/2025/Racing-Bulls-Honda-RBPT" not in phrase
    assert "team/2025/Racing-Bulls-Honda-RBPT" in words


def test_fuzzy_on_a_field(engine):
    assert {"gp/2008/833", "gp/2014/906"} <= set(ids(engine, "winner:hamiltn~1"))
    assert ids(engine, "winner:hamiltn") == []


def test_wildcard(engine):
    assert "driver/1972/emerson-fittipaldi" in ids(engine, "title:fittip*")


def test_range_on_season_and_wiki_field(engine, docs):
    fifties = {d["id"] for d in docs if 1950 <= d.get("season", 0) <= 1959}
    assert set(ids(engine, "season:[1950 TO 1959]")) == fifties
    long_ = {d["id"] for d in docs if d.get("length_m", 0) >= 6000}
    assert set(ids(engine, "length_m:[6000 TO *]")) == long_
    assert "circuit/ain-diab-circuit" in long_


def test_field_option_changes_default_field(engine):
    default = set(ids(engine, "hamilton"))
    winner = set(ids(engine, "hamilton", field="winner"))
    assert "driver/2014/lewis-hamilton" in default
    assert winner == {"gp/2008/833", "gp/2014/906"}
    assert winner < default


def test_sort_by_season(engine):
    asc = engine.search("type:grand_prix", 100, sort="season").hits
    desc = engine.search("type:grand_prix", 100, sort="season-").hits
    seasons = [d["fields"]["season"] for _, d in asc]
    assert seasons == sorted(seasons)
    assert [d["fields"]["season"] for _, d in desc] == sorted(seasons, reverse=True)


def test_keyword_fields_through_parser(engine):
    assert "driver/2014/lewis-hamilton" in ids(engine, "driver_code:HAM")
    assert {"driver/1988/ayrton-senna", "driver/1972/emerson-fittipaldi"} <= set(ids(engine, "nationality:BRA"))
    assert ids(engine, 'id:"gp/1988/529"') == ["gp/1988/529"]
    assert "driver/1988/ayrton-senna" in ids(engine, 'wiki_title:"Ayrton Senna"')
    assert ids(engine, "nationality:bra") == []


@pytest.mark.parametrize("field", schema.INT_FIELDS)
def test_every_numeric_field_point_and_range(engine, docs, field):
    doc = next(d for d in docs if isinstance(d.get(field), int))
    value = doc[field]
    assert doc["id"] in ids(engine, "{}:{}".format(field, value))
    assert doc["id"] in ids(engine, "{}:[{} TO {}]".format(field, value - 1, value + 1))
    assert doc["id"] not in ids(engine, "{}:[{} TO *]".format(field, value + 1))


@pytest.fixture(scope="module")
def old():
    if not os.path.exists(OLD_INDEX):
        pytest.skip("old index/ not built")
    return search.OldEngine("old-bm25")


def postings(old, word):
    return {d for d, _ in old.searcher.index["postings"].get(word, [])}


def test_old_engine_keywords_equal_first_submission(old):
    expected = [doc["id"] for _, doc in old.searcher.search("max verstappen", "bm25", 5)]
    assert old.search("max verstappen", 5).ids == expected


def test_old_engine_boolean_is_set_algebra(old):
    senna, monaco = postings(old, "senna"), postings(old, "monaco")
    assert old.search("senna AND monaco", 10000).total == len(senna & monaco)
    assert old.search("senna OR monaco", 10000).total == len(senna | monaco)
    assert old.search("senna NOT monaco", 10000).total == len(senna - monaco)


@pytest.mark.parametrize("query", ['"red bull racing"', "raikonen~2", "season:[1950 TO 1959]", "fittip*",
                                   "winner:senna"])
def test_old_engine_reports_unsupported(old, query):
    with pytest.raises(search.Unsupported):
        old.search(query)
