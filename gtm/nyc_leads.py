#!/usr/bin/env python3
"""
nyc_leads.py — GTM lead-generation for WizScheduler.

Pulls NYC restaurant data from the NYC DOHMH Restaurant Inspection Results
dataset (Socrata Open Data API), dedupes to one row per restaurant (`camis`),
segments likely multi-unit chains (a cheap proxy for NYC Fair Workweek
fast-food coverage), and writes a clean lead CSV for cold outreach.

Standard library only. No third-party dependencies (requests/pandas/sodapy).

Usage:
    python3 gtm/nyc_leads.py --borough MANHATTAN --limit 200 --out gtm/leads.csv

Set SODA_APP_TOKEN env var to raise Socrata rate limits (sent as X-App-Token).
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DATASET_URL = "https://data.cityofnewyork.us/resource/43nn-pn8j.json"

# Fields we request from Socrata. Keeping $select tight reduces payload size.
SELECT_FIELDS = [
    "camis",
    "dba",
    "boro",
    "building",
    "street",
    "zipcode",
    "phone",
    "cuisine_description",
    "latitude",
    "longitude",
    "council_district",
    "community_board",
    "inspection_date",
]

OUTPUT_COLUMNS = [
    "camis",
    "business_name",
    "address",
    "borough",
    "zip",
    "phone",
    "cuisine",
    "latitude",
    "longitude",
    "council_district",
    "likely_chain",
    "chain_unit_count",
    "fair_workweek_fast_food_candidate",
    "website",
    "owner_name",
    "owner_email",
    "notes",
]

PAGE_SIZE = 50000
MAX_RETRIES = 4


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def build_where(borough: str | None, zips: list[str] | None) -> str | None:
    """Build a Socrata $where clause from the optional server-side filters."""
    clauses: list[str] = []
    if borough:
        # boro values in the dataset are upper-cased borough names.
        safe = borough.replace("'", "''")
        clauses.append(f"upper(boro)='{safe.upper()}'")
    if zips:
        quoted = ",".join(f"'{z.strip()}'" for z in zips if z.strip())
        if quoted:
            clauses.append(f"zipcode in({quoted})")
    return " AND ".join(clauses) if clauses else None


def fetch_page(offset: int, where: str | None, token: str | None) -> list[dict]:
    """Fetch a single page from Socrata, with 429/backoff retry handling."""
    params = {
        "$select": ",".join(SELECT_FIELDS),
        "$limit": str(PAGE_SIZE),
        "$offset": str(offset),
        "$order": "camis",  # stable ordering so pagination is consistent
    }
    if where:
        params["$where"] = where

    url = f"{DATASET_URL}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "User-Agent": "wizscheduler-gtm/1.0"}
    if token:
        headers["X-App-Token"] = token

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("Unexpected response shape (expected JSON array)")
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = 2 ** attempt
                sys.stderr.write(
                    f"  rate-limited (429), backing off {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})...\n"
                )
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                wait = 2 ** attempt
                sys.stderr.write(
                    f"  server error ({e.code}), retrying in {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})...\n"
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code} from Socrata: {e.reason}") from e
        except urllib.error.URLError as e:
            # Network unreachable / DNS failure / no network in sandbox.
            raise RuntimeError(f"Network error contacting Socrata: {e.reason}") from e
        except (json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(f"Could not parse Socrata response: {e}") from e

    raise RuntimeError(f"Exhausted retries contacting Socrata: {last_err}")


# --------------------------------------------------------------------------- #
# Dedupe + transform
# --------------------------------------------------------------------------- #
def normalize_name(dba: str) -> str:
    """Normalize a business name for chain grouping."""
    s = (dba or "").strip().upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)   # drop punctuation
    s = re.sub(r"\s+", " ", s).strip()   # collapse whitespace
    return s


def dedupe_by_camis(records: list[dict]) -> dict[str, dict]:
    """Collapse many inspection rows into one row per camis (most recent kept)."""
    latest: dict[str, dict] = {}
    for rec in records:
        camis = (rec.get("camis") or "").strip()
        if not camis:
            continue
        cur = latest.get(camis)
        if cur is None:
            latest[camis] = rec
            continue
        # Prefer the record with the most recent inspection_date.
        new_date = rec.get("inspection_date") or ""
        cur_date = cur.get("inspection_date") or ""
        if new_date > cur_date:
            latest[camis] = rec
    return latest


def build_rows(
    unique: dict[str, dict],
    min_chain_count: int,
    cuisine_substr: str | None,
    limit: int | None = None,
) -> tuple[list[dict], collections.Counter]:
    """Apply cuisine filter, compute chain counts, and build output rows.

    Chain counts are computed over the FULL fetched set (so the citywide
    multi-unit heuristic is meaningful) before the output is trimmed to
    ``limit`` rows.
    """
    # Count distinct camis per normalized name (citywide within fetched set).
    name_counts: collections.Counter = collections.Counter()
    for rec in unique.values():
        nm = normalize_name(rec.get("dba", ""))
        if nm:
            name_counts[nm] += 1

    cuisine_lc = cuisine_substr.lower() if cuisine_substr else None

    rows: list[dict] = []
    for rec in unique.values():
        cuisine = rec.get("cuisine_description", "") or ""
        if cuisine_lc and cuisine_lc not in cuisine.lower():
            continue

        nm = normalize_name(rec.get("dba", ""))
        unit_count = name_counts.get(nm, 0)
        is_chain = bool(nm) and unit_count >= min_chain_count

        building = (rec.get("building") or "").strip()
        street = (rec.get("street") or "").strip()
        address = " ".join(p for p in (building, street) if p)

        rows.append(
            {
                "camis": rec.get("camis", ""),
                "business_name": (rec.get("dba") or "").strip(),
                "address": address,
                "borough": rec.get("boro", ""),
                "zip": rec.get("zipcode", ""),
                "phone": rec.get("phone", ""),
                "cuisine": cuisine,
                "latitude": rec.get("latitude", ""),
                "longitude": rec.get("longitude", ""),
                "council_district": rec.get("council_district", ""),
                "likely_chain": "true" if is_chain else "false",
                "chain_unit_count": unit_count,
                "fair_workweek_fast_food_candidate": "true" if is_chain else "false",
                "website": "",
                "owner_name": "",
                "owner_email": "",
                "notes": "",
            }
        )

    # Deterministic ordering: chains first (by unit count desc), then name.
    rows.sort(
        key=lambda r: (-int(r["chain_unit_count"]), r["business_name"])
    )
    if limit is not None and len(rows) > limit:
        rows = rows[:limit]
    return rows, name_counts


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def write_csv(rows: list[dict], out_path: str) -> None:
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_summary(rows: list[dict], out_path: str) -> None:
    total = len(rows)
    by_borough = collections.Counter(r["borough"] or "(unknown)" for r in rows)
    chains = sum(1 for r in rows if r["likely_chain"] == "true")
    independents = total - chains

    # Top chains by unit count. Group by NORMALIZED name so spelling variants
    # (e.g. DUNKIN / DUNKIN') collapse into one entry; show a representative
    # raw name for readability.
    chain_names: dict[str, tuple[str, int]] = {}
    for r in rows:
        if r["likely_chain"] == "true" and r["business_name"]:
            nm = normalize_name(r["business_name"])
            cnt = int(r["chain_unit_count"])
            existing = chain_names.get(nm)
            if existing is None or cnt > existing[1]:
                chain_names[nm] = (r["business_name"], cnt)
    top_chains = sorted(
        chain_names.values(), key=lambda v: (-v[1], v[0])
    )[:10]

    print("\n" + "=" * 56)
    print("  WizScheduler GTM — NYC Restaurant Leads")
    print("=" * 56)
    print(f"  Total unique restaurants : {total}")
    print(f"  Likely chains            : {chains}")
    print(f"  Independents             : {independents}")
    print("\n  By borough:")
    for boro, cnt in by_borough.most_common():
        print(f"    {boro:<18} {cnt}")
    print("\n  Top 10 chains by unit count (Fair Workweek candidates):")
    if top_chains:
        for name, cnt in top_chains:
            print(f"    {cnt:>4}  {name}")
    else:
        print("    (none met the --min-chain-count threshold)")
    print(f"\n  Wrote {total} leads -> {out_path}")
    print("=" * 56 + "\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate NYC restaurant leads (Fair Workweek segmented) "
        "from NYC Open Data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--borough",
        default=None,
        help="Filter by borough (MANHATTAN/BROOKLYN/QUEENS/BRONX/STATEN ISLAND), "
        "case-insensitive. Filtered server-side.",
    )
    p.add_argument(
        "--zip",
        dest="zips",
        default=None,
        help="Comma-separated ZIP codes to filter (server-side).",
    )
    p.add_argument(
        "--cuisine",
        default=None,
        help="Case-insensitive substring filter on cuisine description.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max unique restaurants to output (stops pagination early).",
    )
    p.add_argument(
        "--out",
        default="gtm/nyc_leads.csv",
        help="Output CSV path.",
    )
    p.add_argument(
        "--min-chain-count",
        type=int,
        default=3,
        help="Distinct locations sharing a name to flag as a likely chain.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.limit <= 0:
        sys.stderr.write("ERROR: --limit must be a positive integer.\n")
        return 2

    token = os.environ.get("SODA_APP_TOKEN") or None
    zips = args.zips.split(",") if args.zips else None
    where = build_where(args.borough, zips)

    if token:
        print("Using SODA_APP_TOKEN for higher rate limits.")
    if where:
        print(f"Server-side filter: {where}")

    # Paginate until we have enough unique restaurants or the dataset ends.
    # We over-fetch relative to --limit because raw rows contain duplicates.
    unique: dict[str, dict] = {}
    offset = 0
    try:
        while len(unique) < args.limit:
            print(f"Fetching rows at offset {offset} (unique so far: {len(unique)})...")
            page = fetch_page(offset, where, token)
            if not page:
                print("No more rows returned by the API.")
                break
            page_unique = dedupe_by_camis(page)
            for camis, rec in page_unique.items():
                cur = unique.get(camis)
                if cur is None:
                    unique[camis] = rec
                else:
                    new_date = rec.get("inspection_date") or ""
                    cur_date = cur.get("inspection_date") or ""
                    if new_date > cur_date:
                        unique[camis] = rec
            offset += PAGE_SIZE
            if len(page) < PAGE_SIZE:
                print("Reached end of dataset.")
                break
    except RuntimeError as e:
        sys.stderr.write(f"\nERROR: {e}\n")
        sys.stderr.write(
            "Live fetch failed. If this environment has no network access, "
            "run the script where it can reach data.cityofnewyork.us.\n"
        )
        return 1

    if not unique:
        sys.stderr.write(
            "No restaurants matched your filters. "
            "Check --borough / --zip / --cuisine values.\n"
        )
        return 1

    # Chain counts are computed over the full fetched set; output is then
    # trimmed to --limit (highest-priority chains first).
    rows, _ = build_rows(unique, args.min_chain_count, args.cuisine, args.limit)

    if args.cuisine and not rows:
        sys.stderr.write(
            f"No restaurants matched cuisine substring '{args.cuisine}'.\n"
        )
        return 1

    write_csv(rows, args.out)
    print_summary(rows, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
