"""Generalised solver: derive the HUF ration rather than fitting it."""
import two_purse_model as M


def diagnose(req, oi, toi, exempt, nilband, huf_from=None):
    """Per-year headroom, cash demand, and the critical gain fraction."""
    rows=[]
    for t in range(30):
        Hp = max(0.0, exempt[t]-toi[t])
        Hh = nilband[t] if (huf_from is not None and t+1>=huf_from) else 0.0
        D  = req[t]-oi[t]
        fstar = float('inf') if D<=0 else (Hp+Hh)/D
        rows.append(dict(fy=t+1, Hp=Hp, Hh=Hh, D=D, fstar=fstar))
    return rows


def solve(returns, folios=3, threshold=0.05, turnover=0.0, switch_start=8,
          klo=0.6, khi=3.0, steps=49):
    """Sweep the ration constant; lexicographic min(tax), max(end corpus)."""
    best=None
    saved = M.RATION_K          # sweeping mutates a module global; a caller
    try:                        # running run() afterwards must not inherit
        best = _sweep(returns, folios, threshold, turnover, switch_start,
                      klo, khi, steps)
    finally:
        M.RATION_K = saved      # the last swept value. Restore it.
    return best


def _sweep(returns, folios, threshold, turnover, switch_start,
           klo, khi, steps):
    best=None
    for i in range(steps):
        k = klo + (khi-klo)*i/(steps-1)
        M.RATION_K = k
        try:
            p,h,(tp,th,end,nf,nh) = M.run(returns, folios=folios,
                                          threshold=threshold, turnover=turnover,
                                          switch_start=switch_start)
        except Exception:
            continue
        over = max(r['check'] for r in h)
        if over > 1e-6:                     # OVER-delivery is always a bug
            continue
        if min(r['end'] for r in h) < -1e-6 or min(r['end'] for r in p) < -1e-6:
            continue
        # Under-delivery is legitimate ONLY when both corpora are exhausted.
        # Delivery is sacrosanct, so it dominates the objective: a plan that
        # short-changes the user is worse than any tax bill. Ranking on tax
        # alone rewards exhaustion, because a dead corpus pays no tax.
        short = sum(max(0.0, -r['check']) for r in h)
        key = (round(short, 4), round(tp+th, 4), -round(end, 3))
        if best is None or key < best[0]:
            best = (key, k, tp+th, end, p, h, short)
    return best
