import pandas as pd
import pulp
import numpy as np
import itertools
import math
from pathlib import Path

def load_and_clean_data(filepath):
    """Loads the fund metrics data and prepares it for optimization."""
    print(f"Loading data from {filepath}...")
    df = pd.read_csv(filepath)
    print(f"  Input CSV (full path): {Path(filepath).resolve()}")

    # Ensure relevant columns are numeric
    return_cols = ['10Y_CAGR', '5Y_CAGR', '3Y_CAGR']
    std_cols = ['Std_Dev_10Y', 'Std_Dev_5Y', 'Std_Dev_3Y']
    max_dd_cols = ['Max_DD_10Y', 'Max_DD_5Y', 'Max_DD_3Y']
    sortino_cols = ['Sortino_10Y', 'Sortino_5Y', 'Sortino_3Y']
    history_col = ['History_Months']

    for col in return_cols + std_cols + max_dd_cols + sortino_cols + history_col:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Apply History Months constraint (Minimum 84 months)
    if 'History_Months' in df.columns:
        df = df[df['History_Months'] >= 84]

    # a) Calculate Expected Return (WORST case: Minimum of available CAGRs)
    df['Expected_Return'] = df[return_cols].min(axis=1)

    # b) Calculate std_worst (WORST case: Maximum of available standard deviations)
    df['std_worst'] = df[std_cols].max(axis=1)

    # b) Calculate max_dd_worst (WORST case: Minimum of available max drawdowns, since they are negative)
    df['max_dd_worst'] = df[max_dd_cols].min(axis=1).fillna(0)

    # Calculate Sortino averages (Keeping average for ratios as requested to leave them out of worst-case logic)
    df['sortino_avg'] = df[sortino_cols].mean(axis=1).fillna(0)

    # Clean up Fund Type
    df['Fund Type'] = df['Fund Type'].fillna('Unknown Type')

    # Extract Tax Family (First word of Fund Type) to serve as our new tax classification
    df['Tax Family'] = df['Fund Type'].apply(lambda x: str(x).split()[0])

    # Drop rows that don't have enough data to be evaluated
    df = df.dropna(subset=['Expected_Return', 'std_worst']).reset_index(drop=True)

    return df


