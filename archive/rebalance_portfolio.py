# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""Glide-path rebalancing and its transition-tax lookahead. ARCHIVED.

REPLACED BY
    Nothing, yet. The capacity-driven transition scheduler in HANDOVER 4.3
    will occupy this socket. It is a different algorithm, not a repair of this
    one, which is why this is archived rather than left in place to be edited
    into shape.

WHY IT WAS REMOVED
    It was already dead. The glide-path transition scheduler was retired to a
    hard cut in bfbf226 -- 341 lines, no snapshot moved -- and that commit
    removed the only caller of both functions. Neither has run since.
    glide_costs_by_fy in Engine.run() is the visible residue: it is
    initialised and read while nothing ever writes to it.

    It was not merely unused. Measured before removal, the glide path took two
    years costing ZERO tax and made them cost 10.75 (s1_2chunk FY14-15). Its
    per-year budget was total_transition_tax / spread_years -- an even split of
    the tax BILL, where what is needed is each year's remaining tax-free
    HEADROOM. It smoothed gain across progressive slabs rather than using free
    capacity. See HANDOVER_REVISION 4.2 and 7.2.

    Two live defects went with it by deletion rather than repair: min(...)
    where max(...) was meant, so with three boundaries the HUF throttled its
    FY24 transition against the FY9 budget; and the ValueError that expression
    raises when the dict is non-empty but every key exceeds fy.

AND IT IS WHERE HIFO LIVED
    Both functions sort every lot by gain and sell the cheapest first -- HIFO,
    described in the old header as "Tax Alpha". THAT IS NOT ACHIEVABLE. Indian
    practice computes capital gains FIFO per folio (CBDT Circular 704), and an
    AMC cannot be instructed to sell your highest-cost units. Folio choice is
    the only lot-selection lever there is.

    These were the only two places in the engine that did it. With them
    archived the engine holds no HIFO at all: FIFOBucket.redeem is pure FIFO
    within a folio, and FolioSet chooses between folios. Removing them cost no
    snapshot movement, because dead code cannot move one.

STATE WHEN ARCHIVED
    Phase 4b-iii had converted both lot-mutating sites in _rebalance_portfolio
    to route through FolioSet when multiple folios exist -- sell_ordered for
    the forced sell, sell_to_tax_budget for the budget-capped one. Those
    conversions are preserved below. They were made before the function was
    known to be orphaned; HANDOVER 4.10's inventory was built by grepping for
    lot assignments, which finds assignments but not callers.

NOT IMPORTED, AND WILL NOT RUN. _estimate_transition_tax is an Engine method,
wrapped in a shell class here only so the file parses.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from configuration import config
from models import AppState, GlidePath, RebalanceCost

from engine import CESS, FIFOBucket, Lot, new_folio_set


