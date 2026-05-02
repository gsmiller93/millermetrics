#!/usr/bin/env python3
"""
#MillerMetrics — ESPN Data Fetcher
===================================
Fetches all historical data from your ESPN private fantasy league
and injects it into index.html.

LOCAL USE:
  1. Install Python 3 and `pip install -r requirements.txt`
  2. Copy `.env.example` to `.env` and fill in ESPN_S2 / SWID / LEAGUE_ID
  3. Run: `python scripts/update.py`
  4. Open index.html in your browser

GITHUB ACTIONS:
  ESPN_S2, SWID, and LEAGUE_ID come from repository secrets.
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
try:
    LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "683667"))
except ValueError:
    print("ERROR: LEAGUE_ID must be an integer.")
    sys.exit(1)

if not ESPN_S2 or not SWID:
    print("ERROR: ESPN_S2 and SWID must be set (via .env locally or GitHub Secrets in CI).")
    sys.exit(1)

ESPN_API_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
FIRST_SEASON  = 2013
HTML_FILE     = str(_REPO_ROOT / "index.html")
# ============================================================

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

def fetch_season(year):
    """Fetch one season from ESPN. Returns parsed JSON or None."""
    if year >= 2018:
        url = f"{ESPN_API_BASE}/seasons/{year}/segments/0/leagues/{LEAGUE_ID}"
    else:
        url = f"{ESPN_API_BASE}/leagueHistory/{LEAGUE_ID}"

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

    # Build team map: teamId -> info
    teams = {}
    for t in teams_raw:
        owners = t.get("owners") or [t.get("primaryOwner", "")]
        owner_name = get_member_name(owners[0], members) if members and owners else (
            f"{t.get('location','')} {t.get('nickname','')}".strip()
        )
        rec = t.get("record", {}).get("overall", {})
        rank = t.get("rankCalculatedFinal") or t.get("playoffSeed") or 0
        teams[t["id"]] = {
            "owner": owner_name,
            "team_name": f"{t.get('location','')} {t.get('nickname','')}".strip(),
            "wins":   rec.get("wins", 0),
            "losses": rec.get("losses", 0),
            "pf": round(float(t.get("points", 0) or 0), 2),
            "pa": round(float(t.get("pointsAgainst", 0) or 0), 2),
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

        matchups.append({
            "period":       period,
            "is_playoff":   period > reg_periods,
            "is_multiweek": period in multiweek_periods,
            "home_id":      home_id,
            "away_id":      away_id,
            "home_pts":     home_pts,
            "away_pts":     away_pts,
            "winner":       "home" if winner_flag == "HOME" else "away",
        })

    return {
        "year":        year,
        "teams":       teams,
        "matchups":    matchups,
        "reg_periods": reg_periods,
    }


def build_dataset(seasons_data):
    """
    Combine all processed seasons into the data structure the HTML expects.
    """
    # owner_name -> lifetime aggregate
    player_agg = {}  # name -> {wins, losses, pf, pa, h2h: {opp: wins}}

    seasons_ui = {}  # year -> [standings rows]
    all_matchups = []  # flat list of every game for records computation

    def ensure_player(name):
        if name not in player_agg:
            player_agg[name] = {"wins": 0, "losses": 0, "pf": 0.0, "pa": 0.0, "h2h": {}}

    for s in seasons_data:
        year   = s["year"]
        teams  = s["teams"]

        # Season standings for UI
        standings = []
        for tid, t in teams.items():
            ensure_player(t["owner"])
            player_agg[t["owner"]]["wins"]   += t["wins"]
            player_agg[t["owner"]]["losses"] += t["losses"]
            player_agg[t["owner"]]["pf"]     += t["pf"]
            player_agg[t["owner"]]["pa"]     += t["pa"]
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

        # Per-matchup H2H tracking
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
            else:
                w_owner, l_owner = away_owner, home_owner
                w_pts,   l_pts   = m["away_pts"], m["home_pts"]

            h2h = player_agg[w_owner]["h2h"]
            h2h[l_owner] = h2h.get(l_owner, 0) + 1

            all_matchups.append({
                "year":         s["year"],
                "period":       m["period"],
                "is_playoff":   m["is_playoff"],
                "is_multiweek": m.get("is_multiweek", False),
                "winner":       w_owner,
                "loser":        l_owner,
                "winner_pts":   w_pts,
                "loser_pts":    l_pts,
                "margin":       round(w_pts - l_pts, 2),
            })

    # Build player list sorted by win %
    players = []
    for name, agg in player_agg.items():
        g = agg["wins"] + agg["losses"]
        players.append({
            "name":   name,
            "wins":   agg["wins"],
            "losses": agg["losses"],
            "games":  g,
            "pf":     round(agg["pf"], 2),
            "pa":     round(agg["pa"], 2),
            "ppg":    round(agg["pf"] / g, 2) if g else 0,
            "pct":    round(agg["wins"] / g, 4) if g else 0,
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

    # Compute some records
    records_raw = {}
    if all_matchups:
        completed = [m for m in all_matchups if m["winner_pts"] > 0]
        # Exclude multi-week matchups from single-game records — those
        # accumulate points across two weeks and would inflate the numbers.
        single_week = [m for m in completed if not m.get("is_multiweek")]
        eligible = single_week if single_week else completed  # fallback if all were multiweek
        if eligible:
            high = max(eligible, key=lambda x: x["winner_pts"])
            big_margin = max(eligible, key=lambda x: x["margin"])
            records_raw["highest_score"] = {
                "pts": high["winner_pts"], "player": high["winner"],
                "year": high["year"], "period": high["period"]
            }
            records_raw["largest_margin"] = {
                "margin": big_margin["margin"],
                "winner": big_margin["winner"], "loser": big_margin["loser"],
                "year": big_margin["year"], "period": big_margin["period"]
            }

    # Per-season win% records
    best_season = {"pct": 0, "player": "", "year": 0}
    worst_season = {"pct": 1, "player": "", "year": 9999}
    for year, rows in seasons_ui.items():
        for row in rows:
            g = row["w"] + row["l"]
            if g < 8:
                continue  # skip short stints
            p = row["w"] / g
            if p > best_season["pct"]:
                best_season = {"pct": p, "player": row["owner"], "year": year}
            if p < worst_season["pct"]:
                worst_season = {"pct": p, "player": row["owner"], "year": year}
    records_raw["best_season_pct"] = best_season
    records_raw["worst_season_pct"] = worst_season

    return {
        "players":    players,
        "h2h_names":  h2h_names,
        "h2h_matrix": h2h_matrix,
        "seasons":    {str(k): v for k, v in seasons_ui.items()},
        "records_raw": records_raw,
        "fetched_at": datetime.now().isoformat(),
        "league_id":  LEAGUE_ID,
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
        html = re.sub(r'<script id="espn-data">.*?</script>', block, html, flags=re.DOTALL)
    else:
        # Inject right before the main script block so ESPN_DATA is defined
        # before loadData() runs
        html = html.replace('<!-- ESPN_DATA_INJECT -->', f'{block}\n<!-- ESPN_DATA_INJECT -->')

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    print("🏈  #MillerMetrics — ESPN Data Fetcher")
    print("=" * 45)
    print(f"League ID : {LEAGUE_ID}")
    print(f"Seasons   : {FIRST_SEASON} – {CURRENT_YEAR}")
    print()

    # Quick auth check before looping all seasons
    print("🔐  Checking ESPN authentication...", end=" ", flush=True)
    test_url = f"{ESPN_API_BASE}/seasons/2024/segments/0/leagues/{LEAGUE_ID}"
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
    print()

    processed = []
    for year in range(FIRST_SEASON, CURRENT_YEAR + 1):
        print(f"  {year} ... ", end="", flush=True)
        raw = fetch_season(year)
        if not raw:
            print("skipped (no data)")
            continue
        season = process_season(year, raw)
        if not season or not season["teams"]:
            print("skipped (empty)")
            continue
        n_teams = len(season["teams"])
        n_games = len(season["matchups"])
        print(f"✓  {n_teams} teams · {n_games} matchups")
        processed.append(season)
        time.sleep(0.3)  # be polite to ESPN's servers

    if not processed:
        print("\n❌  No data fetched. Check your credentials and league ID.")
        sys.exit(1)

    print(f"\n📊  Building dataset from {len(processed)} seasons...")
    data = build_dataset(processed)

    total_games = sum(len(s["matchups"]) for s in processed)
    print(f"   {len(data['players'])} managers · {total_games} total matchups")

    print(f"\n💾  Injecting into {HTML_FILE}...")
    inject_into_html(data)

    print(f"\nDone. Open index.html in your browser.")
    print(f"Data fetched at: {data['fetched_at'][:19].replace('T', ' ')}")


if __name__ == "__main__":
    main()
