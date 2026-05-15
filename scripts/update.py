#!/usr/bin/env python3
"""
#MillerMetrics — ESPN Data Fetcher
===================================
Fetches all historical data from the configured ESPN private fantasy
leagues (see LEAGUES below) and injects a combined blob into index.html.

LOCAL USE:
  1. Install Python 3 and `pip install -r requirements.txt`
  2. Copy `.env.example` to `.env` and fill in ESPN_S2 / SWID
  3. Run: `python scripts/update.py`
  4. Open index.html in your browser

GITHUB ACTIONS:
  ESPN_S2 and SWID come from repository secrets. League IDs are public
  and live in the LEAGUES list below — no secret needed.
  See .github/workflows/update.yml.

CREDENTIAL REFRESH:
  ESPN cookies expire every few months. Grab fresh values from Chrome
  DevTools (Application > Cookies > fantasy.espn.com) and update the
  GitHub secrets (and your local .env if you run it locally).
"""

import json
import os
import re
import time
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: Missing 'requests' library. Run: pip install -r requirements.txt")
    sys.exit(1)

from urllib.parse import unquote

# Load .env if present (only matters for local runs; CI sets env vars directly)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _REPO_ROOT / ".env"
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ============================================================
# CONFIGURATION (loaded from environment)
# ============================================================
ESPN_S2 = os.environ.get("ESPN_S2", "").strip()
SWID    = os.environ.get("SWID", "").strip()

if not ESPN_S2 or not SWID:
    print("ERROR: ESPN_S2 and SWID must be set (via .env locally or GitHub Secrets in CI).")
    sys.exit(1)

ESPN_API_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
HTML_FILE     = str(_REPO_ROOT / "index.html")

# League IDs are public, not secrets — keep them in code.
# `fallback_2014` flag controls whether to splice the spreadsheet-derived 2014
# games (only West the Box God is missing that year from ESPN history).
# `first_season` lets us skip years before the league existed.
LEAGUES = [
    {
        "key":           "west_the_box",
        "id":            683667,
        "name":          "West the Box God",
        "emoji":         "📦",
        "first_season":  2013,
        "fallback_2014": True,
        # West existed before its current 12-owner core; cap the "Most Playoff
        # Wins" record at 2017 so it reflects the current roster.
        "playoff_records_from": 2017,
    },
    {
        "key":           "jeepers",
        "id":            242712,
        "name":          "Jeepers Keepers",
        "emoji":         "👺",
        "first_season":  2016,
        "fallback_2014": False,
        # Jeepers' current roster has been there since the league started,
        # so "Most Playoff Wins" goes all the way back.
        "playoff_records_from": 2016,
    },
]
# ============================================================

