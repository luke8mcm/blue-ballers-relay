# Blue Ballers ESPN relay

A GitHub Action fetches the league's free agents (sorted by fantasy pts/game), all 8 rosters with season / last-7 / last-15 averages, and current matchup scores every 3 hours, and commits them as CSVs to `data/`. Claude's daily brief reads them from raw.githubusercontent.com.

Files: `data/free_agents.csv`, `data/rosters.csv`, `data/matchups.csv`, `data/status.json`.

Run it now: Actions tab → "ESPN relay" → Run workflow.
