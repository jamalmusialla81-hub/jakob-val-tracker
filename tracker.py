import argparse
import hashlib
import json
import os
import statistics
import time
from collections import Counter, defaultdict
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
TRADE_WINDOW_MS = 5000
HISTORY_LIMIT = 100

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
    "label": "Breeze Reyna reference pop-off",
    "map": "Breeze",
    "agent": "Reyna",
    "kills": 35,
    "deaths": 11,
    "assists": 2,
    "kd": 3.18,
    "acs": 398.0,
    "adr": 265.0,
    "hs": 19.6,
    "rounds": 23,
    "kills_per_round": round(35 / 23, 3),
    "deaths_per_round": round(11 / 23, 3),
    "afk_adjusted_estimate": {"kills": 38, "deaths": 8},
}

PUBLIC_MATCH_FIELDS = (
    "started_at",
    "map",
    "agent",
    "result",
    "won",
    "round_score",
    "rounds",
    "kills",
    "deaths",
    "assists",
    "kd",
    "score",
    "acs",
    "damage_dealt",
    "damage_received",
    "damage_delta",
    "adr",
    "dd_delta_round",
    "headshots",
    "bodyshots",
    "legshots",
    "hs",
    "headshots_per_round",
    "kills_per_round",
    "deaths_per_round",
    "assists_per_round",
    "kast",
    "kast_rounds",
    "survival_rate",
    "rounds_survived",
    "traded_deaths",
    "first_kills",
    "first_deaths",
    "first_duel_attempts",
    "first_duel_diff",
    "first_duel_success_rate",
    "rounds_with_kill",
    "rounds_with_assist",
    "zero_kill_rounds",
    "multi_kill_rounds",
    "max_kills_in_round",
    "kill_participation",
    "weapon_kills",
    "ability_casts",
    "economy",
    "afk_rounds",
    "sides",
    "event_data_available",
    "side_data_available",
    "performance_score",
    "performance_label",
)

AVERAGE_METRICS = (
    "performance_score",
    "kills",
    "deaths",
    "assists",
    "kd",
    "acs",
    "adr",
    "hs",
    "headshots_per_round",
    "kast",
    "dd_delta_round",
    "kills_per_round",
    "deaths_per_round",
    "assists_per_round",
    "survival_rate",
    "first_kills",
    "first_deaths",
    "first_duel_diff",
    "first_duel_success_rate",
    "kill_participation",
    "damage_dealt",
    "damage_received",
)

CONSISTENCY_METRICS = (
    "performance_score",
    "kd",
    "acs",
    "adr",
    "hs",
    "kast",
    "dd_delta_round",
    "kills_per_round",
    "deaths_per_round",
    "first_duel_diff",
)

COMPARISON_METRICS = (
    "performance_score",
    "kd",
    "acs",
    "adr",
    "hs",
    "kast",
    "dd_delta_round",
    "kills_per_round",
    "deaths_per_round",
    "survival_rate",
    "first_duel_diff",
    "first_duel_success_rate",
    "win_rate",
)


def div(a, b):
    return a / b if b else 0


def rounded(value, digits=1):
    return round(value, digits) if value is not None else None


def entity_id(entity):
    return (entity or {}).get("puuid")


def entity_team(entity):
    return str((entity or {}).get("team") or "")


def object_name(value):
    return value.get("name") if isinstance(value, dict) else value


def match_key(match_id):
    if not match_id:
        return None
    return hashlib.sha256(str(match_id).encode("utf-8")).hexdigest()[:16]


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), indent=2) + "\n", encoding="utf-8"
    )


