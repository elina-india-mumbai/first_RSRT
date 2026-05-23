"""
RSRT-by-RSRT Live Validation Dashboard
========================================
For each of 15 RSRTs, queries USAspending's award-description search using
a single canonical term. Displays awards, top recipients, and per-agency totals.

Use this to:
- Verify counts/dollars match your classifier output
- See real awards behind each RSRT classification
- Iterate keyword choice if the term is too broad or too narrow
- Build final per-RSRT award sets for KG-2
"""

import streamlit as st
import requests
import pandas as pd
from io import StringIO

# ════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="RSRT Validator",
    page_icon="🎯",
    layout="wide",
)

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

# Default canonical terms per RSRT — editable in UI
DEFAULT_RSRT_TERMS = {
    "Hypersonics": "hypersonic",
    "Directed_Energy": "directed energy",
    "Networked_Sensing_C4ISR": "integrated sensing",
    "Cybersecurity_Data_Privacy": "cybersecurity",
    "Advanced_Computing_Semiconductors": "microelectronics",
    "Quantum_Information_Science": "quantum information",
    "AI_Autonomy": "artificial intelligence",
    "Advanced_Materials": "advanced materials",
    "Space_Technology": "space technology",
    "Advanced_Manufacturing": "advanced manufacturing",
    "Biotechnology": "biotechnology",
    "Future_Gen_Communications": "5G",
    "HMI_Robotics": "human-machine interface",
    "Advanced_Energy": "advanced energy",
    "Disaster_Resilience": "disaster",
}

WARFARE_TIER = {
    "Hypersonics": "high",
    "Directed_Energy": "high",
    "Networked_Sensing_C4ISR": "high",
    "Cybersecurity_Data_Privacy": "medium_high",
    "Advanced_Computing_Semiconductors": "high",
    "Quantum_Information_Science": "high",
    "AI_Autonomy": "medium_high",
    "Advanced_Materials": "medium_high",
    "Space_Technology": "medium_high",
    "Advanced_Manufacturing": "medium",
    "Biotechnology": "medium",
    "Future_Gen_Communications": "medium",
    "HMI_Robotics": "low_medium",
    "Advanced_Energy": "low",
    "Disaster_Resilience": "low",
}

PARENT_AGENCIES = [
    "Department of Defense",
    "Department of Energy",
    "Department of Health and Human Services",
    "National Aeronautics and Space Administration",
    "National Science Foundation",
]

HIGHER_ED_TYPES = [
    "higher_education",
    "public_institution_of_higher_education",
    "private_institution_of_higher_education",
    "minority_serving_institution_of_higher_education",
    "school_of_forestry",
    "veterinary_college",
]

FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Amount",
    "Total Obligated Amount",
    "Description",
    "cfda_number",
    "cfda_title",
    "Start Date",
    "Award Type",
]


