import os
import unittest

import extractor as e


def extract(filename: str) -> dict:
    path = os.path.join(e.DATA_DIR, filename)
    if not os.path.exists(path):
        raise unittest.SkipTest(f"{filename} is not crawled")
    return e.extract_file(filename)


class HelperTest(unittest.TestCase):
    def test_text_strips_tags_entities_and_spaces(self):
        self.assertEqual(e.text("<p>Max&nbsp;<b>Verstappen</b>\n  &amp; Co</p>"), "Max Verstappen & Co")

    def test_slug(self):
        self.assertEqual(e.slug("Time / Retired"), "time_retired")
        self.assertEqual(e.slug(" Avg Speed "), "avg_speed")

    def test_clean_removes_scripts_styles_svg_comments(self):
        page = "<script>var a='<td>';</script><style>td{}</style><svg><path/></svg><!-- <td> --><td>ok</td>"
        self.assertEqual(e.clean(page), "<td>ok</td>")

    def test_number_regex_and_typed(self):
        self.assertEqual(e.typed("25"), 25)
        self.assertEqual(e.typed("18.5"), 18.5)
        for value in ("01", "1:31.447", "+5.123s", "NC", "DNF", ""):
            self.assertEqual(e.typed(value), value)

    def test_parse_cell_driver(self):
        cell = ('<a href="/en/results/2023/drivers/MAXVER01/max-verstappen"><span>Max</span>&nbsp;'
                '<span>Verstappen</span><span>VER</span></a>')
        self.assertEqual(e.parse_cell("driver", cell), {
            "driver": "Max Verstappen", "driver_code": "VER",
            "driver_id": "MAXVER01", "driver_slug": "max-verstappen"})

    def test_parse_cell_team_and_race_links(self):
        team = e.parse_cell("team", '<a href="/en/results/2023/team/Haas-Ferrari">Haas Ferrari</a>')
        self.assertEqual(team, {"team": "Haas Ferrari", "team_slug": "Haas-Ferrari"})
        race = e.parse_cell("grand_prix", '<a href="/en/results/2023/races/1141/bahrain/race-result">Bahrain</a>')
        self.assertEqual(race, {"grand_prix": "Bahrain", "race_id": "1141", "gp_slug": "bahrain"})

    def test_parse_table(self):
        table = ("<table><tr><th>Pos</th><th>Pts</th></tr>"
                 "<tr><td>1</td><td>25</td></tr><tr><td>NC</td><td>0</td></tr></table>")
        self.assertEqual(e.parse_table(table), [{"pos": "1", "pts": "25"}, {"pos": "NC", "pts": "0"}])

    def test_parse_stats_prefixes_sections(self):
        page = ("<h2>2026 Season</h2><dt>Wins</dt><dd>2</dd>"
                "<h2>Career Stats</h2><dt>Wins</dt><dd>73</dd>"
                "<h2>Personal</h2><dt>Date of birth</dt><dd>30/09/1997</dd>")
        self.assertEqual(e.parse_stats(page), {"season": "2026", "season_wins": "2", "career_wins": "73",
                                               "date_of_birth": "30/09/1997"})

    def test_filename_patterns(self):
        cases = {
            "results_2023_races_1141_bahrain_race-result.html": e.session_page,
            "results_2023_races_1141_bahrain_practice_2.html": e.session_page,
            "results_2023_races_1171_saudi-arabia_qualifying.html": e.session_page,
            "results_2023_races.html": e.season_page,
            "results_2023_awards_fastest-laps.html": e.award_page,
            "results_2023_drivers_MAXVER01_max-verstappen.html": e.driver_season_page,
            "results_2023_team_Red-Bull-Racing-Honda-RBPT.html": e.team_season_page,
            "racing_2023_bahrain.html": e.event_page,
            "drivers_max-verstappen.html": e.driver_page,
            "teams_ferrari.html": e.team_page,
        }
        for filename, expected in cases.items():
            handler = next((h for p, h in e.HANDLERS if p.match(filename)), None)
            self.assertIs(handler, expected, filename)

    def test_session_page_groups(self):
        m = e.SESSION_PAGE.match("results_2023_races_1171_saudi-arabia_practice_2.html")
        self.assertEqual(m.groups(), ("2023", "1171", "saudi-arabia", "practice", "2"))


