# #MillerMetrics

A self-contained fantasy football dashboard for ESPN private league **683667** —
standings, head-to-head matrix, lifetime records, and per-season recaps.

The site is a single static HTML file with the league's data baked into it as a
JSON blob. A small Python script (`scripts/update.py`) refreshes that blob from
ESPN's private API. A GitHub Actions workflow runs the script on a schedule, and
GitHub Pages serves the result.

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

## Live site

Once GitHub Pages is enabled (see setup below), the dashboard is at:

```
https://<your-github-username>.github.io/MillerMetrics/
```

---

## One-time setup

### 1. Create the GitHub repo

1. Go to <https://github.com/new>
2. Repository name: `MillerMetrics` (or whatever you like)
3. **Public** (required for free GitHub Pages on a personal account)
4. Do **not** initialize with README, .gitignore, or license — this folder
   already has them
5. Create the repo, then copy the `git remote add origin …` line GitHub shows

### 2. Push this folder to the repo

From a terminal in `C:\MillerMetrics`:

```bash
git remote add origin https://github.com/<your-username>/MillerMetrics.git
git branch -M main
git push -u origin main
```

### 3. Add the ESPN cookies as repository secrets

Repo page → **Settings** → **Secrets and variables** → **Actions** → **New
repository secret**. Add three secrets:

| Name        | Value                                                            |
|-------------|------------------------------------------------------------------|
| `ESPN_S2`   | The `espn_s2` cookie value from fantasy.espn.com                 |
| `SWID`      | The `SWID` cookie value (include the curly braces)               |
| `LEAGUE_ID` | `683667`                                                         |

To grab the cookies: log in to <https://fantasy.espn.com>, press F12 →
**Application** tab → **Cookies** → `https://fantasy.espn.com` → copy the values
for `espn_s2` and `SWID` exactly as shown.

These cookies expire every few months. When the scheduled workflow starts
failing with an auth error, repeat this step to update the secrets.

### 4. Enable GitHub Pages

Repo page → **Settings** → **Pages**:

- **Source**: Deploy from a branch
- **Branch**: `main` / `/ (root)`
- Save

GitHub will publish the site within a minute. Any future push to `main`
(including auto-commits from the workflow) will trigger a redeploy automatically
— there is nothing more to wire up.

### 5. Allow Actions to push commits

Repo page → **Settings** → **Actions** → **General** → **Workflow permissions**:

- Select **Read and write permissions**
- Save

This lets the weekly job commit the refreshed `index.html` back to `main`.

### 6. Run the workflow once to confirm everything works

Repo page → **Actions** tab → **Update ESPN data** → **Run workflow** → **Run
workflow**. Watch the run; if it goes green, the Pages site will redeploy with
fresh data shortly after.

---

## Running locally (optional)

You only need this if you want to refresh the data from your laptop instead of
waiting for the scheduled workflow.

```bash
# One-time
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste in your ESPN_S2 / SWID values

# Each refresh
python scripts/update.py
# Then open index.html in a browser
```

`.env` is git-ignored, so your cookies stay on your machine.

---

## How the auto-refresh works

`.github/workflows/update.yml` runs `scripts/update.py` every **Tuesday at
12:00 UTC** (configurable via the `cron:` line). The script:

1. Reads `ESPN_S2`, `SWID`, `LEAGUE_ID` from environment variables (set by the
   workflow from your GitHub secrets)
2. Hits ESPN's API for every season since 2013
3. Rebuilds the standings, H2H matrix, and records
4. Rewrites the `<script id="espn-data">…</script>` block inside `index.html`

If `index.html` actually changed, the workflow commits and pushes the update.
GitHub Pages redeploys automatically.

You can also trigger it on demand from the **Actions** tab without waiting for
Tuesday.

---

## Refreshing expired ESPN cookies

Symptom: the scheduled workflow fails with an auth error, or `update.py` prints
`ESPN rejected the request (401/403)`.

Fix:
1. Log in to <https://fantasy.espn.com>
2. F12 → Application → Cookies → copy fresh `espn_s2` and `SWID`
3. Repo → Settings → Secrets and variables → Actions → update both secrets
4. Re-run the workflow from the Actions tab

(If you also run locally, update the same two values in your `.env`.)
