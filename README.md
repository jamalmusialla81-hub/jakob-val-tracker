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
output is written to `stats/latest.json`; it contains only allow-listed match
metrics and rolling 5, 10, and 20 game summaries. Raw match IDs, player IDs,
account details, and the API key are never included.

The `Update rolling stats` GitHub Actions workflow checks every 15 minutes and
pushes only when the exported stats change. Add `HENRIK_API_KEY` as a repository
Actions secret before running the workflow.