class SessionPageTest(unittest.TestCase):
    BAHRAIN = {"season": "2023", "race_id": "1141", "gp_slug": "bahrain"}

    def assert_bahrain_header(self, result):
        self.assertEqual(result["grand_prix"], [{**self.BAHRAIN,
            "official_name": "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX 2023", "dates": "03 - 05 Mar 2023",
            "circuit": "Bahrain International Circuit", "locality": "Sakhir"}])

    def test_race_result_2023_bahrain(self):
        result = extract("results_2023_races_1141_bahrain_race-result.html")
        rows = result["race_results"]
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0], {**self.BAHRAIN, "session": "race-result", "pos": "1", "no": "1",
                                   "driver": "Max Verstappen", "driver_code": "VER",
                                   "team": "Red Bull Racing Honda RBPT", "laps": "57",
                                   "time_retired": "1:33:56.736", "pts": "25"})
        self.assertEqual([r["driver"] for r in rows[1:3]], ["Sergio Perez", "Fernando Alonso"])
        self.assertEqual((rows[-1]["driver"], rows[-1]["pos"], rows[-1]["time_retired"]), ("Oscar Piastri", "NC", "DNF"))
        self.assert_bahrain_header(result)

    def test_qualifying_2023_bahrain(self):
        result = extract("results_2023_races_1141_bahrain_qualifying.html")
        rows = result["qualifying"]
        self.assertEqual(len(rows), 20)
        self.assertEqual((rows[0]["driver"], rows[0]["q1"], rows[0]["q2"], rows[0]["q3"]),
                         ("Max Verstappen", "1:31.295", "1:30.503", "1:29.708"))
        self.assertEqual((rows[-1]["driver"], rows[-1]["q2"], rows[-1]["q3"]), ("Pierre Gasly", "", ""))
        self.assert_bahrain_header(result)

    def test_starting_grid_2023_bahrain(self):
        rows = extract("results_2023_races_1141_bahrain_starting-grid.html")["starting_grid"]
        self.assertEqual(len(rows), 20)
        self.assertEqual((rows[0]["pos"], rows[0]["driver"], rows[0]["time"]), ("1", "Max Verstappen", "1:29.708"))

    def test_fastest_laps_2023_bahrain(self):
        rows = extract("results_2023_races_1141_bahrain_fastest-laps.html")["fastest_laps"]
        self.assertEqual(rows[0]["driver"], "Zhou Guanyu")
        self.assertEqual((rows[0]["lap"], rows[0]["time"], rows[0]["avg_speed"]), ("56", "1:33.996", "207.276"))

    def test_pit_stops_2023_bahrain(self):
        rows = extract("results_2023_races_1141_bahrain_pit-stop-summary.html")["pit_stops"]
        self.assertEqual(len(rows), 50)
        self.assertEqual((rows[0]["driver"], rows[0]["lap"], rows[0]["time"]), ("Pierre Gasly", "9", "25.885"))
        self.assertEqual((rows[-1]["driver"], rows[-1]["stops"], rows[-1]["total"]), ("Lando Norris", "6", "3:15.334"))

    def test_practice_numbered_session(self):
        rows = extract("results_2023_races_1141_bahrain_practice_1.html")["practice"]
        self.assertEqual({r["session"] for r in rows}, {"practice_1"})
        self.assertEqual((rows[0]["driver"], rows[0]["time_gap"]), ("Sergio Perez", "1:32.758"))
        self.assertEqual(rows[-1]["time_gap"], "+3.314s")

    def test_sprint_results_2023_azerbaijan(self):
        result = extract("results_2023_races_1207_azerbaijan_sprint-results.html")
        rows = result["sprint_results"]
        self.assertEqual(len(rows), 19)
        self.assertEqual((rows[0]["driver"], rows[0]["time_retired"], rows[0]["pts"]), ("Sergio Perez", "33:17.667", "8"))
        self.assertEqual((result["grand_prix"][0]["circuit"], result["grand_prix"][0]["locality"]),
                         ("Baku City Circuit", "Baku"))

    def test_sprint_qualifying_2023_azerbaijan(self):
        rows = extract("results_2023_races_1207_azerbaijan_sprint-qualifying.html")["sprint_qualifying"]
        self.assertEqual((rows[0]["driver"], rows[0]["q3"]), ("Charles Leclerc", "1:41.697"))
        self.assertEqual(rows[-1]["driver"], "Nyck De Vries")

    def test_race_result_1958_argentina(self):
        result = extract("results_1958_races_158_argentina_race-result.html")
        rows = result["race_results"]
        self.assertEqual(len(rows), 10)
        self.assertEqual((rows[0]["driver"], rows[0]["team"], rows[0]["pts"]), ("Stirling Moss", "Cooper Climax", "8"))
        gp = result["grand_prix"][0]
        self.assertEqual(gp["official_name"], "1958 ARGENTINE GRAND PRIX")
        self.assertEqual((gp["dates"], gp["circuit"], gp["locality"]),
                         ("19 Jan 1958", "Autódromo Juan y Oscar Gálvez", "Argentina"))


