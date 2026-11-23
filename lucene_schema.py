from __future__ import annotations

import os

import lucene

import indexer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LUCENE_DIR = os.path.join(BASE_DIR, "lucene_index")
STOPWORDS_FILE = "lucene_stopwords.txt"

_vm = None


def init_vm():
    global _vm
    if _vm is None:
        _vm = lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    else:
        _vm.attachCurrentThread()
    return _vm


init_vm()

from java.nio.file import Paths
from java.util import HashMap, Locale
from java.text import NumberFormat
from java.lang import Integer, Float, CharSequence
from org.apache.lucene.analysis.custom import CustomAnalyzer
from org.apache.lucene.analysis.core import KeywordAnalyzer
from org.apache.lucene.analysis.miscellaneous import PerFieldAnalyzerWrapper
from org.apache.lucene.queryparser.flexible.standard import StandardQueryParser
from org.apache.lucene.queryparser.flexible.standard.config import (
    PointsConfig, StandardQueryConfigHandler)
from org.apache.lucene.search.similarities import BM25Similarity, ClassicSimilarity


DOC_TYPES = ("grand_prix", "circuit", "driver_season", "team_season", "driver", "team")

KEYWORD_FIELDS = ("id", "type", "driver_code", "nationality", "wiki_title", "has_wiki")
TEXT_FIELDS = ("title", "text", "wiki_text", "winner", "winner_team", "pole", "fastest_lap", "team", "circuit",
               "location", "birth_place", "weather")
INT_FIELDS = ("season", "position", "round", "birth_year", "wins", "poles", "championships",
              "attendance", "length_m", "turns", "opened_year")
SORT_FIELDS = ("season",)
STORED_JSON_FIELDS = ("results", "stats")
STORED_ONLY_FIELDS = ("abstract",)

DEFAULT_FIELDS = ("title", "text", "wiki_text")
FIELD_BOOSTS = {"title": 3.0}

BM25_K1, BM25_B = indexer.K1, indexer.B


def similarity(name: str = "bm25"):
    return ClassicSimilarity() if name == "classic" else BM25Similarity(BM25_K1, BM25_B)


def write_stopwords() -> None:
    path = os.path.join(BASE_DIR, STOPWORDS_FILE)
    content = "\n".join(sorted(indexer.STOPWORDS)) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def text_analyzer():
    write_stopwords()
    return CustomAnalyzer.builder(Paths.get(BASE_DIR)) \
        .withTokenizer("standard", []) \
        .addTokenFilter("lowercase", []) \
        .addTokenFilter("asciiFolding", []) \
        .addTokenFilter("stop", ["words", STOPWORDS_FILE, "ignoreCase", "true"]) \
        .build()


def analyzer():
    per_field = HashMap()
    keyword = KeywordAnalyzer()
    for field in KEYWORD_FIELDS:
        per_field.put(field, keyword)
    return PerFieldAnalyzerWrapper(text_analyzer(), per_field)


def points_config() -> HashMap:
    fmt = NumberFormat.getIntegerInstance(Locale.ROOT)
    fmt.setGroupingUsed(False)
    config = HashMap()
    for field in INT_FIELDS:
        config.put(field, PointsConfig(fmt, Integer.class_))
    return config


def query_parser(analyzer_=None, fields: tuple | list | None = None, boosts: dict | None = None) -> StandardQueryParser:
    parser = StandardQueryParser(analyzer_ or analyzer())
    parser.setPointsConfigMap(points_config())
    multi = lucene.JArray("object")(list(fields or DEFAULT_FIELDS), CharSequence)
    parser.getQueryConfigHandler().set(StandardQueryConfigHandler.ConfigurationKeys.MULTI_FIELDS, multi)
    boost_map = HashMap()
    for field, boost in (FIELD_BOOSTS if boosts is None else boosts).items():
        boost_map.put(field, Float(boost))
    parser.setFieldsBoost(boost_map)
    parser.setAllowLeadingWildcard(False)
    return parser