# 2014 isn't reachable from this account's ESPN history. The per-game
# data below is transcribed from the #MillerMetrics spreadsheet
# (Google Drive: Fantasy Football > #MillerMetrics, MatchHistoryRaw tab).
# Period 13 = Playoff R1 (multi-week, NFL Wks 13-14). Period 14 = Playoff R2.
# Treating playoff rounds as multi-week excludes them from single-game records
# (consistent with how multi-week ESPN matchups are handled for newer seasons).
FALLBACK_2014_RANKS = {
    "Herman Ryals": 1, "Chase Randolph": 2, "Frank Gibase": 3, "Dylan Kiess": 4,
    "Westly Shealy": 5, "Dominic Reinecker": 6, "Alex Bedillion": 7,
    "Daniel McElhannon": 8, "Scott Marburger": 9, "Justin Floody": 10,
    "Joshua Partridge": 11, "Stan Stevens": 12,
}
FALLBACK_2014_MATCHUPS = [
    # (period, winner, loser, winner_pts, loser_pts)
    (1,  "Chase Randolph",    "Dylan Kiess",        122, 81),
    (1,  "Herman Ryals",      "Justin Floody",      108, 79),
    (1,  "Frank Gibase",      "Daniel McElhannon",  103, 95),
    (1,  "Westly Shealy",     "Dominic Reinecker",  102, 65),
    (1,  "Joshua Partridge",  "Stan Stevens",        80, 51),
    (1,  "Alex Bedillion",    "Scott Marburger",     86, 82),
    (2,  "Westly Shealy",     "Daniel McElhannon",   80, 32),
    (2,  "Scott Marburger",   "Joshua Partridge",    95, 72),
    (2,  "Alex Bedillion",    "Justin Floody",       93, 87),
    (2,  "Frank Gibase",      "Dylan Kiess",         83, 72),
    (2,  "Herman Ryals",      "Stan Stevens",        93, 83),
    (2,  "Chase Randolph",    "Dominic Reinecker",   80, 49),
    (3,  "Herman Ryals",      "Scott Marburger",    108, 67),
    (3,  "Dominic Reinecker", "Daniel McElhannon",   97, 89),
    (3,  "Westly Shealy",     "Dylan Kiess",         93, 58),
    (3,  "Joshua Partridge",  "Alex Bedillion",      85, 64),
    (3,  "Justin Floody",     "Stan Stevens",        82, 66),
    (3,  "Chase Randolph",    "Frank Gibase",        77, 63),
    (4,  "Daniel McElhannon", "Chase Randolph",     137, 113),
    (4,  "Westly Shealy",     "Frank Gibase",        94, 87),
    (4,  "Dylan Kiess",       "Dominic Reinecker",  121, 89),
    (4,  "Justin Floody",     "Joshua Partridge",    92, 62),
    (4,  "Scott Marburger",   "Stan Stevens",        76, 55),
    (4,  "Alex Bedillion",    "Herman Ryals",        68, 48),
    (5,  "Herman Ryals",      "Joshua Partridge",   144, 77),
    (5,  "Stan Stevens",      "Alex Bedillion",      95, 60),
    (5,  "Chase Randolph",    "Westly Shealy",       98, 74),
    (5,  "Dominic Reinecker", "Frank Gibase",       104, 83),
    (5,  "Daniel McElhannon", "Dylan Kiess",         72, 62),
    (5,  "Scott Marburger",   "Justin Floody",       77, 72),
    (6,  "Scott Marburger",   "Westly Shealy",      119, 82),
    (6,  "Justin Floody",     "Chase Randolph",     110, 106),
    (6,  "Herman Ryals",      "Dylan Kiess",        102, 99),
    (6,  "Joshua Partridge",  "Daniel McElhannon",   96, 59),
    (6,  "Frank Gibase",      "Stan Stevens",        84, 63),
    (6,  "Alex Bedillion",    "Dominic Reinecker",   77, 64),
    (7,  "Westly Shealy",     "Joshua Partridge",   118, 59),
    (7,  "Chase Randolph",    "Scott Marburger",    112, 70),
    (7,  "Herman Ryals",      "Dominic Reinecker",   97, 58),
    (7,  "Daniel McElhannon", "Stan Stevens",        93, 81),
    (7,  "Alex Bedillion",    "Frank Gibase",        84, 76),
    (7,  "Dylan Kiess",       "Justin Floody",       82, 64),
    (8,  "Dominic Reinecker", "Justin Floody",      130, 89),
    (8,  "Chase Randolph",    "Joshua Partridge",   125, 88),
    (8,  "Herman Ryals",      "Frank Gibase",       124, 78),
    (8,  "Stan Stevens",      "Westly Shealy",      123, 103),
    (8,  "Dylan Kiess",       "Scott Marburger",    100, 80),
    (8,  "Alex Bedillion",    "Daniel McElhannon",   81, 71),
    (9,  "Herman Ryals",      "Daniel McElhannon",  112, 62),
    (9,  "Dylan Kiess",       "Joshua Partridge",   111, 96),
    (9,  "Dominic Reinecker", "Scott Marburger",    103, 79),
    (9,  "Chase Randolph",    "Stan Stevens",        82, 47),
    (9,  "Westly Shealy",     "Alex Bedillion",      82, 79),
    (9,  "Frank Gibase",      "Justin Floody",       92, 56),
    (10, "Herman Ryals",      "Westly Shealy",      145, 54),
    (10, "Justin Floody",     "Daniel McElhannon",  114, 88),
    (10, "Frank Gibase",      "Scott Marburger",    109, 76),
    (10, "Chase Randolph",    "Alex Bedillion",      97, 89),
    (10, "Joshua Partridge",  "Dominic Reinecker",   94, 78),
    (10, "Dylan Kiess",       "Stan Stevens",        86, 80),
    (11, "Dominic Reinecker", "Stan Stevens",       123, 23),
    (11, "Daniel McElhannon", "Scott Marburger",    118, 57),
    (11, "Chase Randolph",    "Herman Ryals",       106, 87),
    (11, "Dylan Kiess",       "Alex Bedillion",      92, 43),
    (11, "Justin Floody",     "Westly Shealy",       74, 61),
    (11, "Frank Gibase",      "Joshua Partridge",    70, 65),
    (12, "Chase Randolph",    "Justin Floody",      121, 65),
    (12, "Scott Marburger",   "Westly Shealy",      115, 94),
    (12, "Herman Ryals",      "Dylan Kiess",        114, 87),
    (12, "Daniel McElhannon", "Joshua Partridge",   106, 87),
    (12, "Frank Gibase",      "Stan Stevens",        98, 76),
    (12, "Dominic Reinecker", "Alex Bedillion",      69, 69),  # 69-69 tie awarded as Dom W per ESPN
    # Playoff R1 (NFL Wks 13-14, multi-week)
    (13, "Dominic Reinecker", "Westly Shealy",      244, 171),
    (13, "Daniel McElhannon", "Alex Bedillion",     234, 179),
    (13, "Chase Randolph",    "Frank Gibase",       222, 180),
    (13, "Joshua Partridge",  "Stan Stevens",       213, 120),
    (13, "Herman Ryals",      "Dylan Kiess",        202, 171),
    (13, "Scott Marburger",   "Justin Floody",      172, 163),
    # Playoff R2 (NFL Wks 15-16, multi-week)
    (14, "Dylan Kiess",       "Frank Gibase",       195, 163),
    (14, "Herman Ryals",      "Chase Randolph",     180, 160),
    (14, "Stan Stevens",      "Justin Floody",      161, 143),
    (14, "Scott Marburger",   "Westly Shealy",      159, 139),
    (14, "Alex Bedillion",    "Joshua Partridge",   156, 131),
    (14, "Daniel McElhannon", "Dominic Reinecker",  155, 134),
]