def get_matches():
    if not API_KEY:
        raise RuntimeError(
            "No HenrikDev API key. Put HENRIK_API_KEY in your .env file."
        )

    response = requests.get(
        f"{BASE_URL}/{REGION}/pc/{NAME}/{TAG}",
        headers={"Authorization": API_KEY},
        params={"mode": "competitive", "size": 10},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    matches = payload if isinstance(payload, list) else payload.get("data")
    if not isinstance(matches, list):
        raise ValueError("HenrikDev returned an unexpected matches response")
    return matches


def find_player(match):
    players = match.get("players", [])
    if isinstance(players, dict):
        players = players.get("all_players", [])
    return next(
        (
            player
            for player in players
            if str(player.get("name", "")).casefold() == NAME.casefold()
            and str(player.get("tag", "")).casefold() == TAG.casefold()
        ),
        None,
    )


def other_team(team, teams):
    return next((candidate for candidate in teams if candidate != team), None)


def infer_attack_teams(rounds, team_ids):
    """Infer sides from spike plants, then use known side-switch rules."""
    team_ids = [str(team) for team in team_ids if team]
    planted_by_segment = {"first": None, "second": None}
    overtime_by_parity = {}

    for round_data in rounds:
        plant = round_data.get("plant") or {}
        plant_team = entity_team(plant.get("player"))
        if not plant_team:
            continue
        round_id = int(round_data.get("id", 0))
        if round_id < 12:
            planted_by_segment["first"] = plant_team
        elif round_id < 24:
            planted_by_segment["second"] = plant_team
        else:
            overtime_by_parity[round_id % 2] = plant_team

    first = planted_by_segment["first"]
    second = planted_by_segment["second"]
    if not first and second:
        first = other_team(second, team_ids)
    if not second and first:
        second = other_team(first, team_ids)

    if len(overtime_by_parity) == 1:
        parity, team = next(iter(overtime_by_parity.items()))
        overtime_by_parity[1 - parity] = other_team(team, team_ids)

    inferred = {}
    for round_data in rounds:
        round_id = int(round_data.get("id", 0))
        if round_id < 12:
            inferred[round_id] = first
        elif round_id < 24:
            inferred[round_id] = second
        else:
            inferred[round_id] = overtime_by_parity.get(round_id % 2)
    return inferred


def was_traded(death_event, events, player_team):
    killer_id = entity_id(death_event.get("killer"))
    death_time = death_event.get("time_in_round_in_ms", 0)
    if not killer_id:
        return False

    return any(
        entity_id(event.get("victim")) == killer_id
        and entity_team(event.get("killer")).casefold() == player_team.casefold()
        and death_time < event.get("time_in_round_in_ms", 0) <= death_time + TRADE_WINDOW_MS
        for event in events
    )


def round_records(match, player):
    player_id = player.get("puuid")
    player_team = str(player.get("team_id") or player.get("team") or "")
    team_ids = [team.get("team_id") for team in match.get("teams", [])]
    attack_teams = infer_attack_teams(match.get("rounds", []), team_ids)
    events_by_round = defaultdict(list)
    for event in match.get("kills", []):
        events_by_round[int(event.get("round", 0))].append(event)

    records = []
    for round_data in match.get("rounds", []):
        round_id = int(round_data.get("id", 0))
        events = sorted(
            events_by_round.get(round_id, []),
            key=lambda event: event.get("time_in_round_in_ms", 0),
        )
        player_stats = next(
            (
                item
                for item in round_data.get("stats", [])
                if entity_id(item.get("player")) == player_id
            ),
            {},
        )
        stats = player_stats.get("stats", {})
        kills = [event for event in events if entity_id(event.get("killer")) == player_id]
        deaths = [event for event in events if entity_id(event.get("victim")) == player_id]
        assisted = [
            event
            for event in events
            if any(entity_id(assistant) == player_id for assistant in event.get("assistants", []))
        ]
        death_event = deaths[0] if deaths else None
        traded = bool(death_event and was_traded(death_event, events, player_team))
        survived = not deaths
        first_event = events[0] if events else None
        first_kill = bool(first_event and entity_id(first_event.get("killer")) == player_id)
        first_death = bool(first_event and entity_id(first_event.get("victim")) == player_id)

        damage_dealt = sum(
            event.get("damage", 0) for event in player_stats.get("damage_events", [])
        )
        damage_received = sum(
            damage_event.get("damage", 0)
            for other in round_data.get("stats", [])
            for damage_event in other.get("damage_events", [])
            if entity_id(damage_event.get("player")) == player_id
        )
        team_kills = [
            event
            for event in events
            if entity_team(event.get("killer")).casefold() == player_team.casefold()
        ]
        kill_participations = [
            event
            for event in team_kills
            if entity_id(event.get("killer")) == player_id
            or any(entity_id(assistant) == player_id for assistant in event.get("assistants", []))
        ]
        attack_team = attack_teams.get(round_id)
        side = None
        if attack_team:
            side = "attack" if attack_team.casefold() == player_team.casefold() else "defense"

        records.append(
            {
                "round_id": round_id,
                "side": side,
                "won": str(round_data.get("winning_team", "")).casefold() == player_team.casefold(),
                "kills": len(kills),
                "deaths": len(deaths),
                "assists": len(assisted),
                "score": stats.get("score", 0),
                "damage_dealt": damage_dealt,
                "damage_received": damage_received,
                "headshots": stats.get("headshots", 0),
                "bodyshots": stats.get("bodyshots", 0),
                "legshots": stats.get("legshots", 0),
                "survived": survived,
                "traded": traded,
                "kast": bool(kills or assisted or survived or traded),
                "first_kill": first_kill,
                "first_death": first_death,
                "team_kills": len(team_kills),
                "kill_participations": len(kill_participations),
                "loadout_value": (player_stats.get("economy") or {}).get("loadout_value", 0),
            }
        )
    return records


def aggregate_round_records(records):
    if not records:
        return None

    rounds = len(records)
    total = lambda key: sum(record.get(key, 0) for record in records)
    kills = total("kills")
    deaths = total("deaths")
    assists = total("assists")
    headshots = total("headshots")
    bodyshots = total("bodyshots")
    legshots = total("legshots")
    damage_dealt = total("damage_dealt")
    damage_received = total("damage_received")
    first_kills = sum(record["first_kill"] for record in records)
    first_deaths = sum(record["first_death"] for record in records)
    first_attempts = first_kills + first_deaths
    won = sum(record["won"] for record in records)
    survived = sum(record["survived"] for record in records)
    kast_rounds = sum(record["kast"] for record in records)
    team_kills = total("team_kills")

    return {
        "rounds": rounds,
        "rounds_won": won,
        "rounds_lost": rounds - won,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kd": round(div(kills, deaths), 2),
        "score": total("score"),
        "acs": round(div(total("score"), rounds), 1),
        "damage_dealt": damage_dealt,
        "damage_received": damage_received,
        "damage_delta": damage_dealt - damage_received,
        "adr": round(div(damage_dealt, rounds), 1),
        "dd_delta_round": round(div(damage_dealt - damage_received, rounds), 1),
        "headshots": headshots,
        "bodyshots": bodyshots,
        "legshots": legshots,
        "hs": round(div(headshots, headshots + bodyshots + legshots) * 100, 1),
        "kills_per_round": round(div(kills, rounds), 3),
        "deaths_per_round": round(div(deaths, rounds), 3),
        "assists_per_round": round(div(assists, rounds), 3),
        "kast": round(div(kast_rounds, rounds) * 100, 1),
        "kast_rounds": kast_rounds,
        "rounds_survived": survived,
        "survival_rate": round(div(survived, rounds) * 100, 1),
        "traded_deaths": sum(record["traded"] for record in records),
        "first_kills": first_kills,
        "first_deaths": first_deaths,
        "first_duel_attempts": first_attempts,
        "first_duel_diff": first_kills - first_deaths,
        "first_duel_success_rate": round(div(first_kills, first_attempts) * 100, 1),
        "rounds_with_kill": sum(record["kills"] > 0 for record in records),
        "rounds_with_assist": sum(record["assists"] > 0 for record in records),
        "zero_kill_rounds": sum(record["kills"] == 0 for record in records),
        "multi_kill_rounds": sum(record["kills"] >= 2 for record in records),
        "max_kills_in_round": max(record["kills"] for record in records),
        "team_kills": team_kills,
        "kill_participations": total("kill_participations"),
        "kill_participation": round(div(total("kill_participations"), team_kills) * 100, 1),
        "average_loadout_value": round(div(total("loadout_value"), rounds), 1),
    }


def normalized_ability_casts(player):
    casts = player.get("ability_casts", {}) or {}
    return {
        "ability_1": casts.get("ability1", casts.get("ability_1", 0)) or 0,
        "ability_2": casts.get("ability2", casts.get("ability_2", 0)) or 0,
        "grenade": casts.get("grenade", 0) or 0,
        "ultimate": casts.get("ultimate", 0) or 0,
    }


def parse(match):
    player = find_player(match)
    if not player:
        return None

    stats = player.get("stats", {})
    metadata = match.get("metadata", {})
    teams = match.get("teams", [])
    team_id = str(player.get("team_id") or player.get("team") or "")
    player_team = next(
        (
            team
            for team in teams
            if str(team.get("team_id", "")).casefold() == team_id.casefold()
        ),
        {},
    )
    team_rounds = player_team.get("rounds", {}) or {}
    rounds_won = team_rounds.get("won", 0)
    rounds_lost = team_rounds.get("lost", 0)
    rounds = rounds_won + rounds_lost

    damage = stats.get("damage", {}) or {}
    damage_dealt = damage.get("dealt", 0)
    damage_received = damage.get("received", 0)
    headshots = stats.get("headshots", 0)
    bodyshots = stats.get("bodyshots", 0)
    legshots = stats.get("legshots", 0)
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)
    records = round_records(match, player)
    event_stats = aggregate_round_records(records) or {}
    weapon_kills = Counter(
        object_name(event.get("weapon")) or "Unknown"
        for event in match.get("kills", [])
        if entity_id(event.get("killer")) == player.get("puuid")
    )

    economy = player.get("economy", {}) or {}
    spent = economy.get("spent", {}) or {}
    loadout = economy.get("loadout_value", {}) or {}
    behavior = player.get("behavior", {}) or {}
    match_id = metadata.get("match_id") or metadata.get("matchid")

    parsed = {
        "match_id": match_id,
        "match_key": match_key(match_id),
        "started_at": metadata.get("started_at"),
        "map": object_name(metadata.get("map")),
        "agent": object_name(player.get("agent") or player.get("character")),
        "result": "WIN" if player_team.get("won", False) else "LOSS",
        "won": bool(player_team.get("won", False)),
        "round_score": {"won": rounds_won, "lost": rounds_lost},
        "rounds": rounds,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kd": round(div(kills, deaths), 2),
        "score": stats.get("score", 0),
        "acs": round(div(stats.get("score", 0), rounds), 1),
        "damage_dealt": damage_dealt,
        "damage_received": damage_received,
        "damage_delta": damage_dealt - damage_received,
        "adr": round(div(damage_dealt, rounds), 1),
        "dd_delta_round": round(div(damage_dealt - damage_received, rounds), 1),
        "headshots": headshots,
        "bodyshots": bodyshots,
        "legshots": legshots,
        "hs": round(div(headshots, headshots + bodyshots + legshots) * 100, 1),
        "headshots_per_round": round(div(headshots, rounds), 3),
        "kills_per_round": round(div(kills, rounds), 3),
        "deaths_per_round": round(div(deaths, rounds), 3),
        "assists_per_round": round(div(assists, rounds), 3),
        "kast": event_stats.get("kast"),
        "kast_rounds": event_stats.get("kast_rounds"),
        "survival_rate": event_stats.get("survival_rate"),
        "rounds_survived": event_stats.get("rounds_survived"),
        "traded_deaths": event_stats.get("traded_deaths"),
        "first_kills": event_stats.get("first_kills"),
        "first_deaths": event_stats.get("first_deaths"),
        "first_duel_attempts": event_stats.get("first_duel_attempts"),
        "first_duel_diff": event_stats.get("first_duel_diff"),
        "first_duel_success_rate": event_stats.get("first_duel_success_rate"),
        "rounds_with_kill": event_stats.get("rounds_with_kill"),
        "rounds_with_assist": event_stats.get("rounds_with_assist"),
        "zero_kill_rounds": event_stats.get("zero_kill_rounds"),
        "multi_kill_rounds": event_stats.get("multi_kill_rounds"),
        "max_kills_in_round": event_stats.get("max_kills_in_round"),
        "kill_participation": event_stats.get("kill_participation"),
        "weapon_kills": dict(sorted(weapon_kills.items())),
        "ability_casts": normalized_ability_casts(player),
        "economy": {
            "spent_total": spent.get("overall", 0),
            "spent_per_round": round(spent.get("average", 0), 1),
            "average_loadout_value": round(loadout.get("average", 0), 1),
        },
        "afk_rounds": behavior.get("afk_rounds", 0),
        "sides": {
            side: aggregate_round_records([record for record in records if record["side"] == side])
            for side in ("attack", "defense")
        },
        "event_data_available": bool(records),
        "side_data_available": any(record["side"] for record in records),
    }
    parsed["performance_score"] = performance_score(parsed)
    parsed["performance_label"] = performance_label(parsed["performance_score"])
    return parsed


