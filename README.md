# FUNDED/DAILY — Global Startup Funding Dashboard

A free, self-updating website tracking funded startups worldwide:
who got funded today, what problem they solve, their sector, country
of origin, stage, AI vs non-AI, research-based companies, and who keeps
getting funded over time.

```
10 global RSS sources ──► fetch_data.py (daily via GitHub Actions)
                              │  classify: category · sector · country
                              │  stage · AI · research-based
                              ▼
                    data/companies.json  (accumulates history —
                              │           repeat raisers become
                              ▼           "regularly funded")
                    index.html on GitHub Pages  ◄── you, in a browser
```

## What the dashboard shows

- **Today tab** — companies funded in the latest run (your daily news view)
- **All companies** — the full accumulating dataset
- **Regularly funded** — companies that appear in 2+ funding events over time
- **Research-based** — university spinouts, deep tech, lab-origin companies
- **Category sidebar** — 18 problem categories ranked by deal count
  (Professional excellence, Personal transformation, Life management,
  Time freedom, Financial independence, Societal transformation,
  Entrepreneurship, Learning & growth, Creative expression, plus
  Health & longevity, Climate & sustainability, Security & trust,
  Connection & community, Entertainment & play, Scientific discovery,
  Infrastructure & tools, Mobility & logistics, Food & agriculture)
- **Filters** — search, country, sector, stage (new / emerging / established), AI-only

## Setup (one time, ~5 minutes)

1. **Create a public GitHub repo** (Pages is free on public repos), e.g. `funding-dashboard`.
2. **Push these files**, keeping the structure:
   ```
   index.html
   fetch_data.py
   requirements.txt
   data/companies.json            ← seed data included
   .github/workflows/refresh-data.yml
   ```
   ⚠️ The workflow file MUST live at `.github/workflows/refresh-data.yml`.
3. **Enable GitHub Pages**: repo → Settings → Pages → Source: *Deploy from a branch* →
   Branch: `main`, folder `/ (root)` → Save.
4. **Allow the bot to commit**: Settings → Actions → General → Workflow permissions →
   select *Read and write permissions* → Save.
5. **Run it once**: Actions → "Refresh Funding Data (daily)" → Run workflow.

Your dashboard is now live at `https://<your-username>.github.io/funding-dashboard/`
and refreshes itself every day at 8 AM Central. The dataset grows daily, so
"regularly funded" detection gets better the longer it runs.

## Test locally

```bash
pip install -r requirements.txt
python fetch_data.py                 # refresh data
python3 -m http.server               # then open http://localhost:8000
```

(Opening index.html directly via file:// won't load the JSON — browsers
block it. Use the local server.)

## Customizing

| What | Where |
|---|---|
| Schedule/timezone | `cron:` in `.github/workflows/refresh-data.yml` (UTC) |
| Add an RSS source | `FEEDS` dict in `fetch_data.py` |
| Category keywords | `CATEGORIES` list in `fetch_data.py` (first match wins) |
| Sector keywords | `SECTORS` list |
| City/country mapping | `CITY_COUNTRY` / `DEMONYM_COUNTRY` dicts |
| Colors & fonts | `:root` CSS variables in `index.html` |

## Notes & limitations

- Coverage = what these news outlets report (most notable rounds globally,
  not every micro pre-seed). Exhaustive coverage needs paid Crunchbase/Dealroom APIs.
- Classification is keyword-based — fast, free, and right most of the time,
  but expect occasional miscategorization. Swap in an LLM call if you want
  near-perfect labeling (~$0.01/day with a small model).
- Country shows "Unknown" when the article never states a location;
  the region (from the source outlet) is still used.
- "Regularly funded" needs history: it becomes meaningful after the
  dashboard has been running for a few weeks/months.