# NFL seasons run Sept–Jan. If we're before September, the current
# calendar year has no season yet — use the previous year.
_now = datetime.now()
CURRENT_YEAR = _now.year if _now.month >= 9 else _now.year - 1

# Build a session that looks like a real browser to ESPN
SESSION = requests.Session()
SESSION.cookies.set('espn_s2', unquote(ESPN_S2), domain='.espn.com')
SESSION.cookies.set('SWID',    SWID,              domain='.espn.com')
SESSION.headers.update({
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         'https://fantasy.espn.com/',
    'Origin':          'https://fantasy.espn.com',
    'x-fantasy-source': 'kona',
    'x-fantasy-filter': '{}',
})

def fetch_season(year, league_id):
    """Fetch one season from ESPN. Returns parsed JSON or None."""
    if year >= 2018:
        url = f"{ESPN_API_BASE}/seasons/{year}/segments/0/leagues/{league_id}"
    else:
        url = f"{ESPN_API_BASE}/leagueHistory/{league_id}"

    # Fetch each view separately and merge — ESPN sometimes returns 202/empty
    # when multiple views are requested together
    views = ["mMatchup", "mMatchupScore", "mTeam", "mSettings"]
    merged = {}
    for view in views:
        params = {"view": view} if year >= 2018 else {"seasonId": year, "view": view}
        try:
            r = SESSION.get(url, params=params, timeout=15)
            if r.status_code == 404 or not r.text.strip():
                continue
            if "<!DOCTYPE" in r.text[:100]:
                return None
            data = r.json()
            if isinstance(data, list):
                data = data[0]
            merged.update(data)
        except Exception:
            continue

    return merged if merged else None


def get_member_name(member_id, members):
    """Resolve a member ID to 'First Last'."""
    for m in members:
        if m.get("id") == member_id:
            fn = (m.get("firstName") or "").strip()
            ln = (m.get("lastName") or "").strip()
            return f"{fn} {ln}".strip() or m.get("displayName", "Unknown")
    return "Unknown"