def _rebalance_portfolio(
    target_weights:    Dict[str, float],
    prior_weights:     Dict[str, float],
    fund_buckets:      Dict[str, 'FolioSet'],
    fund_navs:         Dict[str, float],
    fund_types:        Dict[str, str],         # fund_name -> "debt"|"equity"|"other"
    state:             'AppState',
    fy:                int,
    month_idx:         int,
    drift_tolerance:   float = 0.03,    # Skip rebalance if total |drift| < this (no-trade region)
    swp_cash_needed:   float = 0.0,     # SWP amount to raise preferentially from over-weighted funds
    use_hifo:          bool  = True,    # HIFO (lowest-gain-first) lot selection to minimise CGT
    annual_tax_budget: float = float('inf'),  # Max rebalancing tax Rs lakhs this FY (lookahead budget)
) -> Tuple[RebalanceCost, Dict[str, float], Dict[str, float]]:
    """
    Perform one annual micro-rebalancing step from prior_weights toward
    target_weights.

    Key design principles (v7 upgrades):

    1.  Self-funded tax (Bug #1 fix):
        Tax from rebalancing sells is deducted from the portfolio's own cash
        BEFORE executing buys.  The user's net_cash_personal (SWP income) is
        never reduced by rebalancing taxes — the portfolio absorbs the cost.

    2.  HIFO lot selection (Tax Alpha):
        When selling to rebalance, lots are sorted by ASCENDING realised gain
        (lowest gain first = Highest cost basis In, First Out).  Selling the
        cheapest-to-realise lots minimises CGT by 30-50% in mature portfolios.
        FIFO is still used for normal monthly SWP withdrawals (legal requirement
        for retail redemptions).

    3.  No-trade threshold (drift_tolerance):
        If the total absolute drift sum(|target_w - current_w|) across all
        funds is below ``drift_tolerance`` (default 3%), the entire rebalance
        is skipped.  This implements the Garleanu & Pedersen no-trade region.

    4.  Smart buy-side / SWP-assisted rebalancing:
        If ``swp_cash_needed`` > 0, the engine first raises that cash by
        preferentially selling OVER-weighted funds.  Regular withdrawals do
        rebalancing work, avoiding separate explicit trades.  The returned
        ``swp_proceeds`` dict tells the caller how much was already raised
        per fund so it can skip those funds in its own redemption pass.

    Returns:
        (RebalanceCost, rebal_gains_by_cat, swp_proceeds)
          rebal_gains_by_cat: {'d': float, 'e': float, 'o': float}
          RebalanceCost.taxes_paid is self-funded (already deducted from
          portfolio; caller must NOT subtract it from net_cash_personal).
    """
    STCG_MONTHS      = config.stcg_holding_months
    EXIT_LOAD        = config.exit_load_fraction

    # ── Read tax rates from user's config for this FY ────────────────
    # These are estimation rates for mid-year cash-retention and budget-
    # capping purposes.  The actual authoritative tax is always computed
    # at FY boundaries by the year-end block which pools ALL gains and
    # applies exemptions exactly once.
    _ec = state.get_equity_tax_chunk(fy, "individual")
    EQUITY_LTCG_RATE = _ec.tax_rate if _ec else config.fallback_equity_ltcg_rate
    _oc = state.get_other_tax_chunk(fy, "individual")
    OTHER_LTCG_RATE  = _oc.tax_rate if _oc else config.fallback_other_ltcg_rate

    _dc_budget = state.get_debt_tax_chunk(fy, "individual")
    if _dc_budget and _dc_budget.slabs:
        _debt_top_rate = max(s.rate for s in _dc_budget.slabs) * (1 + CESS)
    else:
        _debt_top_rate = config.fallback_debt_top_rate * (1 + CESS)

    # ── Current portfolio value ──────────────────────────────────────
    corpus_total = sum(
        bk.current_value(fund_navs[fn])
        for fn, bk in fund_buckets.items()
        if fn in fund_navs
    )
    if corpus_total < 1e-6:
        return (RebalanceCost(year=fy, taxes_paid=0.0, exit_loads_paid=0.0),
                {'d': 0.0, 'e': 0.0, 'o': 0.0},
                {})

    # ── Compute current weights ──────────────────────────────────────
    current_weights: Dict[str, float] = {
        fn: bk.current_value(fund_navs[fn]) / corpus_total
        for fn, bk in fund_buckets.items()
        if fn in fund_navs
    }

    # ── No-trade threshold check (Improvement #3 — Garleanu & Pedersen) ──
    all_names = (set(target_weights.keys()) | set(prior_weights.keys())
                 | set(fund_buckets.keys()))
    total_drift = sum(
        abs(target_weights.get(fn, 0.0) - current_weights.get(fn, 0.0))
        for fn in all_names
        if fn in fund_navs
    )
    if total_drift < drift_tolerance:
        # Portfolio is within the no-trade region — skip rebalancing
        return (RebalanceCost(year=fy, taxes_paid=0.0, exit_loads_paid=0.0),
                {'d': 0.0, 'e': 0.0, 'o': 0.0},
                {})

    taxes_paid      = 0.0
    exit_loads_paid = 0.0
    rebal_gains: Dict[str, float] = {'d': 0.0, 'e': 0.0, 'o': 0.0}

    # ─────────────────────────────────────────────────────────────────
    # Helper: sell ``sell_amt`` from fund ``fn`` using HIFO lot selection.
    # Returns (gains_stcg, gains_ltcg, exit_loads, cash_net_of_loads).
    # ─────────────────────────────────────────────────────────────────
    def _sell_hifo(fn: str, sell_amt: float) -> tuple:
        bk  = fund_buckets.get(fn)
        if bk is None or sell_amt < 0.01:
            return 0.0, 0.0, 0.0, 0.0
        nav = fund_navs[fn]

        # Multi-folio: folio choice is the only lot-selection lever there is.
        # Gains are computed FIFO per folio (CBDT Circular 704) and an AMC
        # cannot be instructed to sell your highest-cost units, so once a
        # second folio exists the sort-and-reassign below is unachievable in
        # reality and refused by the .lots setter. sell_ordered draws
        # cheapest-folio first, FIFO within each.
        #
        # Unreachable while folio_cohort_threshold = 0.0 opens exactly one
        # folio per fund, so this commit moves no snapshot. use_hifo does not
        # reach this branch: the HIFO sort below is a modelling fiction
        # (HANDOVER_REVISION 10.2), and correcting it for the single-folio
        # path moves every snapshot and is a separate commit.
        if len(bk.order) > 1:
            stcg, ltcg, loads, sold = bk.sell_ordered(
                sell_amt, nav, month_idx, STCG_MONTHS, EXIT_LOAD,
                prefer="cash")
            # sold, not sell_amt: if the folios together cannot cover the
            # amount, returning sell_amt would claim cash that was never
            # raised -- the 4b-i defect wearing a different hat.
            return stcg, ltcg, loads, sold - loads

        if use_hifo:
            # Sort ascending by gain-per-unit → sell lowest-gain lots first
            sorted_lots = sorted(bk.lots, key=lambda lot: (nav - lot.purchase_nav))
        else:
            sorted_lots = list(bk.lots)   # FIFO

        remaining  = sell_amt
        stcg = ltcg = loads = 0.0
        kept = []
        for lot in sorted_lots:
            if remaining <= 1e-12:
                kept.append(lot)
                continue
            lot_val    = lot.units * nav
            take_val   = min(lot_val, remaining)
            take_units = take_val / nav
            holding    = month_idx - lot.purchase_month
            gain       = take_units * (nav - lot.purchase_nav)
            if holding < STCG_MONTHS:
                stcg  += max(0.0, gain)
                loads += take_val * EXIT_LOAD
            else:
                ltcg  += max(0.0, gain)
            remaining -= take_val
            leftover   = lot.units - take_units
            if leftover > 1e-12:
                kept.append(Lot(leftover, lot.purchase_nav, lot.purchase_month))
        bk.lots = kept
        return stcg, ltcg, loads, sell_amt - loads

    def _tax_for(ftype: str, stcg: float, ltcg: float) -> float:
        """Estimate tax for budget/cash purposes during rebalancing.

        IMPORTANT: This is an *estimation* used to determine how much cash
        the portfolio must retain to cover its own tax bill.  The actual,
        authoritative tax is computed at year-end by the FY-boundary block
        which pools ALL gains (SWP + rebalancing) and applies exemptions
        exactly once.

        We deliberately do NOT apply the equity LTCG exemption here because
        it is a once-per-FY benefit shared with SWP gains.  Applying it
        per-fund here would under-estimate the tax, causing the buy-side to
        be over-funded and the portfolio to absorb an unfunded liability.
        """
        if ftype == "equity":
            # No exemption here — it's applied once at year-end
            return (stcg + ltcg) * EQUITY_LTCG_RATE * (1 + CESS)
        elif ftype == "other":
            return (stcg + ltcg) * OTHER_LTCG_RATE * (1 + CESS)
        else:
            # Use a conservative flat estimate for debt.  The slab-based
            # calculation needs the full FY income context (other income +
            # all debt gains) which isn't available fund-by-fund here.
            # 15% + 4% cess is a reasonable mid-bracket estimate.
            return (stcg + ltcg) * config.fallback_stcg_rate * (1 + CESS)

    def _ck(ftype: str) -> str:
        return 'e' if ftype == "equity" else ('o' if ftype == "other" else 'd')

    def _slice_tax_of(ftype: str):
        """Marginal tax on one lot slice's gain, as a callable of gain alone.

        Handed to FolioSet.sell_to_tax_budget so the folio walk lives in the
        corpus representation while the rate schedule stays here.

        MIRRORS the inline rate block in the budget-capped sell below, rather
        than replacing it. Rewriting that block would touch the single-folio
        path, and this commit's whole claim is that the single-folio path is
        untouched. Both go when the HIFO correction retires that branch
        (HANDOVER_REVISION 10.2).
        """
        if ftype == "equity":
            rate = EQUITY_LTCG_RATE * (1 + CESS)
        elif ftype == "other":
            rate = OTHER_LTCG_RATE * (1 + CESS)
        else:
            rate = _debt_top_rate
        return lambda gain: max(0.0, gain) * rate

    # ─────────────────────────────────────────────────────────────────
    # Step 1: Smart buy-side / SWP-assisted rebalancing (Improvement #4)
    #
    # Raise swp_cash_needed by preferentially selling OVER-weighted funds.
    # Regular withdrawals do the rebalancing heavy-lifting before any
    # explicit rebalancing trades are needed.
    # ─────────────────────────────────────────────────────────────────
    swp_proceeds: Dict[str, float] = {}   # fund_name -> cash already raised for SWP

    if swp_cash_needed > 0.01:
        over_funds = [
            (fn, (current_weights.get(fn, 0.0) - target_weights.get(fn, 0.0)) * corpus_total)
            for fn in all_names
            if (fn in fund_navs and fn in fund_buckets
                and current_weights.get(fn, 0.0) > target_weights.get(fn, 0.0) + 1e-4)
        ]
        over_funds.sort(key=lambda x: -x[1])   # largest over-weight first

        remaining_swp = swp_cash_needed
        for fn, excess_val in over_funds:
            if remaining_swp <= 0.01:
                break
            sell_amt = min(excess_val, remaining_swp)
            stcg, ltcg, ld, net = _sell_hifo(fn, sell_amt)
            ftype = fund_types.get(fn, "debt")
            rebal_gains[_ck(ftype)] += stcg + ltcg
            taxes_paid              += _tax_for(ftype, stcg, ltcg)
            exit_loads_paid         += ld
            swp_proceeds[fn]         = swp_proceeds.get(fn, 0.0) + net
            remaining_swp           -= sell_amt

    # ─────────────────────────────────────────────────────────────────
    # Step 2: Residual sell / buy deltas after SWP-assisted sells
    # ─────────────────────────────────────────────────────────────────
    corpus_now = sum(
        bk.current_value(fund_navs[fn])
        for fn, bk in fund_buckets.items()
        if fn in fund_navs
    )
    if corpus_now < 1e-6:
        cost = RebalanceCost(year=fy, taxes_paid=taxes_paid, exit_loads_paid=exit_loads_paid)
        return cost, rebal_gains, swp_proceeds

    sell_ops: List[Tuple[str, float]] = []
    buy_ops:  List[Tuple[str, float]] = []

    for fn in all_names:
        if fn not in fund_navs:
            continue
        bk          = fund_buckets.get(fn)
        current_val = bk.current_value(fund_navs[fn]) if bk else 0.0
        target_val  = corpus_now * target_weights.get(fn, 0.0)
        delta       = target_val - current_val
        if delta < -0.01:
            sell_ops.append((fn, -delta))
        elif delta > 0.01:
            buy_ops.append((fn, delta))

    # ── Sells with HIFO — budget-capped ─────────────────────────────
    # When annual_tax_budget < inf, sell lot-by-lot and stop exactly when
    # the running tax tally hits the budget (Gemini v8 lookahead approach).
    # This ensures each transition year bears an equal share of the total
    # transition tax, smoothing the incidence across the spread window.
    cash_raised = 0.0
    rebal_tax_tally = taxes_paid  # includes any tax already paid in Step 1 (SWP)
    for fn, sell_amt in sell_ops:
        budget_left = annual_tax_budget - rebal_tax_tally
        if budget_left <= 1e-4:
            break   # budget exhausted — defer remaining sells to future years
        ftype = fund_types.get(fn, "debt")
        nav   = fund_navs[fn]
        bk    = fund_buckets.get(fn)
        if bk is None:
            continue

        # Multi-folio: site 1's guard, applied to the budget-capped sell.
        # bk.lots = kept below is refused once a second folio exists, and the
        # mid-lot budget stop is why sell_ordered cannot serve here.
        # Unreachable while folio_cohort_threshold = 0.0 opens exactly one
        # folio per fund, so this commit moves no snapshot.
        if len(bk.order) > 1:
            stcg_f, ltcg_f, ld_f, net_f, tax_f = bk.sell_to_tax_budget(
                sell_amt, nav, month_idx, STCG_MONTHS, EXIT_LOAD,
                budget_left, _slice_tax_of(ftype), prefer="cash")
            rebal_tax_tally         += tax_f
            rebal_gains[_ck(ftype)] += stcg_f + ltcg_f
            taxes_paid              += _tax_for(ftype, stcg_f, ltcg_f)
            exit_loads_paid         += ld_f
            cash_raised             += net_f
            continue

        # HIFO sort (or FIFO)
        sorted_lots = (sorted(bk.lots, key=lambda lot: (nav - lot.purchase_nav))
                       if use_hifo else list(bk.lots))
        rem_amt = sell_amt
        stcg_f = ltcg_f = ld_f = net_f = 0.0
        kept = []
        for lot in sorted_lots:
            if rem_amt <= 1e-6:
                kept.append(lot)
                continue
            budget_left = annual_tax_budget - rebal_tax_tally
            if budget_left <= 1e-4:
                kept.append(lot)   # budget hit mid-lot — stop here
                continue
            take_val   = min(lot.units * nav, rem_amt)
            take_units = take_val / nav
            gain       = take_units * (nav - lot.purchase_nav)
            holding    = month_idx - lot.purchase_month
            # Marginal tax for this lot slice
            # Use the user's top slab rate (+ cess) for debt; for equity/other
            # use their flat LTCG rates.  This avoids the hardcoded 0.312 and
            # respects the user's actual tax configuration.
            if ftype == "equity":
                slice_tax = max(0.0, gain) * EQUITY_LTCG_RATE * (1 + CESS)
            elif ftype == "other":
                slice_tax = max(0.0, gain) * OTHER_LTCG_RATE * (1 + CESS)
            else:
                slice_tax = max(0.0, gain) * _debt_top_rate
            # If this full lot would exceed budget, fraction it
            if slice_tax > budget_left:
                fraction  = budget_left / slice_tax if slice_tax > 0 else 1.0
                take_val   *= fraction
                take_units *= fraction
                gain       *= fraction
                slice_tax   = budget_left
            if holding < STCG_MONTHS:
                stcg_f += max(0.0, gain)
                ld_f   += take_val * EXIT_LOAD
            else:
                ltcg_f += max(0.0, gain)
            rebal_tax_tally += slice_tax
            net_f  += take_val - (take_val * EXIT_LOAD if holding < STCG_MONTHS else 0)
            rem_amt -= take_val
            leftover = lot.units - take_units
            if leftover > 1e-12:
                kept.append(Lot(leftover, lot.purchase_nav, lot.purchase_month))
        bk.lots = kept
        rebal_gains[_ck(ftype)] += stcg_f + ltcg_f
        taxes_paid              += _tax_for(ftype, stcg_f, ltcg_f)
        exit_loads_paid         += ld_f
        cash_raised             += net_f

    # ── Buys — portfolio self-funds its own tax (Bug #1 fix) ────────
    # We deduct taxes from cash_raised so the portfolio absorbs the cost.
    # net_cash_personal (the user's SWP income) is NEVER reduced here.
    net_cash_for_buys = max(0.0, cash_raised - taxes_paid)
    total_buy_need    = sum(amt for _, amt in buy_ops)
    if total_buy_need > 0 and net_cash_for_buys > 0.01:
        scale = min(1.0, net_cash_for_buys / total_buy_need)
        for fn, buy_amt in buy_ops:
            actual_buy = buy_amt * scale
            if actual_buy < 0.01:
                continue
            nav = fund_navs[fn]
            if fn not in fund_buckets:
                fund_buckets[fn] = new_folio_set()
                fund_navs[fn]    = nav
            fund_buckets[fn].invest(actual_buy, nav, month_idx)

    cost = RebalanceCost(year=fy, taxes_paid=taxes_paid, exit_loads_paid=exit_loads_paid)
    return cost, rebal_gains, swp_proceeds