# ════════════════════════════════════════════════════════════════════
# DATA FETCH
# ════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_rsrt_awards(keyword, fy_start, fy_end, agencies, recipient_types, max_pages):
    """
    Pull all awards matching keyword in description, filtered by FY/agency/recipient.
    Returns: (rows, error_msg, total_count_reported)
    """
    fy_start_date = f"{fy_start-1}-10-01"
    fy_end_date = f"{fy_end}-09-30"

    base_filters = {
        "time_period": [{"start_date": fy_start_date, "end_date": fy_end_date}],
        "award_type_codes": ["02", "03", "04", "05"],  # grants/cooperative agreements
        "recipient_type_names": recipient_types,
        "description": keyword,
    }
    if agencies:
        base_filters["agencies"] = [
            {"type": "awarding", "tier": "toptier", "name": a} for a in agencies
        ]

    rows = []
    for page in range(1, max_pages + 1):
        payload = {
            "filters": base_filters,
            "fields": FIELDS,
            "limit": 100,
            "page": page,
            "sort": "Award Amount",
            "order": "desc",
        }
        try:
            r = requests.post(API_URL, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            batch = data.get("results", [])
            if not batch:
                break
            rows.extend(batch)
            # Check for more pages
            page_meta = data.get("page_metadata", {})
            if not page_meta.get("hasNext", False):
                break
        except Exception as e:
            return rows, str(e), None

    return rows, None, len(rows)


# ════════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════════

st.title("🎯 RSRT Validator — Live USAspending Lookup")
st.caption(
    "For each Research Security–Relevant Technology area, queries USAspending's "
    "award-description full-text search using a single canonical term. "
    "Use to validate findings and refine canonical terms."
)

# ── Sidebar ────────────────────────────────────────────────────────
st.sidebar.header("Configuration")

selected_rsrt = st.sidebar.selectbox(
    "Select RSRT",
    options=list(DEFAULT_RSRT_TERMS.keys()),
    index=0,
)

# Edit keyword for this RSRT
keyword = st.sidebar.text_input(
    "Search keyword (in award description)",
    value=DEFAULT_RSRT_TERMS[selected_rsrt],
    help="Change to test alternative terms"
)

st.sidebar.markdown(f"**Warfare tier:** `{WARFARE_TIER[selected_rsrt]}`")
st.sidebar.markdown("---")

# Time period
fy_start = st.sidebar.number_input("FY start", 2020, 2026, 2020)
fy_end = st.sidebar.number_input("FY end", 2020, 2026, 2026)

# Parent agencies
selected_parents = st.sidebar.multiselect(
    "Parent agencies (empty = all)",
    options=PARENT_AGENCIES,
    default=[],
    help="Leave empty to include all federal agencies"
)

max_pages = st.sidebar.slider(
    "Max pages (100 awards/page)",
    1, 50, 10,
    help="Higher = more data, slower fetch"
)

st.sidebar.markdown("---")
run_btn = st.sidebar.button("🔍 Fetch RSRT awards", type="primary", use_container_width=True)


# ════════════════════════════════════════════════════════════════════
# MAIN PANE
# ════════════════════════════════════════════════════════════════════

if not run_btn:
    st.info(
        "Select an RSRT in the sidebar and click **Fetch**. "
        "Default canonical term is shown — edit to test alternatives."
    )
    
    st.markdown("### Canonical RSRT terms (defaults)")
    default_df = pd.DataFrame([
        {"RSRT": k, "Canonical term": v, "Warfare tier": WARFARE_TIER[k]}
        for k, v in DEFAULT_RSRT_TERMS.items()
    ])
    st.dataframe(default_df, use_container_width=True, hide_index=True)
    st.stop()


# ── Fetch ──────────────────────────────────────────────────────────
with st.spinner(f"Querying USAspending: '{keyword}' in higher-ed awards, FY{fy_start}–FY{fy_end}..."):
    rows, error, total = fetch_rsrt_awards(
        keyword, fy_start, fy_end,
        selected_parents, HIGHER_ED_TYPES, max_pages
    )

if error:
    st.error(f"API error: {error}")
    st.stop()

if not rows:
    st.warning(f"No awards returned for keyword '{keyword}' with these filters.")
    st.stop()

df = pd.DataFrame(rows)
df["Award Amount"] = pd.to_numeric(df.get("Award Amount"), errors="coerce").fillna(0)
df["Total Obligated Amount"] = pd.to_numeric(df.get("Total Obligated Amount"), errors="coerce").fillna(0)

# ── Header metrics ─────────────────────────────────────────────────
st.success(
    f"**{selected_rsrt}** — Keyword: `{keyword}` — "
    f"FY{fy_start}–FY{fy_end} — Higher-Ed Grants"
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Awards fetched", f"{len(df):,}")
col2.metric("Unique recipients", f"{df['Recipient Name'].nunique():,}")
col3.metric("Total Award Amount", f"${df['Award Amount'].sum()/1e6:,.1f}M")
col4.metric("Total Obligated", f"${df['Total Obligated Amount'].sum()/1e6:,.1f}M")

if max_pages * 100 == len(df):
    st.warning(
        f"⚠ Hit page cap of {max_pages * 100} awards. "
        f"Real total may be higher. Increase max pages in sidebar to see more."
    )

# ── Top recipients ─────────────────────────────────────────────────
st.markdown("### 🏛 Top 20 Recipients by Award Amount")
top_recipients = (df.groupby("Recipient Name")
                  .agg(awards=("Award ID", "count"),
                       total=("Award Amount", "sum"),
                       obligated=("Total Obligated Amount", "sum"))
                  .sort_values("total", ascending=False)
                  .head(20))
top_recipients["total"] = (top_recipients["total"] / 1e6).round(2)
top_recipients["obligated"] = (top_recipients["obligated"] / 1e6).round(2)
top_recipients.columns = ["Awards", "Award Amount ($M)", "Obligated ($M)"]
st.dataframe(top_recipients, use_container_width=True)

# ── By agency ──────────────────────────────────────────────────────
st.markdown("### 🏢 By Awarding Agency")
by_agency = (df.groupby("Awarding Agency")
             .agg(awards=("Award ID", "count"),
                  total=("Award Amount", "sum"))
             .sort_values("total", ascending=False))
by_agency["total"] = (by_agency["total"] / 1e6).round(2)
by_agency.columns = ["Awards", "Total ($M)"]
st.dataframe(by_agency, use_container_width=True)

# ── By sub-agency ──────────────────────────────────────────────────
st.markdown("### 🏛 By Awarding Sub-Agency (top 15)")
by_sub = (df.groupby("Awarding Sub Agency")
          .agg(awards=("Award ID", "count"),
               total=("Award Amount", "sum"))
          .sort_values("total", ascending=False)
          .head(15))
by_sub["total"] = (by_sub["total"] / 1e6).round(2)
by_sub.columns = ["Awards", "Total ($M)"]
st.dataframe(by_sub, use_container_width=True)

# ── Award browser ──────────────────────────────────────────────────
st.markdown("### 📋 Award Browser — Top 25 by Award Amount")
st.caption(f"Read descriptions to verify these are real **{selected_rsrt}** research.")

for i, row in df.nlargest(25, "Award Amount").iterrows():
    amt = row["Award Amount"]
    with st.expander(
        f"${amt/1e6:,.2f}M | {row.get('Recipient Name', '')[:60]} | "
        f"{row.get('Awarding Sub Agency', '')[:30]}",
        expanded=False,
    ):
        c1, c2 = st.columns([1, 1])
        c1.markdown(f"**Award ID:** `{row.get('Award ID', '')}`")
        c1.markdown(f"**Recipient UEI:** `{row.get('Recipient UEI', '')}`")
        c1.markdown(f"**Awarding Agency:** {row.get('Awarding Agency', '')}")
        c1.markdown(f"**Sub-Agency:** {row.get('Awarding Sub Agency', '')}")
        c2.markdown(f"**Start Date:** {row.get('Start Date', '')}")
        c2.markdown(f"**Award Type:** {row.get('Award Type', '')}")
        c2.markdown(f"**CFDA:** {row.get('cfda_number', '')} — {row.get('cfda_title', '')}")
        st.markdown(f"**Description:**")
        st.markdown(f"> {row.get('Description', '') or '_(no description)_'}")

# ── Download ───────────────────────────────────────────────────────
st.markdown("### 💾 Download")

df_export = df.copy()
df_export["rsrt"] = selected_rsrt
df_export["search_keyword"] = keyword
df_export["warfare_tier"] = WARFARE_TIER[selected_rsrt]

csv_buf = StringIO()
df_export.to_csv(csv_buf, index=False)
st.download_button(
    f"⬇ Download {selected_rsrt} awards as CSV",
    data=csv_buf.getvalue(),
    file_name=f"rsrt_{selected_rsrt}_FY{fy_start}-{fy_end}.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption(
    "Tip: Pull all 15 RSRTs one at a time, then concatenate CSVs to build "
    "the final master classified-awards file. No lexicon needed."
)