def process_season(year, data):
    """
    Turn raw ESPN JSON into structured season data.
    Returns dict with 'year', 'teams', 'matchups', 'reg_periods'.
    """
    if not data:
        return None

    members  = data.get("members", [])
    teams_raw = data.get("teams", [])
    schedule = data.get("schedule", [])
    settings = data.get("settings", {})
    sched_s  = settings.get("scheduleSettings", {})
    reg_periods = sched_s.get("matchupPeriodCount", 13)

    # Build team map: teamId -> info.
    # ESPN reports PF/PA in several places depending on season/view — try them
    # in order of reliability. The matchup loop below also derives PA from
    # opponent scores, used as a last-resort fallback if all fields are zero.
    def first_nonzero(*vals):
        for v in vals:
            try:
                f = float(v or 0)
            except (TypeError, ValueError):
                continue
            if f:
                return f
        return 0.0

    teams = {}
    for t in teams_raw:
        owners = t.get("owners") or [t.get("primaryOwner", "")]
        owner_name = get_member_name(owners[0], members) if members and owners else (
            f"{t.get('location','')} {t.get('nickname','')}".strip()
        )
        rec  = t.get("record", {}).get("overall", {})
        vals = t.get("valuesByStat", {}) or {}
        rank = t.get("rankCalculatedFinal") or t.get("playoffSeed") or 0

        pf_val = first_nonzero(
            rec.get("pointsFor"),
            t.get("points"),
            vals.get("pointsFor"),
        )
        pa_val = first_nonzero(
            rec.get("pointsAgainst"),
            t.get("pointsAgainst"),
            vals.get("pointsAgainst"),
        )

        teams[t["id"]] = {
            "owner": owner_name,
            "team_name": f"{t.get('location','')} {t.get('nickname','')}".strip(),
            "wins":   rec.get("wins", 0),
            "losses": rec.get("losses", 0),
            "pf": round(pf_val, 2),
            "pa": round(pa_val, 2),
            "pa_from_matchups": 0.0,  # accumulated below; used only if ESPN returned 0
            "rank": rank,
        }

    # Detect multi-week matchup periods using scheduleSettings.matchupPeriods
    # This is a dict like {"1": [1], "2": [2], "15": [15, 16]} — if the list
    # has more than one scoring period, it's a multi-week matchup.
    matchup_periods_map = sched_s.get("matchupPeriods", {})
    multiweek_periods = set()
    for period_id_str, scoring_periods in matchup_periods_map.items():
        if isinstance(scoring_periods, list) and len(scoring_periods) > 1:
            multiweek_periods.add(int(period_id_str))

    # Process matchup schedule
    matchups = []
    for game in schedule:
        home = game.get("home") or {}
        away = game.get("away") or {}
        winner_flag = game.get("winner", "UNDECIDED")
        if winner_flag == "UNDECIDED" or not home or not away:
            continue

        period   = game.get("matchupPeriodId", 0)
        home_id  = home.get("teamId")
        away_id  = away.get("teamId")
        home_pts = round(float(home.get("totalPoints", 0) or 0), 2)
        away_pts = round(float(away.get("totalPoints", 0) or 0), 2)

        if home_id in teams:
            teams[home_id]["pa_from_matchups"] += away_pts
        if away_id in teams:
            teams[away_id]["pa_from_matchups"] += home_pts

        matchups.append({
            "period":       period,
            "is_playoff":   period > reg_periods,
            "is_multiweek": period in multiweek_periods,
            "playoff_tier": game.get("playoffTierType") or "NONE",
            "home_id":      home_id,
            "away_id":      away_id,
            "home_rank":    teams.get(home_id, {}).get("rank"),
            "away_rank":    teams.get(away_id, {}).get("rank"),
            "home_pts":     home_pts,
            "away_pts":     away_pts,
            "winner":       "home" if winner_flag == "HOME" else "away",
        })

    # Fall back to matchup-derived PA only when ESPN returned 0 for this team.
    for tid, t in teams.items():
        if t["pa"] == 0 and t["pa_from_matchups"]:
            t["pa"] = round(t["pa_from_matchups"], 2)
        del t["pa_from_matchups"]

    return {
        "year":        year,
        "teams":       teams,
        "matchups":    matchups,
        "reg_periods": reg_periods,
    }


