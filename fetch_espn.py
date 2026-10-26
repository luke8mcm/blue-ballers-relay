"""Fetch Blue Ballers (ESPN fantasy basketball) data and write compact CSV/JSON
files that a WebFetch-only reader can consume.

Outputs (in ./data):
  free_agents.csv  - FA + waiver players, sorted by season fantasy pts/game
  rosters.csv      - every rostered player on all 8 teams with season/L7/L15 averages
  matchups.csv     - current matchup period scores
  status.json      - when this ran and the current scoring period

Standard library only. Public league, so no cookies are needed.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

LEAGUE_ID = 1840980279
SEASON = 2027
BASE = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/"
        f"{SEASON}/segments/0/leagues/{LEAGUE_ID}")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

PRO_TEAMS = {0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN",
             8: "DET", 9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA",
             15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI", 21: "PHX",
             22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR",
             29: "MEM", 30: "CHA"}
SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 11: "UTIL", 12: "BE", 13: "IR"}
POS_SLOTS = [0, 1, 2, 3, 4]

# League scoring by ESPN stat id:
# 0 PTS, 1 BLK, 2 STL, 3 AST, 6 REB, 11 TO, 13 FGM, 14 FGA, 15 FTM, 16 FTA, 17 3PM
SCORING = {"0": 1, "1": 4, "2": 4, "3": 2, "6": 1, "11": -2, "13": 2, "14": -1,
           "15": 1, "16": -1, "17": 1}


def get(params, fantasy_filter=None, tries=3):
    qs = "&".join(f"{k}={v}" for k, v in params) + f"&_={int(time.time())}"
    req = urllib.request.Request(f"{BASE}?{qs}", headers={
        "User-Agent": "Mozilla/5.0 (blue-ballers-relay)",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    if fantasy_filter is not None:
        req.add_header("X-Fantasy-Filter", json.dumps(fantasy_filter))
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            print(f"HTTP {e.code} on {params}, retrying", file=sys.stderr)
        except urllib.error.URLError:
            if i == tries - 1:
                raise
        time.sleep(5 * (i + 1))


def fpts(stats):
    return sum(float(stats.get(k, 0) or 0) * w for k, w in SCORING.items())


def split(player, split_type):
    """Return dict(gp, avg, mpg) for actual stats of a split
    (0 season, 1 last 7, 2 last 15, 3 last 30)."""
    for s in player.get("stats") or []:
        if (s.get("seasonId") == SEASON and s.get("statSourceId") == 0
                and s.get("statSplitTypeId") == split_type):
            st = s.get("stats") or {}
            gp = float(st.get("42", 0) or 0)
            avg = s.get("appliedAverage")
            total = s.get("appliedTotal")
            if avg is None and total is not None and gp:
                avg = total / gp
            # Work out whether the raw stat dict holds totals or per-game values
            # by comparing our own fantasy calc with ESPN's applied numbers.
            mins = float(st.get("40", 0) or 0)
            calc = fpts(st)
            per_game = avg is not None and total is not None and gp > 1 and \
                abs(calc - avg) < abs(calc - total)
            mpg = mins if per_game else (mins / gp if gp else 0)
            return {"gp": int(gp), "avg": round(avg or 0, 1), "mpg": round(mpg, 1)}
    return {"gp": 0, "avg": "", "mpg": ""}


def positions(player):
    slots = player.get("eligibleSlots") or []
    return "/".join(SLOTS[s] for s in POS_SLOTS if s in slots)


def row_for(player):
    season, l7, l15 = split(player, 0), split(player, 1), split(player, 2)
    own = (player.get("ownership") or {}).get("percentOwned")
    return {
        "player": player.get("fullName"),
        "nba": PRO_TEAMS.get(player.get("proTeamId"), player.get("proTeamId")),
        "pos": positions(player),
        "injury": player.get("injuryStatus") or ("OUT" if player.get("injured") else "ACTIVE"),
        "gp": season["gp"], "avg": season["avg"], "mpg": season["mpg"],
        "l7_gp": l7["gp"], "l7_avg": l7["avg"],
        "l15_gp": l15["gp"], "l15_avg": l15["avg"],
        "pct_owned": round(own, 1) if isinstance(own, (int, float)) else "",
        "espn_id": player.get("id"),
    }


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    base = get([("view", "mStatus"), ("view", "mTeam")])
    sp = base.get("scoringPeriodId") or (base.get("status") or {}).get("latestScoringPeriod")
    mp = (base.get("status") or {}).get("currentMatchupPeriod")
    team_names = {}
    for t in base.get("teams") or []:
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        team_names[t["id"]] = name

    stat_ids = [f"00{SEASON}", f"01{SEASON}", f"02{SEASON}", f"03{SEASON}"]

    # ---- Free agents + waivers -------------------------------------------------
    pool = {}
    sorts = [
        {"sortAppliedStatAverage": {"sortAsc": False, "sortPriority": 1, "value": f"00{SEASON}"}},
        {"sortPercOwned": {"sortAsc": False, "sortPriority": 1}},
    ]
    for sort in sorts:
        filt = {"players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": POS_SLOTS + [5, 6, 11]},
            "filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": stat_ids},
            "limit": 200, "offset": 0, **sort}}
        try:
            data = get([("view", "kona_player_info"), ("scoringPeriodId", sp)], filt)
        except Exception as e:  # keep going with whichever sort works
            print(f"free-agent query {list(sort)[0]} failed: {e}", file=sys.stderr)
            continue
        for e in data.get("players") or []:
            if e.get("status") not in ("FREEAGENT", "WAIVERS"):
                continue
            p = e.get("player") or {}
            r = row_for(p)
            r["status"] = "WA" if e.get("status") == "WAIVERS" else "FA"
            wpd = e.get("waiverProcessDate")
            r["waiver_clears_utc"] = (datetime.fromtimestamp(wpd / 1000, timezone.utc)
                                      .strftime("%Y-%m-%d %H:%M") if wpd else "")
            pool[p.get("id")] = r
    fa = [r for r in pool.values() if r["gp"]]
    fa.sort(key=lambda r: (r["avg"] if r["avg"] != "" else -99), reverse=True)
    fa = fa[:150]
    write_csv(os.path.join(OUT, "free_agents.csv"), fa,
              ["player", "nba", "pos", "status", "waiver_clears_utc", "injury", "gp", "avg",
               "mpg", "l7_gp", "l7_avg", "l15_gp", "l15_avg", "pct_owned", "espn_id"])

    # ---- All rosters -----------------------------------------------------------
    # Source of truth = the player-pool query (same fresh endpoint as free agents):
    # every ONTEAM player with his onTeamId. mRoster is cached by ESPN and can lag
    # adds/drops by hours, so it is only used to fill in lineup slots.
    slots = {}
    rdata = {}
    try:
        rdata = get([("view", "mRoster"), ("scoringPeriodId", sp)])
        for t in rdata.get("teams") or []:
            for e in (t.get("roster") or {}).get("entries") or []:
                pid = ((e.get("playerPoolEntry") or {}).get("player") or {}).get("id")
                slots[pid] = (t["id"], SLOTS.get(e.get("lineupSlotId"), e.get("lineupSlotId")))
    except Exception as e:
        print(f"mRoster failed: {e}", file=sys.stderr)
    filt = {"players": {
        "filterStatus": {"value": ["ONTEAM"]},
        "filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": stat_ids},
        "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
        "limit": 300, "offset": 0}}
    odata = get([("view", "kona_player_info"), ("scoringPeriodId", sp)], filt)
    rows = []
    for e in odata.get("players") or []:
        tid = e.get("onTeamId")
        if e.get("status") != "ONTEAM" or not tid:
            continue
        p = e.get("player") or {}
        r = row_for(p)
        slot_tid, slot = slots.get(p.get("id"), (None, ""))
        r.update({"team_id": tid, "team": team_names.get(tid, ""),
                  "slot": slot if slot_tid == tid else "NEW"})  # NEW = just added
        rows.append(r)
    if not rows:  # fall back to mRoster if the pool query returned nothing
        for t in rdata.get("teams") or []:
            for e in (t.get("roster") or {}).get("entries") or []:
                p = (e.get("playerPoolEntry") or {}).get("player") or {}
                r = row_for(p)
                r.update({"team_id": t["id"], "team": team_names.get(t["id"], ""),
                          "slot": SLOTS.get(e.get("lineupSlotId"), e.get("lineupSlotId"))})
                rows.append(r)
    rows.sort(key=lambda r: (r["team_id"], -(r["avg"] if r["avg"] != "" else -99)))
    write_csv(os.path.join(OUT, "rosters.csv"), rows,
              ["team_id", "team", "player", "nba", "pos", "slot", "injury", "gp", "avg", "mpg",
               "l7_gp", "l7_avg", "l15_gp", "l15_avg", "pct_owned", "espn_id"])

    # ---- Matchup scores --------------------------------------------------------
    mdata = get([("view", "mMatchupScore"), ("scoringPeriodId", sp)])
    mrows = []
    for m in mdata.get("schedule") or []:
        if m.get("matchupPeriodId") != mp:
            continue
        h, a = m.get("home") or {}, m.get("away") or {}
        mrows.append({
            "matchup_period": mp,
            "home_id": h.get("teamId"), "home": team_names.get(h.get("teamId"), ""),
            "home_pts": h.get("totalPointsLive", h.get("totalPoints")),
            "away_id": a.get("teamId"), "away": team_names.get(a.get("teamId"), ""),
            "away_pts": a.get("totalPointsLive", a.get("totalPoints")),
        })
    write_csv(os.path.join(OUT, "matchups.csv"), mrows,
              ["matchup_period", "home_id", "home", "home_pts", "away_id", "away", "away_pts"])

    with open(os.path.join(OUT, "status.json"), "w") as f:
        json.dump({"updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                   "scoring_period": sp, "matchup_period": mp,
                   "free_agents": len(fa), "rostered": len(rows)}, f, indent=1)
    print(f"ok: period {sp}, {len(fa)} free agents, {len(rows)} rostered")


if __name__ == "__main__":
    main()