def optimize_portfolio(df, portfolio_name, min_return, max_alloc_fund=0.08, max_alloc_sub=0.24, min_alloc_fund=0.02,
                       max_portfolios=15, max_overall_std=0.025):
    """
    Runs a MILP optimization sequentially to find multiple portfolios.
    Forces each subsequent portfolio to have a strictly higher linear overall standard deviation.
    Stops if max_portfolios are found or if the quadratic calculated standard deviation > max_overall_std (2.5%).
    Re-runs with a higher constraint if a duplicate portfolio is detected.
    """
    print(f"\n{'=' * 60}")
    print(f"Starting Scenarios for: {portfolio_name} (Min Return: {min_return * 100}%)")
    print(f"{'=' * 60}")

    funds = df.index.tolist()
    print(f"  Fund Universe: {len(funds)} funds")  # ADD THIS

    # Pre-compute dictionaries to eliminate slow DataFrame `.loc` lookups during model building.
    expected_returns = df['Expected_Return'].to_dict()
    std_worsts = df['std_worst'].to_dict()
    fund_types = df.groupby('Fund Type').groups

    found_portfolios = []
    previous_port_std = None

    p_idx = 1
    attempts = 0
    max_attempts = 50  # Safeguard to prevent infinite loops

    while p_idx <= max_portfolios and attempts < max_attempts:
        attempts += 1
        portfolio_found = False
        is_dup = False

        # Try progressively relaxing the max standard deviation ratio constraint
        for std_multiplier in [3, 4, 5]:
            # Define the problem: We want to MINIMIZE standard deviation
            prob = pulp.LpProblem(f"Opt_{portfolio_name.replace(' ', '_')}_P{p_idx}_m{std_multiplier}_a{attempts}",
                                  pulp.LpMinimize)

            # Variables
            w = pulp.LpVariable.dicts("w", funds, lowBound=0, upBound=max_alloc_fund)
            z = pulp.LpVariable.dicts("z", funds, cat='Binary')

            # SPARSE MATRIX TRICK
            port_std = pulp.LpVariable("port_std", lowBound=0)
            prob += port_std == pulp.lpSum([w[i] * std_worsts[i] for i in funds]), "Def_Port_Std"

            # OBJECTIVE
            prob += port_std, "Minimize_Volatility"

            # CONSTRAINTS:
            # 1. Total allocation must equal 100% (1.0)
            prob += pulp.lpSum([w[i] for i in funds]) == 1.0, "Total_Allocation"

            # 2. Portfolio return must be >= Minimum Return
            prob += pulp.lpSum([w[i] * expected_returns[i] for i in funds]) >= min_return, "Min_Expected_Return"

            # 3. Minimum and Maximum limits per fund
            for i in funds:
                prob += w[i] >= min_alloc_fund * z[i], f"Min_Alloc_{i}"
                prob += w[i] <= max_alloc_fund * z[i], f"Max_Alloc_{i}"

            # 4. Maximum allocation per Sub-Fund Type
            # (Explicitly uses the "Fund Type" column from the CSV as requested)
            for st, st_funds in fund_types.items():
                if len(st_funds) > 0:
                    prob += pulp.lpSum([w[i] for i in st_funds]) <= max_alloc_sub, f"Max_Alloc_Sub_{st}"

            # 5. Fund std_dev cannot exceed std_multiplier * overall portfolio std_dev
            for i in funds:
                prob += std_worsts[i] * z[i] <= std_multiplier * port_std, f"Max_Std_Dev_Ratio_{i}"

            # 6. ITERATIVE CONSTRAINT: Overall Std Dev must be > than previous portfolio's std dev
            if previous_port_std is not None:
                # Add a epsilon to strictly push it out
                prob += port_std >= previous_port_std + 0.0001, "Iterative_Std_Increase"

            # SOLVE (threads=1 and gapRel=1e-6 guarantees 100% deterministic runs)
            if p_idx == 1 and std_multiplier == 3:
                print("Starting solver (threads=1 for determinism, this might take a minute)...")

            prob.solve(pulp.PULP_CBC_CMD(msg=0, gapRel=0.000001, timeLimit=120, threads=1))

            # Gather Results
            if pulp.LpStatus[prob.status] == 'Optimal':
                results = []

                for i in funds:
                    weight = w[i].varValue
                    if weight is not None and weight > 0.0001:
                        results.append({
                            'Fund Name': df.loc[i, 'Fund Name'],
                            'Tax Family': df.loc[i, 'Tax Family'],
                            'Fund Type': df.loc[i, 'Fund Type'],
                            'Allocation (%)': round(weight * 100, 2),
                            'Worst Std Dev': round(std_worsts[i], 4),
                            'Worst Max DD': round(df.loc[i, 'max_dd_worst'], 4),
                            'Min CAGR (%)': round(expected_returns[i] * 100, 2),
                            '1Y CAGR (%)': round(df.loc[i, '1Y_CAGR'] * 100, 2) if pd.notna(
                                df.loc[i, '1Y_CAGR']) else np.nan,
                            '3Y CAGR (%)': round(df.loc[i, '3Y_CAGR'] * 100, 2) if pd.notna(
                                df.loc[i, '3Y_CAGR']) else np.nan,
                            '5Y CAGR (%)': round(df.loc[i, '5Y_CAGR'] * 100, 2) if pd.notna(
                                df.loc[i, '5Y_CAGR']) else np.nan,
                            '10Y CAGR (%)': round(df.loc[i, '10Y_CAGR'] * 100, 2) if pd.notna(
                                df.loc[i, '10Y_CAGR']) else np.nan,
                            'History Months': df.loc[i, 'History_Months']
                        })

                results_df = pd.DataFrame(results).sort_values(by='Allocation (%)', ascending=False)

                # Check for duplicates before processing
                for ep in [p[0] for p in found_portfolios]:  # Compare against previously found results_dfs
                    if len(results_df) == len(ep):
                        merged = results_df.merge(ep, on='Fund Name', suffixes=('_new', '_old'))
                        # If same funds exist and weights are extremely close (< 0.5% diff)
                        if len(merged) == len(results_df):
                            max_diff = (merged['Allocation (%)_new'] - merged['Allocation (%)_old']).abs().max()
                            if max_diff < 0.5:
                                is_dup = True
                                break

                if is_dup:
                    print(
                        f"⚠️ Duplicate portfolio detected. Bumping overall_min_std_dev constraint to force alternatives...")
                    # Force a larger jump in standard deviation to escape the duplicate
                    previous_port_std = port_std.varValue + 0.0005
                    break  # Break inner multiplier loop, continue outer while loop

                # If unique portfolio is found:
                portfolio_found = True

                # Extract metrics for overall calculations
                selected_funds = [i for i in funds if w[i].varValue is not None and w[i].varValue > 0.0001]
                p_weights = np.array([w[i].varValue for i in selected_funds])
                p_returns = np.array([expected_returns[i] for i in selected_funds])
                p_stds = np.array([std_worsts[i] for i in selected_funds])
                p_max_dds = np.array([df.loc[i, 'max_dd_worst'] for i in selected_funds])
                p_sortinos = np.array([df.loc[i, 'sortino_avg'] for i in selected_funds])

                calc_return = np.sum(p_weights * p_returns)
                calc_std_dev = np.sqrt(np.sum((p_weights ** 2) * (p_stds ** 2)))
                calc_max_dd = np.sum(p_weights * p_max_dds)
                calc_sharpe = calc_return / calc_std_dev if calc_std_dev > 0 else 0
                calc_sortino = np.sum(p_weights * p_sortinos)
                calc_calmar = calc_return / abs(calc_max_dd) if calc_max_dd != 0 else 0

                print(
                    f"Found the next portfolio with return : {calc_return * 100:.2f}%, overall_std_dev : {calc_std_dev * 100:.4f}%, overall_mx_dd : {calc_max_dd * 100:.2f}%")

                # Cleanup and record
                results_df = results_df.drop(columns=['History Months'])
                break  # We found the optimal for this p_idx, skip other multipliers

        # Handle iteration state
        if is_dup:
            continue  # Skip saving and incrementing, immediately retry with bumped constraint

        if portfolio_found:
            # Bundle all metrics to carry forward for combination evaluation
            metrics_dict = {
                'return': calc_return,
                'std_dev': calc_std_dev,
                'max_dd': calc_max_dd,
                'sharpe': calc_sharpe,
                'sortino': calc_sortino,
                'calmar': calc_calmar
            }
            found_portfolios.append((results_df, metrics_dict))  # Store tuple of (df, metrics_dict)
            previous_port_std = port_std.varValue  # Store linear std proxy to offset the next run
            p_idx += 1  # Only increment counter for successful unique portfolio

            # Check stopping condition based on true quadratic std_dev formula
            if calc_std_dev > max_overall_std:
                print(
                    f"⚠️ Reached limit: Portfolio {p_idx - 1}'s calculated std_dev ({calc_std_dev * 100:.2f}%) exceeded {max_overall_std * 100}%. Stopping iterations.")
                break
        else:
            print(f"❌ Could not find a feasible solution for Portfolio {p_idx}. Stopping.")
            break

    return found_portfolios