def build_dataset(seasons_data, splice_2014=False, playoff_records_from=2017):
    """
    Combine all processed seasons into the data structure the HTML expects.

    Lifetime player aggregates (wins/losses/PF/PA/H2H/playoff_wins) are
    derived from the all_matchups list — every game counts. Per-season
    standings tables stay in sync with ESPN's record.overall (regular season
    only) so per-year W-L matches what users see in ESPN.

    `splice_2014`: if True, splice spreadsheet-derived 2014 matchups into the
    dataset (used for West the Box God which is missing 2014 from ESPN).
    `playoff_records_from`: earliest year that counts toward "Most Playoff
    Wins" — per-league because some leagues had earlier rosters/brackets
    that aren't comparable to the current setup.
    """
    PLAYOFF_RECORDS_FROM_YEAR = playoff_records_from

    player_agg = {}     # name -> aggregate dict (filled in step 3 below)
    seasons_ui = {}     # year -> [standings rows]
    all_matchups = []   # flat chronological list of every game

    def ensure_player(name):
        if name not in player_agg:
            player_agg[name] = {
                "wins": 0, "losses": 0,
                "playoff_wins": 0, "playoff_losses": 0,
                "pf": 0.0, "pa": 0.0,
                "h2h": {},
            }

    # ---- Step 1: API seasons → standings table + matchup list ----
    for s in seasons_data:
        year  = s["year"]
        teams = s["teams"]

        standings = []
        for tid, t in teams.items():
            ensure_player(t["owner"])
            standings.append({
                "rank":  t["rank"],
                "owner": t["owner"],
                "team":  t["team_name"],
                "w":     t["wins"],
                "l":     t["losses"],
                "pf":    t["pf"],
                "pa":    t["pa"],
            })
        standings.sort(key=lambda x: x["rank"])
        seasons_ui[year] = standings

        for m in s["matchups"]:
            home_owner = teams.get(m["home_id"], {}).get("owner")
            away_owner = teams.get(m["away_id"], {}).get("owner")
            if not home_owner or not away_owner:
                continue
            ensure_player(home_owner)
            ensure_player(away_owner)

            if m["winner"] == "home":
                w_owner, l_owner = home_owner, away_owner
                w_pts,   l_pts   = m["home_pts"], m["away_pts"]
                w_rank,  l_rank  = m.get("home_rank"), m.get("away_rank")
            else:
                w_owner, l_owner = away_owner, home_owner
                w_pts,   l_pts   = m["away_pts"], m["home_pts"]
                w_rank,  l_rank  = m.get("away_rank"), m.get("home_rank")

            # ESPN tags playoff games via playoffTierType. The 3rd-place
            # game shares the WINNERS_CONSOLATION_LADDER tier with 5/6
            # placement games; identify it as the WCL game whose
            # participants finished as ranks 3 and 4.
            tier = m.get("playoff_tier", "NONE")
            is_champ = False
            if year >= PLAYOFF_RECORDS_FROM_YEAR:
                if tier == "WINNERS_BRACKET":
                    is_champ = True
                elif tier == "WINNERS_CONSOLATION_LADDER" and {w_rank, l_rank} == {3, 4}:
                    is_champ = True

            all_matchups.append({
                "year":                    year,
                "period":                  m["period"],
                "is_playoff":              m["is_playoff"],
                "is_multiweek":            m.get("is_multiweek", False),
                "is_championship_bracket": is_champ,
                "winner":                  w_owner,
                "loser":                   l_owner,
                "winner_pts":              w_pts,
                "loser_pts":               l_pts,
                "margin":                  round(w_pts - l_pts, 2),
            })

    # ---- Step 2: 2014 splice (hand-coded matchups; pre-2017, no playoff tagging) ----
    if splice_2014 and 2014 not in seasons_ui and FALLBACK_2014_MATCHUPS:
        teams_2014 = {}
        for period, winner, loser, w_pts, l_pts in FALLBACK_2014_MATCHUPS:
            is_playoff = period > 12
            for owner in (winner, loser):
                ensure_player(owner)
                teams_2014.setdefault(owner, {"wins": 0, "losses": 0, "pf": 0.0, "pa": 0.0})
            teams_2014[winner]["pf"] += w_pts
            teams_2014[winner]["pa"] += l_pts
            teams_2014[loser]["pf"]  += l_pts
            teams_2014[loser]["pa"]  += w_pts
            if not is_playoff:
                teams_2014[winner]["wins"]   += 1
                teams_2014[loser]["losses"]  += 1
            all_matchups.append({
                "year":                    2014,
                "period":                  period,
                "is_playoff":              is_playoff,
                "is_multiweek":            is_playoff,
                "is_championship_bracket": False,  # 2014 < PLAYOFF_RECORDS_FROM_YEAR
                "winner":                  winner,
                "loser":                   loser,
                "winner_pts":              float(w_pts),
                "loser_pts":               float(l_pts),
                "margin":                  float(w_pts - l_pts),
            })
        standings_2014 = [{
            "rank":  FALLBACK_2014_RANKS.get(owner, 99),
            "owner": owner,
            "team":  "",
            "w":     agg["wins"],
            "l":     agg["losses"],
            "pf":    round(agg["pf"], 2),
            "pa":    round(agg["pa"], 2),
        } for owner, agg in teams_2014.items()]
        standings_2014.sort(key=lambda x: x["rank"])
        seasons_ui[2014] = standings_2014

    # ---- Step 3: derive lifetime aggregates from all_matchups ----
    # Every game counts toward lifetime W/L/PF/PA/H2H regardless of tier.
    # Championship-bracket games (only flagged for >= PLAYOFF_RECORDS_FROM_YEAR)
    # additionally feed playoff_wins / playoff_losses.
    for m in all_matchups:
        w, l = m["winner"], m["loser"]
        player_agg[w]["wins"]   += 1
        player_agg[l]["losses"] += 1
        player_agg[w]["pf"]     += m["winner_pts"]
        player_agg[w]["pa"]     += m["loser_pts"]
        player_agg[l]["pf"]     += m["loser_pts"]
        player_agg[l]["pa"]     += m["winner_pts"]
        player_agg[w]["h2h"][l]  = player_agg[w]["h2h"].get(l, 0) + 1
        if m.get("is_championship_bracket"):
            player_agg[w]["playoff_wins"]   += 1
            player_agg[l]["playoff_losses"] += 1

    # Build player list sorted by win %
    players = []
    for name, agg in player_agg.items():
        g = agg["wins"] + agg["losses"]
        players.append({
            "name":           name,
            "wins":           agg["wins"],
            "losses":         agg["losses"],
            "playoff_wins":   agg["playoff_wins"],
            "playoff_losses": agg["playoff_losses"],
            "games":          g,
            "pf":             round(agg["pf"], 2),
            "pa":             round(agg["pa"], 2),
            "ppg":            round(agg["pf"] / g, 2) if g else 0,
            "pct":            round(agg["wins"] / g, 4) if g else 0,
        })
    players.sort(key=lambda x: (-x["pct"], -x["wins"]))

    # H2H matrix (ordered same as players list)
    h2h_names = [p["name"] for p in players]
    h2h_matrix = []
    for p1 in h2h_names:
        row = []
        for p2 in h2h_names:
            row.append(player_agg.get(p1, {}).get("h2h", {}).get(p2, 0))
        h2h_matrix.append(row)

    # ---------- Records ----------
    records_raw = {}

    # Single-game records: exclude multi-week matchups (their totals span two
    # scoring periods and would inflate the numbers).
    if all_matchups:
        completed = [m for m in all_matchups if m["winner_pts"] > 0 and m["loser_pts"] > 0]
        single_week = [m for m in completed if not m.get("is_multiweek")]
        eligible = single_week if single_week else completed
        if eligible:
            high = max(eligible, key=lambda x: x["winner_pts"])
            low  = min(eligible, key=lambda x: x["loser_pts"])
            big_margin = max(eligible, key=lambda x: x["margin"])
            records_raw["highest_score"] = {
                "pts": high["winner_pts"], "player": high["winner"],
                "year": high["year"], "period": high["period"],
            }
            records_raw["lowest_score"] = {
                "pts": low["loser_pts"], "player": low["loser"],
                "opponent": low["winner"],
                "year": low["year"], "period": low["period"],
            }
            records_raw["largest_margin"] = {
                "margin": big_margin["margin"],
                "winner": big_margin["winner"], "loser": big_margin["loser"],
                "year": big_margin["year"], "period": big_margin["period"],
            }

    # Per-season win% / PF / PA records (8+ games, so partial seasons don't dominate)
    best_season  = {"pct": 0, "player": "", "year": 0}
    worst_season = {"pct": 1, "player": "", "year": 9999}
    best_pf      = {"pf": 0, "player": "", "year": 0}
    worst_pa     = {"pa": 0, "player": "", "year": 0}  # "highest PA" = unluckiest season
    for year, rows in seasons_ui.items():
        for row in rows:
            g = row["w"] + row["l"]
            if g < 8:
                continue
            p = row["w"] / g
            if p > best_season["pct"]:
                best_season = {"pct": p, "player": row["owner"], "year": year}
            if p < worst_season["pct"]:
                worst_season = {"pct": p, "player": row["owner"], "year": year}
            if row["pf"] > best_pf["pf"]:
                best_pf = {"pf": row["pf"], "player": row["owner"], "year": year}
            if row["pa"] > worst_pa["pa"]:
                worst_pa = {"pa": row["pa"], "player": row["owner"], "year": year}
    records_raw["best_season_pct"]  = best_season
    records_raw["worst_season_pct"] = worst_season
    records_raw["best_season_pf"]   = best_pf
    records_raw["worst_season_pa"]  = worst_pa

    # All-time wins leader — every game counts (reg + playoff + consolation).
    if players:
        leader = max(players, key=lambda p: p["wins"])
        records_raw["wins_leader"] = {
            "wins": leader["wins"], "games": leader["games"], "player": leader["name"],
        }

    # Most championship-bracket playoff wins (from PLAYOFF_RECORDS_FROM_YEAR
    # onward; includes the 3rd-place game per league rule).
    eligible_playoff = [p for p in players if p["playoff_wins"] > 0]
    if eligible_playoff:
        po_leader = max(eligible_playoff, key=lambda p: p["playoff_wins"])
        records_raw["playoff_wins_leader"] = {
            "wins":     po_leader["playoff_wins"],
            "losses":   po_leader["playoff_losses"],
            "player":   po_leader["name"],
            "since":    PLAYOFF_RECORDS_FROM_YEAR,
        }

    # Longest win streak — chronological walk per owner across API matchups.
    # 2014 is missing, so a streak crossing 2013 → 2015 would be split. Acceptable.
    longest = {"length": 0, "player": "", "start_year": 0, "start_period": 0,
               "end_year": 0, "end_period": 0}
    if all_matchups:
        per_owner = {}
        for m in all_matchups:
            per_owner.setdefault(m["winner"], []).append((m["year"], m["period"], True, m))
            per_owner.setdefault(m["loser"],  []).append((m["year"], m["period"], False, m))
        for name, games in per_owner.items():
            games.sort(key=lambda g: (g[0], g[1]))
            run = 0
            run_start = None
            for yr, pd, won, m in games:
                if won:
                    if run == 0:
                        run_start = (yr, pd)
                    run += 1
                    if run > longest["length"]:
                        longest = {
                            "length": run, "player": name,
                            "start_year": run_start[0], "start_period": run_start[1],
                            "end_year": yr, "end_period": pd,
                        }
                else:
                    run = 0
                    run_start = None
    records_raw["longest_win_streak"] = longest

    # Active owners = anyone in the most recent season's standings. Used by
    # the UI to default the standings tab to current members.
    active_owners = []
    if seasons_ui:
        latest_year = max(seasons_ui.keys())
        active_owners = [row["owner"] for row in seasons_ui[latest_year]]

    return {
        "players":       players,
        "h2h_names":     h2h_names,
        "h2h_matrix":    h2h_matrix,
        "seasons":       {str(k): v for k, v in seasons_ui.items()},
        "records_raw":   records_raw,
        "active_owners": active_owners,
    }


