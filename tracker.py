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

        # Length-normalised combat metrics
        "kills_per_round": round(div(kills, rounds), 3),
        "deaths_per_round": round(div(deaths, rounds), 3),

        "won": bool(
            teams.get(team, {}).get("has_won", False)
        )
    }

    # Add our personalised performance rating directly to the saved match
    parsed["performance_score"] = performance_score(parsed)
    parsed["performance_label"] = performance_label(parsed["performance_score"])

    return parsed

def performance_score(s):
    """
    100 ~= our reference pop-off performance.

    Heavily weights round-normalised combat impact so a 30-round
    match isn't automatically considered better than a short match.
    """

    # Pop-off reference had 23 rounds.
    ref_kpr = 35 / 23
    ref_dpr = 11 / 23

    kpr_score = div(s["kills_per_round"], ref_kpr) * 100
    survival_score = div(ref_dpr, max(s["deaths_per_round"], 0.01)) * 100
    acs_score = div(s["acs"], POPOFF["acs"]) * 100
    adr_score = div(s["adr"], POPOFF["adr"]) * 100

    # HS% matters, but shouldn't dominate overall performance.
    hs_score = div(s["hs"], POPOFF["hs"]) * 100

    score = (
        kpr_score * 0.30
        + survival_score * 0.20
        + acs_score * 0.20
        + adr_score * 0.20
        + hs_score * 0.10
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


def summary(s):
    result = "WIN" if s["won"] else "LOSS"

    score = s.get("performance_score", performance_score(s))
    label = s.get("performance_label", performance_label(score))

    return f"""
==============================
{result} | {s['map']} | {s['agent']}
==============================

PERFORMANCE: {label} ({score}/100)

K/D/A: {s['kills']}/{s['deaths']}/{s['assists']}
KD:    {s['kd']}

K/R:   {s['kills_per_round']}
D/R:   {s['deaths_per_round']}

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


def load_history():
    if not MATCH_FILE.exists():
        return []

    matches = []

    for line in MATCH_FILE.read_text().splitlines():
        try:
            match = json.loads(line)

            # Older saved matches may not have scores yet.
            if "performance_score" not in match:
                match["performance_score"] = performance_score(match)

            if "performance_label" not in match:
                match["performance_label"] = performance_label(
                    match["performance_score"]
                )

            matches.append(match)
        except Exception:
            pass

    # Deduplicate by match ID while preserving newest version
    deduped = {}

    for match in matches:
        mid = match.get("match_id")

        if mid:
            deduped[mid] = match

    return list(deduped.values())


def rolling_stats(history, amount):
    recent = history[-amount:]

    if not recent:
        return None

    count = len(recent)

    avg_score = sum(
        m.get("performance_score", 0) for m in recent
    ) / count

    avg_kd = sum(
        m.get("kd", 0) for m in recent
    ) / count

    avg_acs = sum(
        m.get("acs", 0) for m in recent
    ) / count

    avg_adr = sum(
        m.get("adr", 0) for m in recent
    ) / count

    avg_hs = sum(
        m.get("hs", 0) for m in recent
    ) / count

    avg_kpr = sum(
        m.get("kills_per_round", 0) for m in recent
    ) / count

    avg_dpr = sum(
        m.get("deaths_per_round", 0) for m in recent
    ) / count

    wins = sum(
        1 for m in recent if m.get("won")
    )

    return {
        "games": count,
        "performance": round(avg_score, 1),
        "kd": round(avg_kd, 2),
        "acs": round(avg_acs, 1),
        "adr": round(avg_adr, 1),
        "hs": round(avg_hs, 1),
        "kpr": round(avg_kpr, 3),
        "dpr": round(avg_dpr, 3),
        "winrate": round((wins / count) * 100, 1),
    }


def rolling_report():
    history = load_history()

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

        if not stats:
            continue

        lines.append("")
        lines.append(
            f"LAST {stats['games']} GAMES "
            f"(target window: {amount})"
        )

        lines.append(
            f"Performance: {stats['performance']}/100"
        )

        lines.append(
            f"KD: {stats['kd']} | "
            f"ACS: {stats['acs']} | "
            f"ADR: {stats['adr']}"
        )

        lines.append(
            f"HS: {stats['hs']}% | "
            f"K/R: {stats['kpr']} | "
            f"D/R: {stats['dpr']}"
        )

        lines.append(
            f"Win rate: {stats['winrate']}%"
        )

    return "\n".join(lines)


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

    report = rolling_report()
    print(report)

    (DATA / "rolling_summary.txt").write_text(
        report,
        encoding="utf-8"
    )

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
