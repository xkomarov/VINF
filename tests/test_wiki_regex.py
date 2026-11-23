from __future__ import annotations

import os

import pytest

import wiki_regex as w

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "wiki_sample.xml")


def fixture_pages() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        records = f.read().split("</page>")
    pages = [w.parse_page(r) for r in records]
    return {p["title"]: p for p in pages if p}


@pytest.fixture(scope="module")
def pages():
    return fixture_pages()


def test_all_pages_found(pages):
    assert len(pages) == 10


def test_title_ns_redirect(pages):
    assert pages["Talk:McLaren"]["ns"] == 1
    assert pages["Kimi Raikkonen"]["redirect"] == "Kimi Räikkönen"
    assert pages["Kimi Räikkönen"]["redirect"] is None


def test_text_is_unescaped(pages):
    assert '<ref name="stats">' in pages["Kimi Räikkönen"]["text"]


def test_empty_self_closing_text(pages):
    assert pages["Empty page"]["text"] == ""


def test_trailing_record_without_page_is_skipped():
    assert w.parse_page("\n</mediawiki>\n") is None


def test_infobox_types(pages):
    types = {t: (w.parse_infobox_page(p) or {}).get("wiki_type") for t, p in pages.items()}
    assert types["Kimi Räikkönen"] == "driver"
    assert types["Nelson Piquet (racing driver)"] == "driver"
    assert types["2021 Monaco Grand Prix"] == "grand_prix"
    assert types["Circuit de Spa-Francorchamps"] == "circuit"
    assert types["McLaren"] == "team"
    assert types["Formula One"] is None
    assert types["Empty page"] is None


def test_infobox_body_respects_nested_braces():
    text = "{{Infobox racing driver\n| a = {{nowrap|{{flagicon|FIN}} x}}\n| b = 2\n}}\nRest"
    name, body, end = w.find_infobox(text)
    assert name == "racing driver"
    assert body.endswith("| b = 2\n")
    assert text[end:] == "\nRest"


def test_unbalanced_infobox():
    assert w.find_infobox("{{Infobox racing driver | a = {{x}}") is None


def test_split_ignores_pipes_in_links_and_templates():
    parts = w.split_top_level("X\n| a = [[A|B]]\n| b = {{c|d}}")
    assert [p.strip() for p in parts] == ["X", "a = [[A|B]]", "b = {{c|d}}"]


def test_driver_fields(pages):
    row = w.parse_infobox_page(pages["Kimi Räikkönen"])
    box = row["infobox"]
    assert box["nationality"] == "FIN Finnish"
    assert box["birth_date"] == "1979-10-17"
    assert box["birth_place"] == "Espoo, Finland"
    assert box["championships"] == "1 (2007)"
    assert box["wins"] == "21"
    assert box["first_race"] == "2001 Australian Grand Prix"
    assert "image" in box


def test_grand_prix_fields(pages):
    box = w.parse_infobox_page(pages["2021 Monaco Grand Prix"])["infobox"]
    assert box["date"] == "2021-05-23"
    assert box["location"] == "Circuit de Monaco, Monte Carlo, Monaco"
    assert box["weather"] == "Sunny Air: 23 C"
    assert box["attendance"] == "7,500"
    assert box["race_no"] == "5"


def test_circuit_fields(pages):
    box = w.parse_infobox_page(pages["Circuit de Spa-Francorchamps"])["infobox"]
    assert box["coordinates"] == "50 26 13 N 5 58 17 E"
    assert box["opened"] == "1921"
    assert box["record_driver"] == "Sergio Pérez"
    assert box["record_team"] == "Racing Point"


def test_team_fields(pages):
    box = w.parse_infobox_page(pages["McLaren"])["infobox"]
    assert box["base"] == "Woking, Surrey, England"
    assert box["principal"] == "Andrea Stella"
    assert box["cons_champ"] == "10"                          
    assert box["drivers'_championships"] == "12"                
    assert box["engines"] == "Mercedes"


def test_person_infobox_only_on_f1_articles():
    person = "{{Infobox person\n| name = X\n}}\nX is a person who did many things in life."
    assert w.parse_infobox_page({"title": "X", "text": person}) is None
    row = w.parse_infobox_page({"title": "X", "text": person + "\n[[Category:Formula One drivers]]"})
    assert row["wiki_type"] == "driver" and row["template"] == "person"


@pytest.mark.parametrize("raw, expected", [
    ("[[Espoo]], [[Finland]]", "Espoo, Finland"),
    ("[[Scuderia Ferrari|Ferrari]]", "Ferrari"),
    ("{{flagicon|GBR}} British", "GBR British"),
    ("{{convert|5.4|km|mi|abbr=on}}", "5.4 km"),
    ("{{birth date and age|1997|9|30}}", "1997-09-30"),
    ("'''Bold''' and ''italic''", "Bold and italic"),
    ("a<br />b<br>c", "a, b, c"),
    ("x<ref>note</ref> y<ref name=a/>", "x y"),
    ("v <!-- hidden --> w", "v w"),
    ("{{unknown template|x}}text", "text"),
    ("[https://example.org Example site]", "Example site"),
    ("{{plainlist|\n* A\n* B}}", "A, B"),
    ("[[File:Car.jpg|thumb|A [[car]]]] Text", "Text"),
])
def test_clean_value(raw, expected):
    assert w.clean_value(raw) == expected


def test_remove_templates_nested():
    assert w.remove_templates("a {{x|{{y}}}} b {{z}} c") == "a  b  c"


def test_abstract(pages):
    row = w.parse_infobox_page(pages["Kimi Räikkönen"])
    assert row["abstract"].startswith("Kimi-Matias Räikkönen (born 17 October 1979) is a Finnish")
    assert "Ref text" not in row["abstract"]
    assert "2007 World Drivers' Championship" in row["abstract"]


def test_abstract_skips_ipa_template(pages):
    row = w.parse_infobox_page(pages["Circuit de Spa-Francorchamps"])
    assert row["abstract"].startswith("The Circuit de Spa-Francorchamps () is a motor-racing circuit")


def test_infobox_names_census(pages):
    assert w.infobox_names(pages["Formula One"]["text"]) == ["sports league"]


@pytest.mark.parametrize("raw, expected", [
    ("Kimi Räikkönen", "kimi raikkonen"),
    ("Nelson Piquet (racing driver)", "nelson piquet"),
    ("Circuit de Spa-Francorchamps", "circuit de spa francorchamps"),
    ("1950 BRITISH GRAND PRIX", "1950 british grand prix"),
    ("Nürburgring", "nurburgring"),
    ("", ""),
])
def test_join_key(raw, expected):
    assert w.join_key(raw) == expected


@pytest.mark.parametrize("opening, expected", [
    ("{{Infobox F1 driver", "f1 driver"),
    ("{{F1 driver", "f1 driver"),
    ("{{Former F1 team", "former f1 team"),
    ("{{Motorsport venue", "motorsport venue"),
    ("{{Grand prix race report infobox", "grand prix race report"),
    ("{{infobox_motorsport_venue", "motorsport venue"),
])
def test_infobox_without_prefix(opening, expected):
    name, _, _ = w.find_infobox("{{Use dmy dates}}\n" + opening + "\n| name = X\n}}\nText")
    assert name == expected


def test_racing_car_is_not_an_entity():
    assert w.find_infobox("{{Racing car\n| name = Lotus 72\n}}") is None