def inject_into_html(data):
    """Write ESPN data into the HTML file as an embedded JS variable."""
    try:
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"ERROR: Could not find {HTML_FILE}.")
        sys.exit(1)

    data_json = json.dumps(data, separators=(",", ":"))
    block = f"<script id=\"espn-data\">window.ESPN_DATA={data_json};</script>"

    if 'id="espn-data"' in html:
        # Use a lambda so `\u...` sequences in the JSON aren't interpreted as
        # regex backreferences in the replacement.
        html = re.sub(r'<script id="espn-data">.*?</script>', lambda _m: block, html, flags=re.DOTALL)
    else:
        # Inject right before the main script block so ESPN_DATA is defined
        # before loadData() runs
        html = html.replace('<!-- ESPN_DATA_INJECT -->', f'{block}\n<!-- ESPN_DATA_INJECT -->')

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)


def check_auth():
    """Quick auth ping against ESPN's API. Exits on failure."""
    print("🔐  Checking ESPN authentication...", end=" ", flush=True)
    # Any league works for the cookie check; use the first configured one.
    test_url = f"{ESPN_API_BASE}/seasons/2024/segments/0/leagues/{LEAGUES[0]['id']}"
    try:
        r = SESSION.get(test_url, params={"view": "mTeam"}, timeout=15)
        print(f"HTTP {r.status_code}")
        if r.status_code in (401, 403):
            print(f"\n❌  ESPN rejected the request ({r.status_code}). Cookies may be expired.\n")
            sys.exit(1)
        if not r.text.strip():
            print(f"❌  Empty response — trying without view param...")
            r = SESSION.get(test_url, timeout=15)
        if "<!DOCTYPE" in r.text[:100]:
            print(f"\n❌  Got HTML — cookies not being accepted by ESPN.")
            print("   Grab fresh espn_s2 + SWID from Chrome DevTools and update the script.\n")
            sys.exit(1)
        r.json()
        print("✓  Authenticated successfully!")
    except Exception as e:
        print(f"\n❌  {e}\n   Status: {r.status_code}, Preview: {r.text[:200]!r}")
        sys.exit(1)


