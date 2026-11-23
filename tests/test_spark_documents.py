import pytest

pytest.importorskip("pyspark")

import indexer
import spark_documents as sd


@pytest.mark.parametrize("value, expected", [
    ("3 (2 starts)", 3), ("247,000", 247000), ("0", 0), (41, 41), ("c. 100,000 (estimated)", 100000),
    ("", None), (None, None), ("n/a", None),
])
def test_first_int(value, expected):
    assert sd.first_int(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("5.860", 5860), ("5.303km", 5303), ("7.004 km (4.352 mi)", 7004), ("3.219", 3219), ("", None),
])
def test_km_to_m(value, expected):
    assert sd.km_to_m(value) == expected


def test_years_and_nationality():
    assert sd.first_year("1921-10-15") == 1921
    assert sd.first_year("1985-10-31, Re-opened in 1999-04-08") == 1985
    assert sd.first_year("unknown") is None
    assert sd.nationality_code("CAN Canadian") == "CAN"
    assert sd.nationality_code("ITA") == "ITA"
    assert sd.nationality_code("Brazilian") is None


def test_out_of_range_values_are_dropped():
    fields = sd.clean_fields({"length_m": 120000, "turns": 14, "attendance": 5, "weather": "", "season": 1988})
    assert fields == {"turns": 14, "season": 1988}


def test_wiki_fields_driver():
    wiki = {"birth_date": "1960-03-21", "birth_place": "São Paulo, Brazil", "nationality": "BRA Brazilian",
            "wins": "41", "poles": "65", "championships": "3"}
    assert sd.clean_fields(sd.wiki_fields("driver", wiki)) == {
        "birth_year": 1960, "birth_place": "São Paulo, Brazil", "nationality": "BRA",
        "wins": 41, "poles": 65, "championships": 3}


def test_wiki_fields_grand_prix_and_circuit():
    gp = sd.clean_fields(sd.wiki_fields("grand_prix", {"round": "15", "attendance": "247,000",
                                                       "course_km": "5.860", "weather": "Dry"}))
    assert gp == {"round": 15, "attendance": 247000, "length_m": 5860, "weather": "Dry"}
    circuit = sd.clean_fields(sd.wiki_fields("circuit", {"length_km": "7.004", "turns": "19", "opened": "1921"}))
    assert circuit == {"length_m": 7004, "turns": 19, "opened_year": 1921}


def test_our_values_win_and_location_is_merged():
    merged = sd.merge_fields({"location": "Japan", "season": 1987}, {"location": "Suzuka, Japan", "season": 1900,
                                                                     "round": 15})
    assert merged == {"location": "Japan; Suzuka, Japan", "season": 1987, "round": 15}


def test_wiki_text():
    assert sd.wiki_text({"birth_place": "Monaco"}, "Charles Leclerc is a driver.") == \
        "Charles Leclerc is a driver. birth place Monaco"


def test_doc_functions_match_old_wrappers():
    gp = {"season": 1988, "race_id": 5, "gp_slug": "monaco", "official_name": "1988 MONACO GRAND PRIX",
          "circuit": "Circuit de Monaco", "locality": "Monaco"}
    race = {"grand_prix": "Monaco", "winner": "Alain Prost", "team": "McLaren Honda"}
    results = [{"pos": 1, "driver": "Alain Prost", "team": "McLaren Honda"}]
    doc = indexer.grand_prix_doc(gp, race, results, [{"pos": 1, "driver": "Ayrton Senna"}], [])
    assert doc["id"] == "gp/1988/5"
    assert "winner Alain Prost McLaren Honda" in doc["text"]
    assert "pole position Ayrton Senna" in doc["text"]
    circuit = indexer.circuit_doc("Circuit de Monaco", [(gp, race, None)])
    assert circuit["id"] == "circuit/circuit-de-monaco"
    assert circuit["results"] == [[1988, "Monaco", "Alain Prost", "McLaren Honda"]]
