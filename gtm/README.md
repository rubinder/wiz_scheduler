# GTM — NYC Restaurant Lead Generation

Standalone go-to-market tooling for WizScheduler. Pulls NYC restaurant data
from **NYC Open Data** and outputs a clean lead CSV for cold outreach,
segmented to prioritize **NYC Fair Workweek**-covered businesses
(multi-unit fast-food franchisees) over independents.

> This directory is intentionally decoupled from the app. It uses **only the
> Python standard library** — no new dependencies (per the repo `CLAUDE.md`).

## Data source

NYC DOHMH **Restaurant Inspection Results** via the Socrata Open Data API:

- Endpoint: `https://data.cityofnewyork.us/resource/43nn-pn8j.json`
- It's an *inspection* dataset — each restaurant (unique by `camis`) appears
  many times. The script **dedupes to one row per `camis`**, keeping the most
  recent record by `inspection_date`.

No API token is required for light use. For higher rate limits, set an app
token (sent as the `X-App-Token` header):

```bash
export SODA_APP_TOKEN=your_socrata_app_token
```

## Usage

```bash
# From the repo root
python3 gtm/nyc_leads.py [options]
```

### Options

| Flag                | Default            | Description                                                              |
|---------------------|--------------------|--------------------------------------------------------------------------|
| `--borough`         | (all)              | MANHATTAN / BROOKLYN / QUEENS / BRONX / STATEN ISLAND (case-insensitive, server-side) |
| `--zip`             | (all)              | Comma-separated ZIP codes (server-side)                                  |
| `--cuisine`         | (all)              | Case-insensitive substring filter on cuisine description                 |
| `--limit`           | `5000`             | Max unique restaurants to output (stops pagination early)                |
| `--out`             | `gtm/nyc_leads.csv`| Output CSV path                                                          |
| `--min-chain-count` | `3`                | Distinct locations sharing a name to flag as a likely chain              |

### Examples

```bash
# Manhattan sample
python3 gtm/nyc_leads.py --borough MANHATTAN --limit 200 --out gtm/sample_leads.csv

# Brooklyn pizza shops, treat 5+ locations as a chain
python3 gtm/nyc_leads.py --borough BROOKLYN --cuisine pizza --min-chain-count 5

# Specific ZIPs, larger pull
python3 gtm/nyc_leads.py --zip 10001,10011,10018 --limit 3000
```

## Output

CSV columns:

```
camis, business_name, address, borough, zip, phone, cuisine,
latitude, longitude, council_district, likely_chain, chain_unit_count,
fair_workweek_fast_food_candidate, website, owner_name, owner_email, notes
```

The last four columns (`website`, `owner_name`, `owner_email`, `notes`) are
left blank as **enrichment placeholders** — fill them later via Clay/Apollo.

### Fair Workweek segmentation

NYC's Fair Workweek fast-food law covers chains of **30+ locations
nationally**. That number isn't in this dataset, so the script uses a cheap,
useful proxy: count how many distinct `camis` share the same normalized `dba`
name in the fetched set. When a name appears `>= --min-chain-count` times, the
lead is flagged `likely_chain=true` and `fair_workweek_fast_food_candidate=true`.
Independents get `false`. Treat these as **prioritization hints**, not legal
determinations — verify true nationwide unit counts before making coverage claims.

## Summary output

The script prints a summary to stdout: total unique restaurants, count by
borough, likely chains vs independents, and the top 10 chain names by unit count.

## Notes

- Standard library only (`urllib`, `json`, `csv`, `argparse`, `collections`, `os`, `re`, `sys`, `time`).
- Handles HTTP errors, empty results, missing fields, and rate limiting
  (HTTP 429) with exponential backoff + retry.
- `--limit` stops pagination early so quick pulls stay fast.