def fetch_league(cfg):
    """Fetch + process all seasons for a single league. Returns league blob."""
    print(f"\n{cfg['emoji']}  {cfg['name']}  (league {cfg['id']})")
    print("-" * 45)

    processed = []
    for year in range(cfg["first_season"], CURRENT_YEAR + 1):
        print(f"  {year} ... ", end="", flush=True)
        raw = fetch_season(year, cfg["id"])
        if not raw:
            print("skipped (no data)")
            continue
        season = process_season(year, raw)
        if not season or not season["teams"]:
            print("skipped (empty)")
            continue
        print(f"✓  {len(season['teams'])} teams · {len(season['matchups'])} matchups")
        processed.append(season)
        time.sleep(0.3)

    if not processed:
        print(f"  ⚠  No data fetched for {cfg['name']}.")
        return None

    data = build_dataset(
        processed,
        splice_2014=cfg["fallback_2014"],
        playoff_records_from=cfg.get("playoff_records_from", 2017),
    )
    total_games = sum(len(s["matchups"]) for s in processed)
    print(f"  📊  {len(data['players'])} managers · {total_games} total matchups")

    return {
        "id":            cfg["id"],
        "name":          cfg["name"],
        "emoji":         cfg["emoji"],
        "first_season":  cfg["first_season"],
        **data,
    }


def main():
    print("🏈  #MillerMetrics — ESPN Data Fetcher")
    print("=" * 45)
    print(f"Leagues   : {', '.join(L['name'] for L in LEAGUES)}")
    print(f"Current   : {CURRENT_YEAR}")
    print()

    check_auth()

    leagues_out = {}
    for cfg in LEAGUES:
        blob = fetch_league(cfg)
        if blob:
            leagues_out[cfg["key"]] = blob

    if not leagues_out:
        print("\n❌  No data fetched for any league. Check credentials.")
        sys.exit(1)

    data = {
        "default":    LEAGUES[0]["key"],
        "fetched_at": datetime.now().isoformat(),
        "leagues":    leagues_out,
    }

    print(f"\n💾  Injecting into {HTML_FILE}...")
    inject_into_html(data)

    print(f"\nDone. Open index.html in your browser.")
    print(f"Data fetched at: {data['fetched_at'][:19].replace('T', ' ')}")


if __name__ == "__main__":
    main()
