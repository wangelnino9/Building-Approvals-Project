"""
Explore the ABS Building Approvals dataflow before designing the star schema.

What it does:
  1. Lists ABS dataflows matching "building approvals" (so you can see what exists)
  2. Pulls the structure of one dataflow (dimensions + codelists with labels)
  3. Downloads a slice of the data as CSV and profiles it (grain, totals, duplicates)
  4. Saves everything under ./data/raw/ so it can feed the staging layer later

Run:
  pip install requests pandas
  python explore_abs_building_approvals.py
  python explore_abs_building_approvals.py BA_LGA2025 2024-01   # other flow / start period

Note: ABS dataflow IDs and dimension names can change between releases.
If a call fails, check the dataflow list printed in step 1 and the ABS Data API docs.
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.api.abs.gov.au/rest"
FLOW = sys.argv[1] if len(sys.argv) > 1 else "BA_GCCSA"
START = sys.argv[2] if len(sys.argv) > 2 else "2023-01"
OUT = Path("data/raw")
OUT.mkdir(parents=True, exist_ok=True)

STRUCT_HDR = {"Accept": "application/vnd.sdmx.structure+json"}
CSV_HDR = {"Accept": "application/vnd.sdmx.data+csv"}


def get(url, headers, params=None):
    r = requests.get(url, headers=headers, params=params, timeout=120)
    r.raise_for_status()
    return r


def lang(name_obj):
    """SDMX-JSON names are either a string or a {lang: text} dict."""
    if isinstance(name_obj, dict):
        return name_obj.get("en") or next(iter(name_obj.values()), "")
    return name_obj or ""


# 1. Which dataflows relate to building approvals? -------------------------
print("=== 1. Matching dataflows ===")
flows = get(f"{BASE}/dataflow/ABS", STRUCT_HDR).json()["data"]["dataflows"]
for f in flows:
    label = lang(f.get("name"))
    if "approval" in label.lower() or f["id"].startswith("BA_"):
        print(f"  {f['id']:<20} v{f.get('version', '?'):<8} {label}")

# 2. Structure: dimensions and codelists ------------------------------------
print(f"\n=== 2. Structure of {FLOW} ===")
struct = get(
    f"{BASE}/dataflow/ABS/{FLOW}",
    STRUCT_HDR,
    params={"references": "descendants"},
).json()["data"]
(OUT / f"{FLOW}_structure.json").write_text(json.dumps(struct, indent=2))

dsd = struct["dataStructures"][0]
dims = dsd["dataStructureComponents"]["dimensionList"]["dimensions"]
codelists = {c["id"]: c for c in struct.get("codelists", [])}

print("Dimensions (this is your candidate list of dimension tables):")
for d in dims:
    print(f"  - {d['id']}")

print("\nCodelists (code -> label), first 12 codes each:")
for cid, cl in codelists.items():
    codes = cl.get("codes", [])
    print(f"\n  {cid} ({len(codes)} codes)")
    for c in codes[:12]:
        print(f"    {c['id']:<10} {lang(c.get('name'))}")

# 3. Pull and profile data ---------------------------------------------------
print(f"\n=== 3. Data from {START} ===")
resp = get(f"{BASE}/data/ABS,{FLOW}/all", CSV_HDR, params={"startPeriod": START})
df = pd.read_csv(io.StringIO(resp.text))
df.to_csv(OUT / f"{FLOW}_{START}.csv", index=False)

print(f"Rows: {len(df):,}  Columns: {list(df.columns)}")

dim_cols = [d["id"] for d in dims if d["id"] in df.columns and d["id"] != "TIME_PERIOD"]
print("\nDistinct values per dimension:")
for col in dim_cols:
    print(f"  {col:<20} {df[col].nunique()}")

if "TIME_PERIOD" in df.columns:
    print(f"\nPeriods: {df['TIME_PERIOD'].min()} to {df['TIME_PERIOD'].max()}")
    print(f"Months loaded: {df['TIME_PERIOD'].nunique()}")

# Grain check: is (all dimensions + period) unique?
grain = dim_cols + ["TIME_PERIOD"]
dupes = df.duplicated(subset=grain).sum()
print(f"\nDuplicate rows at grain {grain}: {dupes}")

# Totals check: look for aggregate codes that would double count
print("\nCodes that may be totals (check these against the codelist labels):")
for col in dim_cols:
    vals = df[col].astype(str).unique()
    suspects = [v for v in vals if v in {"0", "00", "TOT", "AUS", "9", "99"} or "TOT" in v.upper()]
    if suspects:
        print(f"  {col}: {suspects}")

# Missing values / observation column
if "OBS_VALUE" in df.columns:
    print(f"\nOBS_VALUE nulls: {df['OBS_VALUE'].isna().sum()}")
    print(df["OBS_VALUE"].describe())

print(f"\nSaved raw files to {OUT.resolve()}")
print("Next: decide the grain, then decide which total rows to exclude from the fact table.")