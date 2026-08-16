import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("HENRIK_API_KEY")
REGION = os.getenv("VAL_REGION", "ap")
NAME = os.getenv("VAL_NAME", "King")
TAG = os.getenv("VAL_TAG", "Jakob")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))

BASE_URL = "https://api.henrikdev.xyz/valorant/v4/matches"

DATA = Path("data")
STATS = Path("stats")
DATA.mkdir(exist_ok=True)
STATS.mkdir(exist_ok=True)

SEEN_FILE = DATA / "seen.json"
MATCH_FILE = DATA / "matches.jsonl"
SUMMARY_FILE = DATA / "latest_summary.txt"
ROLLING_FILE = DATA / "rolling_summary.txt"
PUBLIC_HISTORY_FILE = STATS / "history.json"
PUBLIC_LATEST_FILE = STATS / "latest.json"

POPOFF = {
    "kills": 35,
    "deaths": 11,
    "assists": 2,
    "acs": 398,
    "adr": 265,
    "hs": 19.6,
}

PUBLIC_MATCH_FIELDS = (
    "started_at",
    "map",
    "agent",
    "kills",
    "deaths",
    "assists",
    "kd",
    "acs",
    "adr",
    "hs",
    "dd_delta_round",
    "rounds",
    "kills_per_round",
    "deaths_per_round",
    "won",
    "performance_score",
    "performance_label",
)


def div(a, b):
    return a / b if b else 0


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), indent=2) + "\n",
        encoding="utf-8",
    )


def get_matches():
    if not API_KEY:
        raise RuntimeError(
            "No HenrikDev API key. Put HENRIK_API_KEY in your .env file."
        )

    url = f"{BASE_URL}/{REGION}/pc/{NAME}/{TAG}"
    response = requests.get(
        url,
        headers={"Authorization": API_KEY},
        params={"mode": "competitive", "size": 10},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        matches = payload
    elif isinstance(payload, dict):
        matches = payload.get("data")
    else:
        matches = None

    if not isinstance(matches, list):
        raise ValueError("HenrikDev returned an unexpected matches response")

    return matches


def find_player(match):
    players = match.get("players", [])
    if isinstance(players, dict):
        players = players.get("all_players", [])

    for player in players:
        if (
            str(player.get("name", "")).casefold() == NAME.casefold()
            and str(player.get("tag", "")).casefold() == TAG.casefold()
        ):
            return player

    return None


def object_name(value):
    if isinstance(value, dict):
        return value.get("name")
    return value


def match_key(match_id):
    if not match_id:
        return None
    return hashlib.sha256(str(match_id).encode("utf-8")).hexdigest()[:16]


def parse(match):
    player = find_player(match)
    if not player:
        return None

    stats = player.get("stats", {})
    metadata = match.get("metadata", {})
    teams = match.get("teams", [])

    team_id = str(player.get("team_id") or player.get("team") or "")
    player_team = None

    if isinstance(teams, list):
        player_team = next(
            (
                team
                for team in teams
                if str(team.get("team_id", "")).casefold()
                == team_id.casefold()
            ),
            None,
        )
    elif isinstance(teams, dict):
        player_team = teams.get(team_id.casefold()) or teams.get(team_id)

    team_rounds = (player_team or {}).get("rounds", {})
    if isinstance(team_rounds, dict):
        rounds = team_rounds.get("won", 0) + team_rounds.get("lost", 0)
    else:
        rounds = 0

    if not rounds and isinstance(teams, dict):
        rounds = sum(
            team.get("rounds_won", 0)
            for team in teams.values()
            if isinstance(team, dict)
        )

    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)
    headshots = stats.get("headshots", 0)
    bodyshots = stats.get("bodyshots", 0)
    legshots = stats.get("legshots", 0)

    damage = stats.get("damage", {})
    if isinstance(damage, dict):
        damage_dealt = damage.get("dealt", 0)
        damage_received = damage.get("received", 0)
    else:
        damage_dealt = player.get("damage_made", 0)
        damage_received = player.get("damage_received", 0)

    match_id = metadata.get("match_id") or metadata.get("matchid")
    parsed = {
        "match_id": match_id,
        "match_key": match_key(match_id),
        "started_at": metadata.get("started_at"),
        "map": object_name(metadata.get("map")),
        "agent": object_name(player.get("agent") or player.get("character")),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kd": round(div(kills, deaths), 2),
        "acs": round(div(stats.get("score", 0), rounds), 1),
        "adr": round(div(damage_dealt, rounds), 1),
        "hs": round(
            div(headshots, headshots + bodyshots + legshots) * 100,
            1,
        ),
        "dd_delta_round": round(
            div(damage_dealt - damage_received, rounds), 1
        ),
        "rounds": rounds,
        "kills_per_round": round(div(kills, rounds), 3),
        "deaths_per_round": round(div(deaths, rounds), 3),
        "won": bool((player_team or {}).get("won", False)),
    }
    parsed["performance_score"] = performance_score(parsed)
    parsed["performance_label"] = performance_label(
        parsed["performance_score"]
    )
    return parsed


