# Jakob VAL Tracker

Background match tracker for King#Jakob.

## Reference pop-off

Breeze — Reyna

- 35/11/2
- 398 ACS
- 265 ADR
- 19.6% HS
- 78% KAST
- +149 DD Delta
- AFK-adjusted estimate: ~38/8

## Setup

Install dependencies:

    pip install -r requirements.txt

Copy `.env.example` to `.env` and add your HenrikDev API key.

Run:

    python tracker.py

Run one check and exit:

    python tracker.py --once

Private local state is stored in the ignored `data` folder. Sanitized public
output is written to `stats/latest.json`; it contains only allow-listed coaching
metrics. Raw match IDs, player IDs, account details, player names/tags, and the
API key are never included.

The public export includes:

- result, round score, map, agent, K/D/A, KD, ACS, ADR, HS%, KAST, DDΔ/R,
  damage, survival, opening-duel and per-round combat metrics
- attack and defence splits derived from spike-side evidence and side switches
- weapon kills, ability usage, economy, multikill rounds and kill participation
- rolling 5, 10 and 20 game averages, variance and consistency
- window-over-window changes, best/worst games, map and agent summaries,
  personal-best context and comparison with the Breeze Reyna reference game

`stats/history.json` keeps up to 100 sanitized matches so the rolling 20-game
view grows over time. Event and side coverage counts make older or unavailable
fields explicit instead of silently treating missing data as zero.

The reference pop-off remains 35/11/2 on Breeze with Reyna, 398 ACS, 265 ADR
and 19.6% HS. The AFK-adjusted working estimate remains approximately 38/8.

The `Update rolling stats` GitHub Actions workflow checks every 15 minutes and
pushes only when the exported stats change. Add `HENRIK_API_KEY` as a repository
Actions secret before running the workflow.