class SeasonPageTest(unittest.TestCase):
    def test_races_2023(self):
        rows = extract("results_2023_races.html")["races"]
        self.assertEqual(len(rows), 22)
        self.assertEqual(rows[0], {"season": "2023", "grand_prix": "Bahrain", "race_id": "1141", "gp_slug": "bahrain",
                                   "date": "05 Mar", "winner": "Max Verstappen", "winner_code": "VER",
                                   "team": "Red Bull Racing Honda RBPT", "laps": "57", "time": "1:33:56.736"})
        self.assertEqual((rows[-1]["grand_prix"], rows[-1]["race_id"]), ("Abu Dhabi", "1226"))

    def test_driver_standings_2023(self):
        rows = extract("results_2023_drivers.html")["driver_standings"]
        self.assertEqual(len(rows), 22)
        self.assertEqual({k: rows[0][k] for k in ("driver", "driver_id", "driver_slug", "nationality", "team_slug", "pts")},
                         {"driver": "Max Verstappen", "driver_id": "MAXVER01", "driver_slug": "max-verstappen",
                          "nationality": "NED", "team_slug": "Red-Bull-Racing-Honda-RBPT", "pts": "575"})

    def test_team_standings_2023(self):
        rows = extract("results_2023_team.html")["team_standings"]
        self.assertEqual(len(rows), 10)
        self.assertEqual((rows[0]["team"], rows[0]["pts"]), ("Red Bull Racing Honda RBPT", "860"))
        self.assertEqual((rows[-1]["team"], rows[-1]["pts"]), ("Haas Ferrari", "12"))

    def test_driver_standings_1950(self):
        rows = extract("results_1950_drivers.html")["driver_standings"]
        self.assertEqual((rows[0]["driver"], rows[0]["team"], rows[0]["pts"]), ("Nino Farina", "Alfa Romeo", "30"))

    def test_award_fastest_laps_2023(self):
        rows = extract("results_2023_awards_fastest-laps.html")["awards"]
        self.assertEqual(len(rows), 20)
        self.assertEqual({k: rows[0][k] for k in ("award", "grand_prix", "winner", "time")},
                         {"award": "fastest-laps", "grand_prix": "Bahrain", "winner": "Zhou Guanyu", "time": "1:33.996"})

    def test_award_pole_positions_2023(self):
        rows = extract("results_2023_awards_pole-positions.html")["awards"]
        self.assertEqual(len(rows), 22)
        self.assertEqual((rows[-1]["grand_prix"], rows[-1]["winner"], rows[-1]["time"]),
                         ("Abu Dhabi", "Max Verstappen", "1:23.445"))

    def test_driver_season_2023_verstappen(self):
        rows = extract("results_2023_drivers_MAXVER01_max-verstappen.html")["driver_season_results"]
        self.assertEqual(len(rows), 22)
        self.assertEqual({r["driver_id"] for r in rows}, {"MAXVER01"})
        self.assertEqual(sum(1 for r in rows if r["race_pos"] == "1"), 19)
        self.assertEqual((rows[-1]["grand_prix"], rows[-1]["pts"]), ("Abu Dhabi", "26"))

    def test_driver_season_1958_brabham(self):
        rows = extract("results_1958_drivers_JACBRA01_jack-brabham.html")["driver_season_results"]
        self.assertEqual(len(rows), 9)
        self.assertEqual((rows[0]["grand_prix"], rows[0]["team"], rows[0]["race_pos"]), ("Monaco", "Cooper Climax", "4"))

    def test_team_season_2023_red_bull(self):
        rows = extract("results_2023_team_Red-Bull-Racing-Honda-RBPT.html")["team_season_results"]
        self.assertEqual(len(rows), 22)
        self.assertEqual({r["team_slug"] for r in rows}, {"Red-Bull-Racing-Honda-RBPT"})
        self.assertEqual(sum(int(r["pts"]) for r in rows), 860)

    def test_team_season_1958_ferrari(self):
        rows = extract("results_1958_team_Ferrari.html")["team_season_results"]
        self.assertEqual(len(rows), 11)
        self.assertEqual((rows[0]["grand_prix"], rows[0]["date"], rows[0]["pts"]), ("Argentina", "19 Jan", "6"))


