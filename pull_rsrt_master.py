"""
pull_rsrt_master.py
===================

One-shot pull of higher-education obligations from USAspending.gov, classified
by RSRT (Research Security-Relevant Technology) via keyword search on award
descriptions, for the 5 parent agencies in scope.

Output schema (long format, one row per agency x recipient x rsrt x fy):
    rsrt, rsrt_canonical_term, agency, recipient, fy, obligated_usd

Writes:
    rsrt_master.csv
    rsrt_master.pkl
    rsrt_master_pull_log.json   (per-bucket call counts + result counts; audit trail)

Notes
-----
* Endpoint: POST /api/v2/search/spending_by_category/recipient
  Returns recipient-level aggregations -- exactly what the four RQs need
  (dollar concentration, topic concentration, agency-topic bias, recipient-topic
  preference). Award IDs are deliberately not collected; see Methods.

* Multi-RSRT overlap: an award matching two canonical terms (e.g. "hypersonic"
  AND "advanced materials") will appear under both RSRTs at full obligated
  value. This is disclosed in Methods rather than engineered around. Per-RSRT
  totals therefore describe "obligations to awards matching this canonical
  term," not a partition of total federal spending.

* C4ISR exception: three canonical terms (synthetic aperture radar, target
  tracking, electronic warfare). Within C4ISR a recipient's obligation is the
  SUM across the three terms. A note in the log records which awards (if any)
  matched multiple C4ISR sub-terms -- but since we don't pull award IDs, this
  is a known limitation, documented in Methods.

* FY range: hard-stopped FY2020-FY2025. FY2026 excluded as partial year.
* Recipient types: higher_education + public/private institution + MSI.
* Agency filter type: 'funding' (matches the working dashboard's choice;
  captures who actually paid, even when pass-through awarding agency differs).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "https://api.usaspending.gov/api/v2"
ENDPOINT = f"{API_BASE}/search/spending_by_category/recipient"

# 5 parent agencies in scope. Full top-tier names per USAspending agency dictionary.
AGENCIES = {
    "Department of Defense": "DoD",
    "Department of Energy": "DOE",
    "Department of Health and Human Services": "HHS",
    "National Science Foundation": "NSF",
    "National Aeronautics and Space Administration": "NASA",
}

RECIPIENT_TYPES = [
    "higher_education",
    "public_institution_of_higher_education",
    "private_institution_of_higher_education",
    "minority_serving_institution_of_higher_education",
]

# FY2020-FY2025 inclusive. FY2026 excluded as partial.
FISCAL_YEARS = list(range(2020, 2026))

# 15 RSRTs, single canonical term each (C4ISR is the documented exception).
# Order matters only for output stability and progress display.
RSRT_TERMS: dict[str, list[str]] = {
    "Hypersonics":                          ["hypersonic"],
    "Directed_Energy":                      ["directed energy"],
    "Networked_Sensing_C4ISR":              ["synthetic aperture radar",
                                             "target tracking",
                                             "electronic warfare"],
    "Cybersecurity_Data_Privacy":           ["cybersecurity"],
    "Advanced_Computing_Semiconductors":    ["microelectronics"],
    "Quantum_Information_Science":          ["quantum information"],
    "AI_Autonomy":                          ["artificial intelligence"],
    "Advanced_Materials":                   ["advanced materials"],
    "Space_Technology":                     ["space technology"],
    "Advanced_Manufacturing":               ["advanced manufacturing"],
    "Biotechnology":                        ["biotechnology"],
    "Future_Gen_Communications":            ["5G"],
    "HMI_Robotics":                         ["human-machine interface"],
    "Advanced_Energy":                      ["advanced energy"],
    "Disaster_Resilience":                  ["disaster"],
}

PAGE_LIMIT = 100        # per-page recipients (API max is 100)
RATE_SLEEP = 0.30       # seconds between requests (matches working dashboard)
REQUEST_TIMEOUT = 60    # seconds
MAX_RETRIES = 3
BACKOFF_BASE = 2.0      # seconds, exponential

OUTPUT_DIR = "."
OUT_CSV = os.path.join(OUTPUT_DIR, "rsrt_master.csv")
OUT_PKL = os.path.join(OUTPUT_DIR, "rsrt_master.pkl")
OUT_LOG = os.path.join(OUTPUT_DIR, "rsrt_master_pull_log.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fy_to_dates(fy: int) -> dict:
    """USAspending fiscal-year window: Oct 1 prior calendar year - Sep 30 FY year."""
    return {"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}


@dataclass
class BucketLog:
    rsrt: str
    term: str
    agency_abbr: str
    agency_name: str
    fy: int
    pages: int = 0
    rows: int = 0
    obligated_total: float = 0.0
    error: Optional[str] = None


@dataclass
class PullLog:
    started_at: str
    finished_at: Optional[str] = None
    fiscal_years: list[int] = field(default_factory=list)
    agencies: list[str] = field(default_factory=list)
    rsrts: list[str] = field(default_factory=list)
    buckets: list[dict] = field(default_factory=list)
    total_calls: int = 0
    total_errors: int = 0


def post_with_retry(payload: dict) -> dict:
    """POST with exponential backoff. Raises on terminal failure."""
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT)
            # 429 or 5xx -> retry
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                sleep_for = BACKOFF_BASE ** attempt
                time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def fetch_bucket(agency_name: str, fy: int, keyword: str) -> tuple[list[dict], int]:
    """
    Pull all paginated recipient results for one (agency, fy, keyword) bucket.

    Returns (results, page_count). Each result dict has at least 'name' and 'amount'.
    """
    dates = fy_to_dates(fy)
    filters = {
        "time_period": [dates],
        "agencies": [{"type": "funding", "tier": "toptier", "name": agency_name}],
        "recipient_type_names": RECIPIENT_TYPES,
        "keywords": [keyword],
    }

    all_results: list[dict] = []
    page = 1
    while True:
        payload = {
            "filters": filters,
            "category": "recipient",
            "limit": PAGE_LIMIT,
            "page": page,
        }
        data = post_with_retry(payload)
        results = data.get("results", []) or []
        all_results.extend(results)
        # Pagination signal: USAspending returns page_metadata.hasNext when more pages exist.
        page_meta = data.get("page_metadata", {}) or {}
        has_next = page_meta.get("hasNext")
        if has_next is None:
            # Fallback: stop if fewer than limit rows returned (defensive, matches dashboard)
            if len(results) < PAGE_LIMIT:
                break
        elif not has_next:
            break
        page += 1
        time.sleep(RATE_SLEEP)

    return all_results, page


# ---------------------------------------------------------------------------
# Main pull
# ---------------------------------------------------------------------------

def run_pull() -> pd.DataFrame:
    log = PullLog(
        started_at=datetime.utcnow().isoformat() + "Z",
        fiscal_years=FISCAL_YEARS,
        agencies=list(AGENCIES.values()),
        rsrts=list(RSRT_TERMS.keys()),
    )

    rows: list[dict] = []

    total_buckets = (
        sum(len(terms) for terms in RSRT_TERMS.values())
        * len(AGENCIES)
        * len(FISCAL_YEARS)
    )
    bucket_i = 0

    for rsrt, terms in RSRT_TERMS.items():
        for term in terms:
            for agency_full, agency_abbr in AGENCIES.items():
                for fy in FISCAL_YEARS:
                    bucket_i += 1
                    bucket = BucketLog(
                        rsrt=rsrt,
                        term=term,
                        agency_abbr=agency_abbr,
                        agency_name=agency_full,
                        fy=fy,
                    )
                    prefix = f"[{bucket_i:>4}/{total_buckets}] {rsrt[:28]:<28} | {agency_abbr:<4} | FY{fy} | term={term!r}"
                    try:
                        results, pages = fetch_bucket(agency_full, fy, term)
                    except Exception as e:
                        bucket.error = f"{type(e).__name__}: {e}"
                        log.total_errors += 1
                        print(f"{prefix}  ERROR: {bucket.error}", file=sys.stderr)
                        log.buckets.append(bucket.__dict__)
                        log.total_calls += 1
                        continue

                    bucket.pages = pages
                    bucket.rows = len(results)
                    bucket.obligated_total = float(sum((r.get("amount") or 0) for r in results))
                    log.total_calls += pages

                    for r in results:
                        rows.append({
                            "rsrt": rsrt,
                            "rsrt_canonical_term": term,
                            "agency": agency_abbr,
                            "recipient": r.get("name", "Unknown"),
                            "fy": fy,
                            "obligated_usd": float(r.get("amount") or 0),
                        })

                    print(
                        f"{prefix}  -> {bucket.rows:>4} recipients, "
                        f"${bucket.obligated_total/1e6:>8.2f}M, {pages} page(s)"
                    )
                    log.buckets.append(bucket.__dict__)
                    time.sleep(RATE_SLEEP)

    df = pd.DataFrame(rows, columns=[
        "rsrt", "rsrt_canonical_term", "agency", "recipient", "fy", "obligated_usd",
    ])

    # Aggregate inside C4ISR: a single recipient may appear in multiple sub-term
    # pulls. We sum within (rsrt, agency, recipient, fy) so the master file has
    # one row per such tuple. The rsrt_canonical_term column is dropped at this
    # point because it's no longer 1:1 with rows; the per-term breakdown is
    # preserved in the pull log for auditability.
    df_master = (
        df.groupby(["rsrt", "agency", "recipient", "fy"], as_index=False)["obligated_usd"]
          .sum()
          .sort_values(["rsrt", "agency", "fy", "obligated_usd"],
                       ascending=[True, True, True, False])
          .reset_index(drop=True)
    )

    # Diagnostic: per-agency row counts. The original bug (zero DoD rows) would
    # have been caught here -- so we print and check.
    print("\n=== Per-agency row counts in master ===")
    for agency_abbr in AGENCIES.values():
        n = int((df_master["agency"] == agency_abbr).sum())
        tag = "  <-- WARNING: zero rows" if n == 0 else ""
        print(f"  {agency_abbr:<5} {n:>6} rows{tag}")

    log.finished_at = datetime.utcnow().isoformat() + "Z"

    # Write outputs
    df_master.to_csv(OUT_CSV, index=False)
    df_master.to_pickle(OUT_PKL)
    with open(OUT_LOG, "w") as f:
        json.dump(log.__dict__, f, indent=2, default=str)

    print(f"\nWrote:\n  {OUT_CSV}\n  {OUT_PKL}\n  {OUT_LOG}")
    print(f"Master shape: {df_master.shape}")
    print(f"Total API calls: {log.total_calls}  |  Errors: {log.total_errors}")

    return df_master


if __name__ == "__main__":
    run_pull()
