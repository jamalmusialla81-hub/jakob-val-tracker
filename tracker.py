import os
import time
import json
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("HENRIK_API_KEY")
REGION = os.getenv("VAL_REGION", "ap")
NAME = os.getenv("VAL_NAME", "King")
TAG = os.getenv("VAL_TAG", "Jakob")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))

BASE_URL = "https://api.henrikdev.xyz/valorant/v3/matches"

DATA = Path("data")
DATA.mkdir(exist_ok=True)

SEEN_FILE = DATA / "seen.json"
MATCH_FILE = DATA / "matches.jsonl"
SUMMARY_FILE = DATA / "latest_summary.txt"

POPOFF = {
    "kills": 35,
    "deaths": 11,
    "assists": 2,
    "acs": 398,
    "adr": 265,
    "hs": 19.6
}

def div(a, b):
    return a / b if b else 0

def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except Exception:
        return set()

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen), indent=2))

def get_matches():
    if not API_KEY:
        raise RuntimeError(
            "No HenrikDev API key. Put HENRIK_API_KEY in your .env file."
        )

    url = f"{BASE_URL}/{REGION}/{NAME}/{TAG}"

    r = requests.get(
        url,
        headers={"Authorization": API_KEY},
        params={"mode": "competitive", "size": 5},
        timeout=20
    )

    r.raise_for_status()
    return r.json().get("data", [])

def find_player(match):
    players = match.get("players", {}).get("all_players", [])

    for p in players:
        if (
            str(p.get("name", "")).lower() == NAME.lower()
            and str(p.get("tag", "")).lower() == TAG.lower()
        ):
            return p

def parse(match):
    p = find_player(match)

    if not p:
        return None

    stats = p.get("stats", {})
    meta = match.get("metadata", {})
    teams = match.get("teams", {})

    rounds = (
        teams.get("red", {}).get("rounds_won", 0)
        + teams.get("blue", {}).get("rounds_won", 0)
    )

    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)

    hs = stats.get("headshots", 0)
    body = stats.get("bodyshots", 0)
    legs = stats.get("legshots", 0)

    damage = p.get("damage_made", 0)
    received = p.get("damage_received", 0)

    team = str(p.get("team", "")).lower()

    return {
        "match_id": meta.get("matchid") or meta.get("match_id"),
        "map": meta.get("map"),
        "agent": p.get("character"),

        "kills": kills,
        "deaths": deaths,
        "assists": assists,

        "kd": round(div(kills, deaths), 2),

        "acs": round(div(stats.get("score", 0), rounds), 1),
        "adr": round(div(damage, rounds), 1),

        "hs": round(div(hs, hs + body + legs) * 100, 1),

        "dd_delta_round": round(
            div(damage - received, rounds), 1
        ),

        "rounds": rounds,

        "won": bool(
            teams.get(team, {}).get("has_won", False)
        )
    }

def summary(s):
    result = "WIN" if s["won"] else "LOSS"

    return f"""
==============================
{result} | {s['map']} | {s['agent']}
==============================

K/D/A: {s['kills']}/{s['deaths']}/{s['assists']}
KD:    {s['kd']}

ACS:   {s['acs']}
ADR:   {s['adr']}
HS%:   {s['hs']}%
DDΔ/R: {s['dd_delta_round']}

--- VS BREEZE POP-OFF ---

Kills:  {s['kills'] - POPOFF['kills']:+}
Deaths: {s['deaths'] - POPOFF['deaths']:+}
ACS:    {s['acs'] - POPOFF['acs']:+.1f}
ADR:    {s['adr'] - POPOFF['adr']:+.1f}
HS%:    {s['hs'] - POPOFF['hs']:+.1f}

Reference:
35/11/2 | 398 ACS | 265 ADR | 19.6% HS
AFK-adjusted working estimate: ~38/8
""".strip()

def check():
    seen = load_seen()

    for match in reversed(get_matches()):
        s = parse(match)

        if not s or not s["match_id"]:
            continue

        if s["match_id"] in seen:
            continue

        MATCH_FILE.open("a").write(
            json.dumps(s) + "\n"
        )

        text = summary(s)

        SUMMARY_FILE.write_text(text)

        print()
        print(text)
        print()

        seen.add(s["match_id"])

    save_seen(seen)

def main():
    print(f"Tracking {NAME}#{TAG}")
    print(f"Region: {REGION}")
    print(f"Checking every {POLL_SECONDS} seconds.")
    print("Ctrl+C to stop.\n")

    while True:
        try:
            check()
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print("ERROR:", e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
