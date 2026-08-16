import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tracker


TARGET = {"puuid": "private-player-id", "name": "King", "tag": "Jakob", "team": "Blue"}
ALLY = {"puuid": "ally-id", "name": "Ally", "tag": "A", "team": "Blue"}
ENEMY_1 = {"puuid": "enemy-1", "name": "Enemy", "tag": "1", "team": "Red"}
ENEMY_2 = {"puuid": "enemy-2", "name": "Enemy", "tag": "2", "team": "Red"}


def kill(round_id, time_ms, killer, victim, assistants=(), weapon="Vandal"):
    return {
        "round": round_id,
        "time_in_round_in_ms": time_ms,
        "time_in_match_in_ms": time_ms,
        "killer": killer,
        "victim": victim,
        "assistants": list(assistants),
        "weapon": {"id": "weapon-id", "name": weapon, "type": "Weapon"},
    }


def round_data(
    round_id,
    winner,
    score,
    damage_dealt,
    damage_received=0,
    headshots=0,
    bodyshots=0,
    plant_team=None,
):
    target_stats = {
        "player": TARGET,
        "stats": {
            "score": score,
            "kills": 0,
            "headshots": headshots,
            "bodyshots": bodyshots,
            "legshots": 0,
        },
        "damage_events": (
            [{"player": ENEMY_1, "damage": damage_dealt, "headshots": headshots, "bodyshots": bodyshots, "legshots": 0}]
            if damage_dealt
            else []
        ),
        "economy": {"loadout_value": 3000},
    }
    enemy_stats = {
        "player": ENEMY_1,
        "stats": {"score": 0, "kills": 0, "headshots": 0, "bodyshots": 0, "legshots": 0},
        "damage_events": (
            [{"player": TARGET, "damage": damage_received, "headshots": 0, "bodyshots": 1, "legshots": 0}]
            if damage_received
            else []
        ),
        "economy": {"loadout_value": 3000},
    }
    return {
        "id": round_id,
        "winning_team": winner,
        "result": "Elimination",
        "plant": {"player": ENEMY_1 if plant_team == "Red" else TARGET, "site": "A"} if plant_team else None,
        "defuse": None,
        "stats": [target_stats, enemy_stats],
    }


V4_MATCH = {
    "metadata": {
        "match_id": "private-match-id",
        "started_at": "2026-08-15T05:58:54.162Z",
        "map": {"id": "map-id", "name": "Summit"},
    },
    "players": [
        {
            **TARGET,
            "team_id": "Blue",
            "agent": {"id": "agent-id", "name": "Jett"},
            "stats": {
                "score": 1000,
                "kills": 1,
                "deaths": 2,
                "assists": 1,
                "headshots": 1,
                "bodyshots": 4,
                "legshots": 0,
                "damage": {"dealt": 400, "received": 300},
            },
            "ability_casts": {"ability1": 3, "ability2": 4, "grenade": 5, "ultimate": 1},
            "economy": {
                "spent": {"overall": 12000, "average": 2400},
                "loadout_value": {"overall": 15000, "average": 3000},
            },
            "behavior": {"afk_rounds": 0},
        }
    ],
    "teams": [
        {"team_id": "Red", "rounds": {"won": 2, "lost": 3}, "won": False},
        {"team_id": "Blue", "rounds": {"won": 3, "lost": 2}, "won": True},
    ],
    "rounds": [
        round_data(0, "Blue", 300, 200, headshots=1, plant_team="Red"),
        round_data(1, "Red", 100, 50, damage_received=150, bodyshots=1),
        round_data(12, "Blue", 250, 100, bodyshots=2, plant_team="Blue"),
        round_data(13, "Red", 150, 50, damage_received=150, bodyshots=1, plant_team="Blue"),
        round_data(14, "Blue", 200, 0, plant_team="Blue"),
    ],
    "kills": [
        kill(0, 1000, TARGET, ENEMY_1),
        kill(0, 2000, ALLY, ENEMY_2),
        kill(1, 1000, ENEMY_1, TARGET),
        kill(1, 7001, ALLY, ENEMY_1),
        kill(12, 1000, ALLY, ENEMY_1, assistants=(TARGET,)),
        kill(13, 1000, ENEMY_1, TARGET),
        kill(13, 4000, ALLY, ENEMY_1),
    ],
}