def performance_score(stats):
    """Return a length-normalised score where 100 is the reference game."""
    ref_kpr = 35 / 23
    ref_dpr = 11 / 23

    score = (
        div(stats["kills_per_round"], ref_kpr) * 100 * 0.30
        + div(ref_dpr, max(stats["deaths_per_round"], 0.01)) * 100 * 0.20
        + div(stats["acs"], POPOFF["acs"]) * 100 * 0.20
        + div(stats["adr"], POPOFF["adr"]) * 100 * 0.20
        + div(stats["hs"], POPOFF["hs"]) * 100 * 0.10
    )
    return round(score, 1)


def performance_label(score):
    if score >= 95:
        return "POPOFF"
    if score >= 75:
        return "GREAT"
    if score >= 55:
        return "NORMAL"
    return "BAD"


def normalize_saved_match(saved):
    match = dict(saved)
    rounds = match.get("rounds", 0)
    match["match_key"] = match.get("match_key") or match.get("key") or match_key(
        match.get("match_id")
    )
    match.setdefault("started_at", None)
    match.setdefault(
        "kills_per_round", round(div(match.get("kills", 0), rounds), 3)
    )
    match.setdefault(
        "deaths_per_round", round(div(match.get("deaths", 0), rounds), 3)
    )
    match["performance_score"] = performance_score(match)
    match["performance_label"] = performance_label(
        match["performance_score"]
    )
    return match


def load_local_history():
    if not MATCH_FILE.exists():
        return []

    matches = []
    for line in MATCH_FILE.read_text(encoding="utf-8").splitlines():
        try:
            matches.append(normalize_saved_match(json.loads(line)))
        except (KeyError, TypeError, ValueError):
            continue
    return matches


def load_public_history():
    try:
        payload = json.loads(PUBLIC_HISTORY_FILE.read_text(encoding="utf-8"))
        matches = payload.get("matches", [])
        return [normalize_saved_match(match) for match in matches]
    except (OSError, KeyError, TypeError, ValueError):
        return []


def merge_history(new_matches=()):
    deduped = {}
    anonymous = 0

    for match in [*load_local_history(), *load_public_history(), *new_matches]:
        normalized = normalize_saved_match(match)
        identity = normalized.get("match_key")
        if not identity:
            anonymous += 1
            identity = f"anonymous-{anonymous}"
        deduped[identity] = normalized

    return list(deduped.values())


def rolling_stats(history, amount):
    recent = history[-amount:]
    if not recent:
        return None

    count = len(recent)
    wins = sum(1 for match in recent if match.get("won"))

    def average(key):
        return sum(match.get(key, 0) for match in recent) / count

    return {
        "games": count,
        "performance": round(average("performance_score"), 1),
        "kd": round(average("kd"), 2),
        "acs": round(average("acs"), 1),
        "adr": round(average("adr"), 1),
        "hs": round(average("hs"), 1),
        "kpr": round(average("kills_per_round"), 3),
        "dpr": round(average("deaths_per_round"), 3),
        "winrate": round(div(wins, count) * 100, 1),
    }