def performance_score(stats):
    ref_kpr = POPOFF["kills_per_round"]
    ref_dpr = POPOFF["deaths_per_round"]
    score = (
        div(stats.get("kills_per_round", 0), ref_kpr) * 100 * 0.30
        + div(ref_dpr, max(stats.get("deaths_per_round", 0), 0.01)) * 100 * 0.20
        + div(stats.get("acs", 0), POPOFF["acs"]) * 100 * 0.20
        + div(stats.get("adr", 0), POPOFF["adr"]) * 100 * 0.20
        + div(stats.get("hs", 0), POPOFF["hs"]) * 100 * 0.10
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
    match["match_key"] = match.get("match_key") or match.get("key") or match_key(match.get("match_id"))
    match.setdefault("started_at", None)
    match.setdefault("result", "WIN" if match.get("won") else "LOSS")
    match.setdefault("round_score", None)
    match.setdefault("score", round((match.get("acs") or 0) * rounds))
    match.setdefault("damage_dealt", round((match.get("adr") or 0) * rounds))
    match.setdefault("damage_received", None)
    match.setdefault("damage_delta", None)
    match.setdefault("headshots", None)
    match.setdefault("bodyshots", None)
    match.setdefault("legshots", None)
    match.setdefault("headshots_per_round", None)
    match.setdefault("kills_per_round", round(div(match.get("kills", 0), rounds), 3))
    match.setdefault("deaths_per_round", round(div(match.get("deaths", 0), rounds), 3))
    match.setdefault("assists_per_round", round(div(match.get("assists", 0), rounds), 3))
    for field in PUBLIC_MATCH_FIELDS:
        match.setdefault(field, None)
    match.setdefault("weapon_kills", {})
    match.setdefault("ability_casts", None)
    match.setdefault("economy", None)
    match.setdefault("sides", {"attack": None, "defense": None})
    match.setdefault("event_data_available", False)
    match.setdefault("side_data_available", False)
    match["performance_score"] = performance_score(match)
    match["performance_label"] = performance_label(match["performance_score"])
    return match


def load_jsonl(path):
    if not path.exists():
        return []
    matches = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            matches.append(normalize_saved_match(json.loads(line)))
        except (KeyError, TypeError, ValueError):
            continue
    return matches


def load_public_history():
    try:
        payload = json.loads(PUBLIC_HISTORY_FILE.read_text(encoding="utf-8"))
        return [normalize_saved_match(match) for match in payload.get("matches", [])]
    except (OSError, KeyError, TypeError, ValueError):
        return []


def merge_history(new_matches=()):
    deduped = {}
    anonymous = 0
    for match in [*load_jsonl(MATCH_FILE), *load_public_history(), *new_matches]:
        normalized = normalize_saved_match(match)
        identity = normalized.get("match_key")
        if not identity:
            anonymous += 1
            identity = f"anonymous-{anonymous}"
        deduped[identity] = normalized
    return sorted(
        deduped.values(),
        key=lambda match: (
            match.get("started_at") is not None,
            match.get("started_at") or "",
        ),
    )


def values(matches, metric):
    return [match[metric] for match in matches if isinstance(match.get(metric), (int, float))]


def average_metrics(matches):
    averages = {}
    for metric in AVERAGE_METRICS:
        metric_values = values(matches, metric)
        averages[metric] = round(statistics.fmean(metric_values), 3) if metric_values else None
    return averages


def compact_game(match):
    return {
        "started_at": match.get("started_at"),
        "map": match.get("map"),
        "agent": match.get("agent"),
        "result": match.get("result"),
        "round_score": match.get("round_score"),
        "kills": match.get("kills"),
        "deaths": match.get("deaths"),
        "assists": match.get("assists"),
        "kd": match.get("kd"),
        "acs": match.get("acs"),
        "adr": match.get("adr"),
        "hs": match.get("hs"),
        "kast": match.get("kast"),
        "dd_delta_round": match.get("dd_delta_round"),
        "first_duel_diff": match.get("first_duel_diff"),
        "performance_score": match.get("performance_score"),
        "performance_label": match.get("performance_label"),
    }


def grouped_summary(matches, field):
    groups = defaultdict(list)
    for match in matches:
        if match.get(field):
            groups[match[field]].append(match)
    summaries = {}
    for name, group in groups.items():
        summaries[name] = {
            "games": len(group),
            "wins": sum(bool(match.get("won")) for match in group),
            "win_rate": round(div(sum(bool(match.get("won")) for match in group), len(group)) * 100, 1),
            "averages": average_metrics(group),
        }
    ranked = sorted(
        summaries,
        key=lambda name: summaries[name]["averages"].get("performance_score") or -1,
        reverse=True,
    )
    return {
        "groups": summaries,
        "best": ranked[0] if ranked else None,
        "worst": ranked[-1] if ranked else None,
    }


def combine_side_summaries(matches, side):
    summaries = [match.get("sides", {}).get(side) for match in matches if match.get("sides")]
    summaries = [summary for summary in summaries if summary]
    if not summaries:
        return None

    rounds = sum(summary["rounds"] for summary in summaries)
    totals = {
        key: sum(summary.get(key, 0) for summary in summaries)
        for key in (
            "rounds_won", "rounds_lost", "kills", "deaths", "assists", "score",
            "damage_dealt", "damage_received", "headshots", "bodyshots", "legshots",
            "kast_rounds", "rounds_survived", "traded_deaths", "first_kills", "first_deaths",
            "rounds_with_kill", "rounds_with_assist", "zero_kill_rounds", "multi_kill_rounds",
            "team_kills", "kill_participations",
        )
    }
    first_attempts = totals["first_kills"] + totals["first_deaths"]
    hit_total = totals["headshots"] + totals["bodyshots"] + totals["legshots"]
    return {
        "rounds": rounds,
        **totals,
        "win_rate": round(div(totals["rounds_won"], rounds) * 100, 1),
        "kd": round(div(totals["kills"], totals["deaths"]), 2),
        "acs": round(div(totals["score"], rounds), 1),
        "adr": round(div(totals["damage_dealt"], rounds), 1),
        "dd_delta_round": round(div(totals["damage_dealt"] - totals["damage_received"], rounds), 1),
        "hs": round(div(totals["headshots"], hit_total) * 100, 1),
        "kast": round(div(totals["kast_rounds"], rounds) * 100, 1),
        "survival_rate": round(div(totals["rounds_survived"], rounds) * 100, 1),
        "kills_per_round": round(div(totals["kills"], rounds), 3),
        "deaths_per_round": round(div(totals["deaths"], rounds), 3),
        "assists_per_round": round(div(totals["assists"], rounds), 3),
        "first_duel_attempts": first_attempts,
        "first_duel_diff": totals["first_kills"] - totals["first_deaths"],
        "first_duel_success_rate": round(div(totals["first_kills"], first_attempts) * 100, 1),
        "kill_participation": round(div(totals["kill_participations"], totals["team_kills"]) * 100, 1),
    }


def rolling_stats(history, amount):
    recent = history[-amount:]
    if not recent:
        return None
    averages = average_metrics(recent)
    consistency = {}
    for metric in CONSISTENCY_METRICS:
        metric_values = values(recent, metric)
        consistency[metric] = {
            "standard_deviation": round(statistics.pstdev(metric_values), 3) if metric_values else None,
            "variance": round(statistics.pvariance(metric_values), 3) if metric_values else None,
            "range": round(max(metric_values) - min(metric_values), 3) if metric_values else None,
        }
    ranked = sorted(recent, key=lambda match: match.get("performance_score", 0), reverse=True)
    wins = sum(bool(match.get("won")) for match in recent)
    return {
        "games": len(recent),
        "wins": wins,
        "losses": len(recent) - wins,
        "win_rate": round(div(wins, len(recent)) * 100, 1),
        "totals": {
            field: sum(match.get(field, 0) or 0 for match in recent)
            for field in ("kills", "deaths", "assists", "damage_dealt", "damage_received", "first_kills", "first_deaths")
        },
        "averages": averages,
        "consistency": consistency,
        "best_game": compact_game(ranked[0]),
        "worst_game": compact_game(ranked[-1]),
        "maps": grouped_summary(recent, "map"),
        "agents": grouped_summary(recent, "agent"),
        "sides": {
            "attack": combine_side_summaries(recent, "attack"),
            "defense": combine_side_summaries(recent, "defense"),
        },
        "coverage": {
            "event_data_games": sum(bool(match.get("event_data_available")) for match in recent),
            "side_data_games": sum(bool(match.get("side_data_available")) for match in recent),
        },
    }


def comparable_values(window):
    if not window:
        return {}
    return {**window.get("averages", {}), "win_rate": window.get("win_rate")}


def comparison(left, right):
    left_values = comparable_values(left)
    right_values = comparable_values(right)
    return {
        metric: round(left_values[metric] - right_values[metric], 3)
        if isinstance(left_values.get(metric), (int, float)) and isinstance(right_values.get(metric), (int, float))
        else None
        for metric in COMPARISON_METRICS
    }


def recent_vs_previous(history, amount=5):
    if len(history) < amount * 2:
        return None
    recent = rolling_stats(history[-amount:], amount)
    previous = rolling_stats(history[-amount * 2:-amount], amount)
    return {"recent_games": amount, "previous_games": amount, "deltas": comparison(recent, previous)}


def latest_vs_baselines(latest, windows):
    result = {}
    for name, window in windows.items():
        baseline = comparable_values(window)
        result[name] = {
            metric: round(latest[metric] - baseline[metric], 3)
            if isinstance(latest.get(metric), (int, float)) and isinstance(baseline.get(metric), (int, float))
            else None
            for metric in COMPARISON_METRICS
            if metric != "win_rate"
        }
    return result


def popoff_comparison(latest):
    return {
        metric: round(latest[metric] - POPOFF[metric], 3)
        if isinstance(latest.get(metric), (int, float)) and isinstance(POPOFF.get(metric), (int, float))
        else None
        for metric in ("kills", "deaths", "assists", "kd", "acs", "adr", "hs", "kills_per_round", "deaths_per_round")
    }


def personal_best_context(history):
    latest = history[-1]
    result = {}
    for metric in ("performance_score", "kills", "kd", "acs", "adr", "hs", "kast", "dd_delta_round", "kills_per_round", "first_duel_diff"):
        metric_values = values(history, metric)
        latest_value = latest.get(metric)
        if not metric_values or not isinstance(latest_value, (int, float)):
            continue
        best = max(metric_values)
        rank = sorted(metric_values, reverse=True).index(latest_value) + 1
        result[metric] = {
            "latest": latest_value,
            "personal_best": best,
            "rank": rank,
            "games_with_data": len(metric_values),
            "is_personal_best": latest_value == best,
            "near_personal_best": best > 0 and latest_value >= best * 0.95,
        }
    return result


def public_match(match, include_key=False):
    public = {field: match.get(field) for field in PUBLIC_MATCH_FIELDS}
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
    history = history[-HISTORY_LIMIT:]
    windows = {str(amount): rolling_stats(history, amount) for amount in (5, 10, 20)}
    comparisons = {
        "5_vs_10": comparison(windows["5"], windows["10"]),
        "5_vs_20": comparison(windows["5"], windows["20"]),
        "10_vs_20": comparison(windows["10"], windows["20"]),
        "recent_5_vs_previous_5": recent_vs_previous(history, 5),
    }
    write_json_if_changed(
        PUBLIC_HISTORY_FILE,
        {"schema_version": 2, "matches": [public_match(match, include_key=True) for match in history]},
    )
    write_json_if_changed(
        PUBLIC_LATEST_FILE,
        {
            "schema_version": 2,
            "reference_popoff": POPOFF,
            "latest_match": public_match(history[-1]),
            "latest_vs_baselines": latest_vs_baselines(history[-1], windows),
            "latest_vs_popoff": popoff_comparison(history[-1]),
            "personal_best_context": personal_best_context(history),
            "rolling": windows,
            "window_comparisons": comparisons,
        },
    )


def summary(stats):
    score = stats.get("round_score") or {}
    return f"""
==============================
{stats['result']} {score.get('won', '?')}-{score.get('lost', '?')} | {stats['map']} | {stats['agent']}
==============================

PERFORMANCE: {stats['performance_label']} ({stats['performance_score']}/100)
K/D/A: {stats['kills']}/{stats['deaths']}/{stats['assists']} | KD {stats['kd']}
ACS {stats['acs']} | ADR {stats['adr']} | HS {stats['hs']}% | KAST {stats.get('kast')}%
DDΔ/R {stats['dd_delta_round']} | K/R {stats['kills_per_round']} | D/R {stats['deaths_per_round']}
Opening duels: {stats.get('first_kills')}-{stats.get('first_deaths')} ({stats.get('first_duel_success_rate')}%)

Reference: Breeze Reyna 35/11/2 | 398 ACS | 265 ADR | 19.6% HS
AFK-adjusted working estimate: ~38/8
""".strip()


def rolling_report(history):
    lines = ["", "==============================", "ROLLING PERFORMANCE", "=============================="]
    for amount in (5, 10, 20):
        window = rolling_stats(history, amount)
        averages = window["averages"]
        lines.extend(
            [
                "",
                f"LAST {window['games']} GAMES (target window: {amount})",
                f"Performance {averages['performance_score']} | Win rate {window['win_rate']}%",
                f"KD {averages['kd']} | ACS {averages['acs']} | ADR {averages['adr']} | DDΔ/R {averages['dd_delta_round']}",
                f"HS {averages['hs']}% | KAST {averages['kast']}% | K/R {averages['kills_per_round']} | D/R {averages['deaths_per_round']}",
                f"Opening diff {averages['first_duel_diff']} | success {averages['first_duel_success_rate']}%",
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
    parser.add_argument("--once", action="store_true", help="check once and exit")
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