def find_best_combination(all_scenario_results, scenario_names):
    """
    Evaluates all combinations of portfolios using a generalized Target Distribution Fitness Algorithm.
    It targets ~60% of unique funds to be present in ALL N portfolios,
    and >20% to be present in exactly (N-1) portfolios.
    """
    N = len(scenario_names)
    target_N_minus_1 = max(1, N - 1)

    print("\n" + "=" * 80)
    print("Evaluating all portfolio combinations for Target Distribution Match...")
    print(f"Target: ~60% funds in all {N} portfolios | >=20% in exactly {target_N_minus_1} portfolios")
    print("=" * 80)

    # Extract the lists of portfolios dynamically
    lists = [all_scenario_results[name] for name in scenario_names]

    # Calculate the total theoretical combinations
    total_combos = math.prod([len(l) for l in lists])
    print(f"Total combinations to evaluate: {total_combos}")

    best_combo = None
    min_penalty = float('inf')
    min_sum_std = float('inf')

    # Using itertools.product dynamically creates an iterator to save memory
    combinations = itertools.product(*lists)

    for combo in combinations:
        dfs = [p[0] for p in combo]
        metrics = [p[1] for p in combo]

        # Combine dataframes to calculate commonality
        combined_df = pd.concat(dfs)

        # Group by Fund Name to count occurrences
        fund_counts = combined_df['Fund Name'].value_counts()

        total_unique = len(fund_counts)
        count_all_N = (fund_counts == N).sum()
        count_N_minus_1 = (fund_counts == target_N_minus_1).sum() if N > 1 else 0

        ratio_all_N = count_all_N / total_unique if total_unique > 0 else 0
        ratio_N_minus_1 = count_N_minus_1 / total_unique if total_unique > 0 else 0

        # --- GENERALIZED TARGET DISTRIBUTION ALGORITHM ---
        # 1. Penalty for missing 60% in all N portfolios.
        penalty_all = abs(ratio_all_N - 0.60)

        # 2. Penalty for falling below 20% in N-1 portfolios (0 penalty if >= 20%).
        penalty_N_minus_1 = max(0, 0.20 - ratio_N_minus_1) if N > 1 else 0

        total_penalty = penalty_all + penalty_N_minus_1

        # Tie-breaker sum of std deviations
        sum_std = sum(m['std_dev'] for m in metrics)

        # Check if it's the new best (closest to our ideal distribution targets)
        if round(total_penalty, 4) < round(min_penalty, 4) or (
                round(total_penalty, 4) == round(min_penalty, 4) and sum_std < min_sum_std):
            min_penalty = total_penalty
            min_sum_std = sum_std
            best_combo = combo

    print(f"✅ Found Best Combination matching distribution targets!")
    print(f"-> Minimized Distribution Penalty Score: {min_penalty:.4f}")
    print(f"-> Minimized Sum of Worst Std Devs: {min_sum_std * 100:.4f}%")

    return best_combo, min_penalty, min_sum_std


