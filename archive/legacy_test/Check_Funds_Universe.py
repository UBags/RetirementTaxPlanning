"""
ARCHIVED. Formerly test/Check_Funds_Universe.py: a scratch script, never a
pytest test, that printed how many funds in a per-user Fund_Metrics_Output.csv
passed the old test/portfolio_allocator.py eligibility screen.

Replaced by: allocate_funds' own eligibility filtering and its run report
(swp_planner.optimiser.allocate_funds). The precursor allocator it checked
against is untracked (older_versions/), so the thresholds here describe a
screen that no longer exists. The relative path below predates outputs/<user>/.
"""
import pandas as pd
import numpy as np

df = pd.read_csv('../Uddipan/Fund_Metrics_Output.csv')
df.columns = [c.strip() for c in df.columns]

# portfolio_allocator eligibility
for col in ['10Y_CAGR','5Y_CAGR','3Y_CAGR','1Y_CAGR','Std_Dev_10Y','Std_Dev_5Y','Std_Dev_3Y',
            'Max_DD_10Y','Max_DD_5Y','Max_DD_3Y','History_Months']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')

df['Expected_Return'] = df[['10Y_CAGR','5Y_CAGR','3Y_CAGR','1Y_CAGR']].min(axis=1)
df['std_worst'] = df[['Std_Dev_10Y','Std_Dev_5Y','Std_Dev_3Y']].max(axis=1)
df['max_dd_worst'] = df[['Max_DD_10Y','Max_DD_5Y','Max_DD_3Y']].min(axis=1)

pa_universe = df[(df['History_Months'] >= 84) &
                 df['Expected_Return'].notna() &
                 df['std_worst'].notna()].copy()

af_universe = df[(df['History_Months'] >= 84) &
                 df['Expected_Return'].notna() & (df['Expected_Return'] > 0) &
                 df['std_worst'].notna() & (df['std_worst'] > 0) &
                 df['max_dd_worst'].notna()].copy()

print(f"PA universe: {len(pa_universe)} funds")
print(f"AF universe: {len(af_universe)} funds")
print(f"\nIn PA but not AF:")
pa_names = set(pa_universe['Fund Name'])
af_names = set(af_universe['Fund Name'])
for f in sorted(pa_names - af_names):
    print(f"  {f}")