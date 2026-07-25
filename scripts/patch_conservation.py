#!/usr/bin/env python3
"""
Phase 1.3 fix — personal tax and the HUF tax-saving transfer must actually
leave the personal portfolio.

THE BUG
-------
Every April the engine rebuilds the aggregate buckets from the per-fund
buckets (engine.py ~1633):

    p_debt_agg = FIFOBucket(); p_debt_agg.invest(_agg_sync_d, p_nav_d, m)

The per-fund buckets are the source of truth; the aggregates are derived.
But the April tax event debits *only* the aggregates, so the very next
re-sync overwrites the debit. Two flows are silently discarded:

  * personal income tax         (Rs 84.18L over 30y, scenario 0)
  * the HUF tax-saving transfer (Rs 66.06L transferred -> Rs 296.07L of
                                 phantom HUF corpus at FY30 after compounding)

Verified: setting _p_tax_payable = 0.0 (paying NO tax for 30 years) leaves
FY10 personal corpus unchanged at 359.88333 to six decimal places. The
Personal_Monthly_FundDetail.csv exports confirm it independently -- April
months show an unexplained outflow of +/-0.03 (rounding) against an expected
Rs 2-6 lakh.

THE FIX
-------
1. _redeem_pro_rata() redeems an amount across the per-fund buckets in
   proportion to their value -- mirroring the logic the HUF-shortfall path
   already uses correctly.
2. The April block computes available corpus FROM the per-fund buckets, then
   redeems both the tax payment and the HUF transfer from them.
3. Gains realised by those redemptions accumulate into the FY pool, so they
   are taxed the following FY like any other realised gain.

Point 3 is a modelling choice, not a bug fix: selling units to pay tax does
realise capital gains in reality. Set ACCRUE_REDEMPTION_GAINS = False to fix
conservation only and leave tax treatment unchanged. It converges -- no
runaway feedback.

Safe to run twice. Preserves existing line endings.
"""
import shutil
import sys
from pathlib import Path

TARGET = Path("engine.py")

ACCRUE_REDEMPTION_GAINS = True

HELPER = '''
def _redeem_pro_rata(buckets: Dict[str, "FIFOBucket"],
                     navs: Dict[str, float],
                     amount: float) -> Tuple[float, float, Dict[str, float]]:
    """Redeem *amount* across per-fund buckets in proportion to their value.

    Returns (principal, gain, gain_by_fund_name).

    Used for cash that leaves the portfolio without being an SWP withdrawal:
    the April income-tax payment and the HUF tax-saving transfer. Both were
    previously applied to the aggregate buckets only, which the annual
    re-sync then overwrote -- so the money never actually left.
    """
    if amount <= 1e-9 or not buckets:
        return 0.0, 0.0, {}

    total = sum(bk.current_value(navs.get(fn, 1.0)) for fn, bk in buckets.items())
    if total <= 1e-9:
        return 0.0, 0.0, {}

    amount = min(amount, total)
    principal = gain = 0.0
    gains_by_fund: Dict[str, float] = {}

    for fn, bk in list(buckets.items()):
        val = bk.current_value(navs.get(fn, 1.0))
        if val <= 1e-9:
            continue
        nav = navs.get(fn, 1.0)
        pr, gn = bk.redeem(amount * val / total, nav)
        principal += pr
        gain += gn
        if gn:
            gains_by_fund[fn] = gains_by_fund.get(fn, 0.0) + gn

    return principal, gain, gains_by_fund

'''

OLD_BLOCK = '''                    # ── Personal tax: bounded by personal corpus ──────────────
                    _p_corpus_now = (p_debt_agg.current_value(p_nav_d)
                                     + p_equity_agg.current_value(p_nav_e)
                                     + p_other_agg.current_value(p_nav_o))
                    _p_tax_payable  = min(raw_ind_tax, max(0.0, _p_corpus_now))
                    _p_tax_shortfall = max(0.0, raw_ind_tax - _p_tax_payable)'''