class TrackerTests(unittest.TestCase):
    def test_history_merge_is_chronological(self):
        newer = tracker.normalize_saved_match(
            {
                "key": "newer",
                "started_at": "2026-08-15T00:00:00Z",
                "kills": 1,
                "deaths": 1,
                "assists": 0,
                "rounds": 1,
                "acs": 100,
                "adr": 100,
                "hs": 10,
                "won": True,
            }
        )
        older = copy.deepcopy(newer)
        older["match_key"] = "older"
        older["started_at"] = "2026-08-01T00:00:00Z"

        with (
            patch.object(tracker, "MATCH_FILE", Path("/does/not/exist")),
            patch.object(tracker, "load_public_history", return_value=[newer, older]),
        ):
            merged = tracker.merge_history()

        self.assertEqual([match["match_key"] for match in merged], ["older", "newer"])

    def test_parses_combat_round_and_side_metrics(self):
        parsed = tracker.parse(V4_MATCH)

        self.assertEqual(parsed["round_score"], {"won": 3, "lost": 2})
        self.assertEqual(parsed["kills"], 1)
        self.assertEqual(parsed["deaths"], 2)
        self.assertEqual(parsed["assists"], 1)
        self.assertEqual(parsed["acs"], 200.0)
        self.assertEqual(parsed["adr"], 80.0)
        self.assertEqual(parsed["dd_delta_round"], 20.0)
        self.assertEqual(parsed["kast"], 80.0)
        self.assertEqual(parsed["traded_deaths"], 1)
        self.assertEqual(parsed["first_kills"], 1)
        self.assertEqual(parsed["first_deaths"], 2)
        self.assertEqual(parsed["first_duel_diff"], -1)
        self.assertEqual(parsed["first_duel_success_rate"], 33.3)
        self.assertEqual(parsed["sides"]["defense"]["rounds"], 2)
        self.assertEqual(parsed["sides"]["attack"]["rounds"], 3)
        self.assertEqual(parsed["weapon_kills"], {"Vandal": 1})

    def test_rolling_analysis_has_variance_groups_sides_and_changes(self):
        parsed = tracker.parse(V4_MATCH)
        history = []
        for index in range(20):
            match = copy.deepcopy(parsed)
            match["match_key"] = f"key-{index}"
            match["started_at"] = f"2026-08-{index + 1:02d}T00:00:00Z"
            match["map"] = "Summit" if index % 2 else "Breeze"
            match["agent"] = "Jett" if index % 3 else "Reyna"
            match["acs"] = 180 + index * 5
            match["adr"] = 120 + index * 3
            match["performance_score"] = 40 + index * 2
            match["won"] = index % 2 == 1
            match["result"] = "WIN" if match["won"] else "LOSS"
            history.append(match)

        window = tracker.rolling_stats(history, 20)
        change = tracker.recent_vs_previous(history, 5)

        self.assertEqual(window["games"], 20)
        self.assertEqual(window["maps"]["best"], "Summit")
        self.assertIn("standard_deviation", window["consistency"]["acs"])
        self.assertEqual(window["coverage"]["event_data_games"], 20)
        self.assertIsNotNone(window["sides"]["attack"])
        self.assertGreater(change["deltas"]["acs"], 0)
        self.assertGreater(change["deltas"]["adr"], 0)

    def test_public_export_is_allow_listed_stable_and_keeps_benchmark(self):
        parsed = tracker.parse(V4_MATCH)

        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.json"
            latest_path = Path(directory) / "latest.json"
            with (
                patch.object(tracker, "PUBLIC_HISTORY_FILE", history_path),
                patch.object(tracker, "PUBLIC_LATEST_FILE", latest_path),
            ):
                tracker.export_public_stats([parsed])
                first = latest_path.read_text(encoding="utf-8")
                tracker.export_public_stats([parsed])
                second = latest_path.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            payload = json.loads(first)
            serialized = json.dumps(payload)
            for forbidden in ("private-match-id", "private-player-id", "King", "Jakob", "enemy-1", "ally-id"):
                self.assertNotIn(forbidden, serialized)
            self.assertNotIn("key", payload["latest_match"])
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["reference_popoff"]["kills"], 35)
            self.assertEqual(payload["reference_popoff"]["deaths"], 11)
            self.assertEqual(payload["reference_popoff"]["afk_adjusted_estimate"], {"kills": 38, "deaths": 8})


if __name__ == "__main__":
    unittest.main()
