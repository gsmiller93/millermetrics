# #MillerMetrics

A self-contained fantasy football dashboard for ESPN private league **683667** —
standings, head-to-head matrix, lifetime records, and per-season recaps.

The site is a single static HTML file with the league's data baked into it as a
JSON blob. A small Python script (`scripts/update.py`) refreshes that blob from
ESPN's private API. A GitHub Actions workflow runs the script on a schedule, and
GitHub Pages serves the result.

## Live site

<https://gsmiller93.github.io/millermetrics/>

## Project layout

```
.
├── index.html                    # The dashboard (open in any browser)
├── scripts/
│   └── update.py                 # Fetches data from ESPN, injects into index.html
├── requirements.txt              # Python dependencies
├── .env.example                  # Template for local credentials
├── .gitignore                    # Keeps .env and other junk out of git
└── .github/workflows/update.yml  # Weekly auto-refresh
```

## How the auto-refresh works

`.github/workflows/update.yml` runs `scripts/update.py` every **Tuesday at
12:00 UTC** (configurable via the `cron:` line). The script:

1. Reads `ESPN_S2`, `SWID`, and `LEAGUE_ID` from environment variables (set by
   the workflow from repository secrets)
2. Hits ESPN's API for every season since 2013
3. Rebuilds the standings, H2H matrix, and records
4. Rewrites the `<script id="espn-data">…</script>` block inside `index.html`

If `index.html` actually changed, the workflow commits and pushes the update.
GitHub Pages redeploys automatically.

You can also trigger it on demand from the **Actions** tab without waiting for
Tuesday.

## Refreshing expired ESPN cookies

Symptom: the scheduled workflow fails with an auth error, or `update.py` prints
`ESPN rejected the request (401/403)`.

Fix:
1. Log in to <https://fantasy.espn.com>
2. F12 → Application → Cookies → copy fresh `espn_s2` and `SWID` values
3. Repo → Settings → Secrets and variables → Actions → update both secrets
4. Re-run the workflow from the Actions tab

(If you also run locally, update the same two values in your `.env`.)

## Running locally

Only needed for ad-hoc data refreshes outside the weekly schedule.

```bash
# One-time
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste in ESPN_S2 / SWID values

# Each refresh
python scripts/update.py
# Then open index.html in a browser
```

`.env` is git-ignored, so cookies stay on the local machine.

## Repo configuration reference

Settings already applied — listed here as a recovery checklist in case anything
gets reset:

- **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main` /
  `(root)`
- **Settings → Actions → General → Workflow permissions**: *Read and write
  permissions*
- **Settings → Secrets and variables → Actions**: secrets `ESPN_S2`, `SWID`,
  `LEAGUE_ID`