def public_match(match, include_key=False):
    public = {key: match.get(key) for key in PUBLIC_MATCH_FIELDS}
    if include_key:
        public["key"] = match.get("match_key")
    return public


def write_json_if_changed(path, payload):
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


def export_public_stats(history):
    if not history:
        return

    public_history = [
        public_match(match, include_key=True) for match in history[-100:]
    ]
    write_json_if_changed(
        PUBLIC_HISTORY_FILE,
        {"schema_version": 1, "matches": public_history},
    )
    write_json_if_changed(
        PUBLIC_LATEST_FILE,
        {
            "schema_version": 1,
            "latest_match": public_match(history[-1]),
            "rolling": {
                str(amount): rolling_stats(history, amount)
                for amount in (5, 10, 20)
            },
        },
    )


def summary(stats):
    result = "WIN" if stats["won"] else "LOSS"
    return f"""
==============================
{result} | {stats['map']} | {stats['agent']}
==============================

PERFORMANCE: {stats['performance_label']} ({stats['performance_score']}/100)

K/D/A: {stats['kills']}/{stats['deaths']}/{stats['assists']}
KD:    {stats['kd']}

K/R:   {stats['kills_per_round']}
D/R:   {stats['deaths_per_round']}

ACS:   {stats['acs']}
ADR:   {stats['adr']}
HS%:   {stats['hs']}%
DDΔ/R: {stats['dd_delta_round']}

--- VS BREEZE POP-OFF ---

Kills:  {stats['kills'] - POPOFF['kills']:+}
Deaths: {stats['deaths'] - POPOFF['deaths']:+}
ACS:    {stats['acs'] - POPOFF['acs']:+.1f}
ADR:    {stats['adr'] - POPOFF['adr']:+.1f}
HS%:    {stats['hs'] - POPOFF['hs']:+.1f}

Reference:
35/11/2 | 398 ACS | 265 ADR | 19.6% HS
AFK-adjusted working estimate: ~38/8
""".strip()


def rolling_report(history):
    if not history:
        return "No match history yet."

    lines = [
        "",
        "==============================",
        "ROLLING PERFORMANCE",
        "==============================",
    ]
    for amount in (5, 10, 20):
        stats = rolling_stats(history, amount)
        lines.extend(
            [
                "",
                f"LAST {stats['games']} GAMES (target window: {amount})",
                f"Performance: {stats['performance']}/100",
                f"KD: {stats['kd']} | ACS: {stats['acs']} | ADR: {stats['adr']}",
                f"HS: {stats['hs']}% | K/R: {stats['kpr']} | D/R: {stats['dpr']}",
                f"Win rate: {stats['winrate']}%",
            ]
        )
    return "\n".join(lines)


def check():
    seen = load_seen()
    fetched = []

    for raw_match in reversed(get_matches()):
        stats = parse(raw_match)
        if not stats or not stats["match_id"]:
            continue

        fetched.append(stats)
        if stats["match_id"] in seen:
            continue

        with MATCH_FILE.open("a", encoding="utf-8") as matches_file:
            matches_file.write(json.dumps(stats) + "\n")

        text = summary(stats)
        SUMMARY_FILE.write_text(text, encoding="utf-8")
        print(f"\n{text}\n")
        seen.add(stats["match_id"])

    save_seen(seen)
    history = merge_history(fetched)
    export_public_stats(history)

    report = rolling_report(history)
    print(report)
    ROLLING_FILE.write_text(report, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Track VALORANT match stats")
    parser.add_argument(
        "--once", action="store_true", help="check once and exit"
    )
    args = parser.parse_args()

    print(f"Tracking {NAME}#{TAG}")
    print(f"Region: {REGION}")

    if args.once:
        check()
        return

    print(f"Checking every {POLL_SECONDS} seconds.")
    print("Ctrl+C to stop.\n")
    while True:
        try:
            check()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as error:
            print("ERROR:", error)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
