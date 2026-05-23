# Paper 1 — RSRT Data Pipeline (v2)

This replaces the old CSV-download → `master_consolidation.ipynb` pipeline
that silently dropped all DoD rows during `groupby('Award ID')`.

## What changed

**Old pipeline**
1. Manually download 17 CSVs from USAspending.gov keyword search.
2. Concatenate in `master_consolidation.ipynb` (groupby on Award ID).
3. DoD Award IDs (contract numbers like `N00014-22-1-2367`) caused
   silent drops in step 2 → master pickle had 0 DoD rows.

**New pipeline**
1. `pull_rsrt_master.py` — one-shot script. Calls USAspending's
   `spending_by_category/recipient` endpoint directly, 450 buckets
   (15 RSRTs × 5 agencies × 6 FYs), recipient-level aggregation.
2. Award IDs never enter the pipeline → bug is structurally impossible.
3. Outputs: `rsrt_master.csv`, `rsrt_master.pkl`, `rsrt_master_pull_log.json`.

## What you lose vs. the old approach

Award-level granularity. The new master file has one row per
`(rsrt, agency, recipient, fy)` with summed obligated USD. This is
sufficient for Paper 1's four core analyses:

- Dollar concentration (which recipients capture the most $)
- Topic concentration (which RSRTs dominate)
- Agency × RSRT bias (which agency favors which topic)
- Recipient × RSRT preference (which universities favored for which topic)

For analyses that *need* award counts (e.g., "DoD made N hypersonics
awards to MIT") this dataset will not suffice — but none of the RQs
require that.

## Methodology disclosures the paper needs to state

1. **Multi-RSRT overlap is not split.** An award matching two canonical
   terms (e.g., "hypersonic" and "advanced materials") appears under
   both RSRTs at full obligated value. Per-RSRT totals therefore
   describe "obligations to awards matching this canonical term," not
   a partition of total federal spending. This is how USAspending's
   own keyword search works.
2. **Agency filter type is `funding`, not `awarding`.** Captures who
   actually paid, even when a pass-through awarding agency differs.
3. **C4ISR exception:** three sub-terms (synthetic aperture radar,
   target tracking, electronic warfare). Within C4ISR, a recipient's
   obligation is the sum across the three sub-term pulls.
4. **Recipient types:** higher_education, public/private institution
   of higher education, MSI. (DHS excluded from agencies per team scope.)
5. **FY2026 excluded** as partial fiscal year. Range is FY2020–FY2025
   hard-stopped.

## Files

- `pull_rsrt_master.py` — main data pull. Run once. ~30–60 min.
- `app.py` — Streamlit validator. RSRT × agency × FY filters; sanity
  panel that prints zero-row agencies prominently (would have caught
  the DoD bug instantly).
- `requirements.txt` — pandas, requests, streamlit, plotly.

## To run

```
pip install -r requirements.txt
python pull_rsrt_master.py            # writes master files + audit log
streamlit run app.py                  # optional live validator
```

## Next steps in the paper

1. Run `pull_rsrt_master.py` → verify per-agency row counts in stdout
   (DoD must be > 0).
2. Update `kg_build_clean.ipynb` to read `rsrt_master.pkl` and build
   `(ParentAgency)-[:FUNDS]->(Recipient)-[:WORKS_ON]->(CTopic)`.
3. Update `rq_analysis_v3.ipynb` for the new schema (no Award ID column).
4. Draft Section V (Results).
