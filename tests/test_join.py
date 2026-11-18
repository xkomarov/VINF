from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

import join as j


def test_career_span_from_career_fields():
    assert j.career_span("driver", {"years": "1996–2006", "birth_date": "1971-04-09"}) == (1996, 2006)
    assert j.career_span("driver", {"first_race": "2001 Australian Grand Prix",
                                    "last_race": "2021 Abu Dhabi Grand Prix"}) == (2001, 2021)


def test_career_span_falls_back_to_birth_and_death():
    assert j.career_span("driver", {"birth_date": "1953-11-04"}) == (1967, 0)
    assert j.career_span("driver", {"birth_date": "1919-11-20", "death_date": "2004-01-20"}) == (1933, 2004)
    assert j.career_span("driver", {}) == (0, 0)


def test_career_span_team_uses_debut():
    assert j.career_span("team", {"debut": "1958 Monaco Grand Prix"}) == (1958, 0)


def test_season_gaps():
    piquet = list(range(1978, 1992)) + [2008, 2009]
    assert j.season_gaps(piquet) == [(1991, 2008)]
    assert j.season_gaps([1996, 1997, 2006]) == []