def main():
    filepath = '../Uddipan/Fund_Metrics_Output.csv'
    try:
        df = load_and_clean_data(filepath)
    except FileNotFoundError:
        print(f"Error: Could not find '{filepath}'. Please ensure it is in the same folder as this script.")
        return

    # You can add or remove scenarios from this list; the algorithm dynamically scales to N chunks
    scenarios = [
        {"name": "Portfolio 1 (Yrs 1 - 10)", "min_return": 0.080},  # 8%
        {"name": "Portfolio 2 (Yrs 11 - 20)", "min_return": 0.075},  # 7.5%
        {"name": "Portfolio 3 (Yrs 21 - 30)", "min_return": 0.070},  # 7%
    ]

    # Store all found scenarios in memory as requested for combinations
    all_scenario_results = {}

    for scenario in scenarios:
        portfolios = optimize_portfolio(
            df=df,
            portfolio_name=scenario["name"],
            min_return=scenario["min_return"],
            max_alloc_fund=0.08,
            max_alloc_sub=0.24,
            min_alloc_fund=0.02,
            max_portfolios=25,  # INCREASED to 25 to give us multiple combinations for the target algorithm
            max_overall_std=0.025  # Stop if quadratic std dev exceeds 2.5%
        )

        if portfolios and len(portfolios) > 0:
            # Save into memory dictionary
            all_scenario_results[scenario["name"]] = portfolios

    # Once all scenarios are completed, find the best combination
    scenario_names = [s["name"] for s in scenarios]

    if len(all_scenario_results) == len(scenario_names):
        best_combo, best_score, best_std = find_best_combination(all_scenario_results, scenario_names)

        if best_combo:
            with pd.ExcelWriter('Optimized_Portfolios.xlsx') as writer:
                for i, p_tuple in enumerate(best_combo):
                    best_portfolio_df = p_tuple[0]  # Extract df from the tuple
                    metrics = p_tuple[1]  # Extract the stored metrics dictionary

                    # Export the winning portfolio for this scenario to Excel
                    best_portfolio_df.to_excel(writer, sheet_name=scenario_names[i][:31], index=False)

                    # Print the winning portfolio's table to console
                    print("\n" + "=" * 80)
                    print(f"BEST COMBINATION - {scenario_names[i]} Allocation:")
                    print("=" * 80)
                    print(best_portfolio_df.to_string(index=False))

                    # Print the portfolio's specific overall metrics as requested
                    print("\n--- Portfolio Metrics ---")
                    print(f"overall_std_dev : {metrics['std_dev'] * 100:.4f}%")
                    print(f"max_dd          : {metrics['max_dd'] * 100:.2f}%")
                    print(f"Sharpe_ratio    : {metrics['sharpe']:.4f}")
                    print(f"Calmar_ratio    : {metrics['calmar']:.4f}")
                    print(f"Sortino_ratio   : {metrics['sortino']:.4f}")
                    print("=" * 80 + "\n")

            # Calculate and print dynamic combination fund statistics across N chunks
            dfs = [p[0] for p in best_combo]
            combined_best_df = pd.concat(dfs)
            fund_counts = combined_best_df['Fund Name'].value_counts()

            print("--- Combination Fund Statistics ---")
            total_unique = len(fund_counts)
            N = len(scenario_names)

            print(f"Total number of unique funds across all {N} portfolios = {total_unique}")

            # Dynamically count and print for each overlap level from N down to 1
            for i in range(N, 0, -1):
                count_i = (fund_counts == i).sum()
                label = f"all {N}" if i == N else str(i)

                # Adding "only" for the case where a fund appears in just 1 portfolio
                if i == 1 and N > 1:
                    label = "only 1"

                pct = (count_i / total_unique * 100) if total_unique > 0 else 0
                print(f"Number of funds in {label} = {count_i} ({pct:.1f}%)")

            print("=" * 80 + "\n")

            print(
                "Optimization & Combination Complete! The highly-correlated best combination is saved to 'Optimized_Portfolios.xlsx'.")
    else:
        print("Could not evaluate combinations because one or more scenarios failed to generate any portfolios.")


if __name__ == "__main__":
    main()