NEW_BLOCK = '''                    # ── Personal tax: bounded by personal corpus ──────────────
                    # Value from the PER-FUND buckets, which are the source of
                    # truth. The aggregates are rebuilt from them a few lines
                    # below, so anything debited only from the aggregates is
                    # discarded (this was the bug).
                    _p_corpus_now = sum(
                        bk.current_value(p_fund_navs.get(fn, 1.0))
                        for fn, bk in p_fund_buckets.items())
                    _p_tax_payable  = min(raw_ind_tax, max(0.0, _p_corpus_now))
                    _p_tax_shortfall = max(0.0, raw_ind_tax - _p_tax_payable)

                    # Debit the tax from the per-fund buckets.
                    if _p_tax_payable > 1e-9:
                        _tx_pr, _tx_gn, _tx_gbf = _redeem_pro_rata(
                            p_fund_buckets, p_fund_navs, _p_tax_payable)
                        if ACCRUE_REDEMPTION_GAINS and _tx_gn > 1e-12:
                            _a_tx = acc_get(p_acc, fy)
                            for _fn, _g in _tx_gbf.items():
                                _ft = fund_types_map.get(_fn, "debt")
                                _a_tx["gain_e" if _ft == "equity"
                                      else "gain_o" if _ft == "other"
                                      else "gain_d"] += _g'''

OLD_XFER = '''                    _p_corpus_after_tax = max(0.0, _p_corpus_now - _p_tax_payable)
                    huf_xfer_this = min(huf_xfer_this, _p_corpus_after_tax)'''

NEW_XFER = '''                    _p_corpus_after_tax = sum(
                        bk.current_value(p_fund_navs.get(fn, 1.0))
                        for fn, bk in p_fund_buckets.items())
                    huf_xfer_this = min(huf_xfer_this, max(0.0, _p_corpus_after_tax))

                    # Debit the transfer from the per-fund buckets. Without
                    # this the amount was credited to the HUF and never
                    # removed from Personal -- money created from nothing.
                    if huf_xfer_this > 1e-9:
                        _xf_pr, _xf_gn, _xf_gbf = _redeem_pro_rata(
                            p_fund_buckets, p_fund_navs, huf_xfer_this)
                        if ACCRUE_REDEMPTION_GAINS and _xf_gn > 1e-12:
                            _a_xf = acc_get(p_acc, fy)
                            for _fn, _g in _xf_gbf.items():
                                _ft = fund_types_map.get(_fn, "debt")
                                _a_xf["gain_e" if _ft == "equity"
                                      else "gain_o" if _ft == "other"
                                      else "gain_d"] += _g'''


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run from the project root.")
        return 1

    raw = TARGET.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")

    if "_redeem_pro_rata" in text:
        print("Already patched — nothing to do.")
        return 0

    anchor = "# ─── Glide-path rebalancing ─"
    if anchor not in text:
        print("ERROR: could not find the glide-path section marker.")
        return 1
    flag = f"ACCRUE_REDEMPTION_GAINS = {ACCRUE_REDEMPTION_GAINS}\n"
    text = text.replace(anchor, flag + HELPER + "\n" + anchor, 1)
    print("  - inserted _redeem_pro_rata() helper")

    if OLD_BLOCK not in text:
        print("ERROR: April tax block does not match the expected text.")
        return 1
    text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    print("  - personal tax now debited from per-fund buckets")

    if OLD_XFER not in text:
        print("ERROR: HUF transfer block does not match the expected text.")
        return 1
    text = text.replace(OLD_XFER, NEW_XFER, 1)
    print("  - HUF transfer now debited from per-fund buckets")

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes(text.replace("\n", newline).encode("utf-8")
                       if newline != "\n" else text.encode("utf-8"))
    print(f"\nPatched {TARGET} (backup: {TARGET}.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
