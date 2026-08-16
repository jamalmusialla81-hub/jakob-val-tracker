import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tracker


V4_MATCH = {
    "metadata": {
        "match_id": "private-match-id",
        "started_at": "2026-08-15T05:58:54.162Z",
        "map": {"id": "map-id", "name": "Summit"},
    },
    "players": [
        {
            "name": "King",
            "tag": "Jakob",
            "puuid": "private-player-id",
            "team_id": "Blue",
            "agent": {"id": "agent-id", "name": "Jett"},
            "stats": {
                "score": 2452,
                "kills": 8,
                "deaths": 15,
                "assists": 4,
                "headshots": 3,
                "bodyshots": 32,
                "legshots": 6,
                "damage": {"dealt": 1685, "received": 2919},
            },
        }
    ],
    "teams": [
        {
            "team_id": "Red",
            "rounds": {"won": 4, "lost": 13},
            "won": False,
        },
        {
            "team_id": "Blue",
            "rounds": {"won": 13, "lost": 4},
            "won": True,
        },
    ],
}


class TrackerTests(unittest.TestCase):
    def test_parses_henrik_v4_shape(self):
        parsed = tracker.parse(V4_MATCH)

        self.assertEqual(parsed["map"], "Summit")
        self.assertEqual(parsed["agent"], "Jett")
        self.assertEqual(parsed["rounds"], 17)
        self.assertEqual(parsed["acs"], 144.2)
        self.assertEqual(parsed["adr"], 99.1)
        self.assertEqual(parsed["dd_delta_round"], -72.6)
        self.assertTrue(parsed["won"])
        self.assertIn("performance_score", parsed)

    def test_public_export_is_allow_listed_and_stable(self):
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
            self.assertNotIn("private-match-id", serialized)
            self.assertNotIn("private-player-id", serialized)
            self.assertNotIn("King", serialized)
            self.assertNotIn("Jakob", serialized)
            self.assertNotIn("key", payload["latest_match"])


if __name__ == "__main__":
    unittest.main()