class _ArchivedEngineMethods:
    """Shell so the Engine method below parses. Never instantiated."""

    def _estimate_transition_tax(
        self,
        prior_w: Dict[str, float],
        target_w: Dict[str, float],
        fund_buckets: Dict,
        fund_navs: Dict,
        fund_types: Dict,
        month_idx: int,
    ) -> float:
        """
        Lookahead: deep-copy the portfolio, simulate the COMPLETE transition
        from prior_w to target_w, and return total estimated tax in Rs lakhs.
        Used once per boundary to set the annual tax budget = total / spread_years.
        """
        import copy as _copy
        sim_buckets = _copy.deepcopy(fund_buckets)
        corpus = sum(
            bk.current_value(fund_navs[fn])
            for fn, bk in sim_buckets.items() if fn in fund_navs
        )
        if corpus < 1.0:
            return 0.0

        # Read tax rates from user config (same FY used by the caller)
        fy_est = self.state._fy_for_transition if hasattr(self.state, '_fy_for_transition') else 1
        # Use a rough FY from the month_idx
        fy_est = self._fy_year_for_month(month_idx)
        _ec = self.state.get_equity_tax_chunk(fy_est, "individual")
        _oc = self.state.get_other_tax_chunk(fy_est, "individual")
        eq_rate  = (_ec.tax_rate if _ec else config.fallback_equity_ltcg_rate) * (1 + CESS)
        oth_rate = (_oc.tax_rate if _oc else config.fallback_other_ltcg_rate) * (1 + CESS)
        _dc = self.state.get_debt_tax_chunk(fy_est, "individual")
        if _dc and _dc.slabs:
            debt_rate = max(s.rate for s in _dc.slabs) * (1 + CESS)
        else:
            debt_rate = config.fallback_debt_top_rate * (1 + CESS)

        total_tax = 0.0
        for fn, bk in sim_buckets.items():
            cw = prior_w.get(fn, 0.0)
            tw = target_w.get(fn, 0.0)
            if cw <= tw + 1e-4:
                continue   # not being sold in this transition
            sell_amt  = (cw - tw) * corpus
            nav       = fund_navs.get(fn, 1.0)
            ftype     = fund_types.get(fn, "debt")
            # HIFO: sort ascending by gain (lowest-gain lots first)
            sorted_lots = sorted(bk.lots, key=lambda lot: nav - lot.purchase_nav)
            rem = sell_amt
            for lot in sorted_lots:
                if rem <= 1e-6:
                    break
                take    = min(lot.units * nav, rem)
                gain    = max(0.0, (take / nav) * (nav - lot.purchase_nav))
                if ftype == "equity":
                    total_tax += gain * eq_rate
                elif ftype == "other":
                    total_tax += gain * oth_rate
                else:
                    total_tax += gain * debt_rate
                rem -= take
        return total_tax