class ProfilePageTest(unittest.TestCase):
    def test_event_2023_bahrain(self):
        result = extract("racing_2023_bahrain.html")
        circuit = result["circuits"][0]
        self.assertEqual({k: circuit[k] for k in ("event_name", "circuit_length", "first_grand_prix", "number_of_laps",
                                                  "fastest_lap_time", "fastest_lap_time_note", "race_distance")},
                         {"event_name": "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX 2023", "circuit_length": "5.412km",
                          "first_grand_prix": "2004", "number_of_laps": "57", "fastest_lap_time": "1:31.447",
                          "fastest_lap_time_note": "Pedro de la Rosa (2005)", "race_distance": "308.238km"})
        self.assertTrue(circuit["about"].startswith("When was the track built?"))
        schedule = result["schedule"]
        self.assertEqual([s["session"] for s in schedule], ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"])
        self.assertEqual({k: schedule[0][k] for k in ("day", "month", "start", "end")},
                         {"day": "03", "month": "Mar", "start": "11:30", "end": "12:30"})

    def test_event_2023_monaco(self):
        result = extract("racing_2023_monaco.html")
        circuit = result["circuits"][0]
        self.assertEqual((circuit["circuit_length"], circuit["number_of_laps"], circuit["fastest_lap_time_note"]),
                         ("3.337km", "78", "Lewis Hamilton (2021)"))
        self.assertEqual((result["schedule"][-1]["session"], result["schedule"][-1]["start"]), ("Race", "13:00"))

    def test_driver_verstappen(self):
        driver = extract("drivers_max-verstappen.html")["drivers"][0]
        self.assertEqual({k: driver[k] for k in ("first_name", "last_name", "country", "date_of_birth",
                                                 "place_of_birth", "career_world_championships")},
                         {"first_name": "Max", "last_name": "Verstappen", "country": "Netherlands",
                          "date_of_birth": "30/09/1997", "place_of_birth": "Hasselt, Belgium",
                          "career_world_championships": "4"})
        self.assertTrue(driver["biography"].startswith("He’s Max by name"))

    def test_driver_hamilton(self):
        driver = extract("drivers_lewis-hamilton.html")["drivers"][0]
        self.assertEqual((driver["first_name"], driver["last_name"], driver["number"]), ("Lewis", "Hamilton", "44"))
        self.assertEqual((driver["career_world_championships"], driver["date_of_birth"]), ("7", "07/01/1985"))

    def test_team_ferrari(self):
        team = extract("teams_ferrari.html")["teams"][0]
        self.assertEqual(team["drivers"], ["Charles Leclerc", "Lewis Hamilton"])
        self.assertEqual((team["full_team_name"], team["base"], team["first_team_entry"]),
                         ("Scuderia Ferrari HP", "Maranello, Italy", "1950"))
        self.assertEqual(team["career_world_championships"], "16")

    def test_team_mclaren(self):
        team = extract("teams_mclaren.html")["teams"][0]
        self.assertEqual(team["drivers"], ["Lando Norris", "Oscar Piastri"])
        self.assertEqual((team["team_chief"], team["first_team_entry"]), ("Andrea Stella", "1966"))
        self.assertTrue(team["profile"].startswith("Since entering the sport in 1966"))

    def test_skipped_page(self):
        self.assertEqual(e.extract_file("drivers_hall-of-fame.html"), {})


if __name__ == "__main__":
    unittest.main()
