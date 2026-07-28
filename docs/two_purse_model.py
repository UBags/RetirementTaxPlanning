"""Standalone two-purse retirement model — corrected.

Both purses run the same two-ordering structure:
    cash    -> cheapest folio (lowest front-lot gain fraction), gain-capped
    harvest -> dearest folio (highest front-lot gain fraction), rebought
The personal purse inserts the transition between the two, taking priority
over the harvest for whatever headroom the cash draw left.
"""

import copy

EPS = 1e-12
LOAD = 0.01
CESS = 0.04          # health & education cess; engine applies it, so we must
SPLIT_GRID = 20      # resolution of the personal/HUF escalation search          # exit_load_fraction, on lots sold inside 12 months


class Folio:
    __slots__ = ("lots",)

    def __init__(self):
        self.lots = []                       # [units, basis_nav, buy_year]

    def invest(self, amount, nav, year):
        if amount > EPS:
            self.lots.append([amount / nav, nav, year])

    def value(self, nav):
        return sum(l[0] for l in self.lots) * nav

    def units(self):
        return sum(l[0] for l in self.lots)

    def front_frac(self, nav):
        for u, b, _ in self.lots:
            if u > EPS:
                return (nav - b) / nav
        return float("inf")

    def _tidy(self):
        self.lots = [l for l in self.lots if l[0] > EPS]

    def sell(self, nav, year, amount=None, gain_cap=None, gain_target=None):
        """FIFO sell. Stops at whichever of amount / gain_cap / gain_target binds."""
        pr = gn = ld = 0.0
        for lot in self.lots:
            if lot[0] <= EPS:
                continue
            if amount is not None and amount - pr <= EPS:
                break
            if gain_cap is not None and gain_cap - gn <= EPS:
                break
            if gain_target is not None and gain_target - gn <= EPS:
                break
            frac = (nav - lot[1]) / nav
            take = lot[0] * nav
            if amount is not None:
                take = min(take, amount - pr)
            if frac > EPS:
                room = None
                if gain_cap is not None:
                    room = gain_cap - gn
                if gain_target is not None:
                    room = gain_target - gn if room is None else min(room, gain_target - gn)
                if room is not None:
                    take = min(take, room / frac)
            elif gain_target is not None:
                take = 0.0                   # no gain here; useless to harvest
            if take <= EPS:
                continue
            pr += take
            gn += take * frac
            if lot[2] == year:
                ld += take * LOAD
            lot[0] -= take / nav
        self._tidy()
        return pr, gn, ld


class FolioSet:
    def __init__(self, threshold, max_cohorts):
        self.threshold, self.max_cohorts = threshold, max_cohorts
        self.folios = [Folio()]

    def value(self, nav):
        return sum(f.value(nav) for f in self.folios)

    def _live(self):
        return [f for f in self.folios if f.units() > EPS]

    def cheapest(self, nav):
        live = self._live()
        return min(live, key=lambda f: f.front_frac(nav)) if live else self.folios[0]

    def dearest(self, nav):
        live = self._live()
        return max(live, key=lambda f: f.front_frac(nav)) if live else self.folios[0]

    def invest(self, amount, nav, year):
        if amount <= EPS:
            return
        newest = self.folios[-1]
        if (self.threshold > 0.0 and len(self.folios) < self.max_cohorts
                and newest.units() > EPS
                and newest.front_frac(nav) > self.threshold):
            self.folios.append(Folio())
            newest = self.folios[-1]
        newest.invest(amount, nav, year)

    def cash_capacity(self, nav, year, gain_cap):
        """Most cash raisable with total realised gain <= gain_cap.

        Must be measured across the spill, not as band/front_frac of the
        cheapest folio: once a draw crosses into a dearer folio the marginal
        gain per rupee changes.
        """
        clone = copy.deepcopy(self)
        pr, _, ld = clone.draw_cash(float("inf"), nav, year, gain_cap=gain_cap)
        return pr - ld

    def draw_cash(self, amount, nav, year, gain_cap=None):
        """Cheapest folio first, spilling onward. Stops early on gain_cap."""
        pr = gn = ld = 0.0
        for _ in range(len(self.folios) + 1):
            if amount - pr <= EPS:
                break
            if gain_cap is not None and gain_cap - gn <= EPS:
                break
            f = self.cheapest(nav)
            if f.units() <= EPS:
                break
            cap = None if gain_cap is None else gain_cap - gn
            p, g, l = f.sell(nav, year, amount=amount - pr, gain_cap=cap)
            if p <= EPS:
                break
            pr += p; gn += g; ld += l
        return pr, gn, ld

    def harvest(self, gain_target, nav, year, amount_cap=None):
        """Dearest folio first, spilling onward. Sell to realise gain_target."""
        pr = gn = ld = 0.0
        for _ in range(len(self.folios) + 1):
            if gain_target - gn <= EPS:
                break
            if amount_cap is not None and amount_cap - pr <= EPS:
                break
            f = self.dearest(nav)
            if f.units() <= EPS or f.front_frac(nav) <= EPS:
                break
            amt = None if amount_cap is None else amount_cap - pr
            p, g, l = f.sell(nav, year, amount=amt, gain_target=gain_target - gn)
            if p <= EPS:
                break
            pr += p; gn += g; ld += l
        return pr, gn, ld


# ── parameters ────────────────────────────────────────────────────────────
REQ     = [35.0]*5 + [37.5]*5 + [42.5]*5 + [45.0]*5 + [47.5]*5 + [50.0]*5
EXEMPT  = [12.0]*5 + [14.0]*5 + [16.0]*5 + [18.0]*5 + [20.0]*5 + [20.0]*5
NILBAND = [ 4.0]*5 + [ 5.0]*5 + [ 6.0]*5 + [ 7.0]*5 + [ 8.0]*5 + [ 9.0]*5
PERSONAL_CORPUS, HUF_WINDFALL, HUF_YEAR, HUF_RETURN = 350.0, 190.0, 5, 0.07
RATION_K = 1.195


def _oi():
    oi, out = 15.80, []
    for k in range(30):
        out.append(oi)
        if k < 29:
            oi += 0.50 + (0.15 - 0.50) * k / 28.0
    return out


OI = _oi()
TOI = [x * 5.95 / 15.80 for x in OI]


SLABS = [(4.0,0.00),(8.0,0.05),(12.0,0.10),(16.0,0.15),
         (20.0,0.20),(24.0,0.25),(float("inf"),0.30)]


def personal_tax(income, exempt):
    """New regime with s.87A. Marginal relief caps tax at the excess over the
    rebate threshold, so the marginal rate just above `exempt` is ~100%. That
    is why a shortfall is always pushed to the HUF's 5% band first."""
    if income <= exempt:
        return 0.0
    t, lo = 0.0, 0.0
    for hi, rate in SLABS:
        if income > lo:
            t += (min(income, hi) - lo) * rate
        lo = hi
    return min(t, income - exempt) * (1.0 + CESS)


def huf_tax(gain, band):
    t = 0.0
    if gain > band:
        t += 0.05 * (min(gain, 2*band) - band)
    if gain > 2*band:
        t += 0.20 * (min(gain, 4*band) - 2*band)
    if gain > 4*band:
        t += 0.30 * (gain - 4*band)
    return t * (1.0 + CESS)


def _gross_draw(fs, nav, year, want_net, taxfn, gain_before):
    """Draw until `want_net` arrives net of exit load and incremental tax."""
    gross = gain = load = 0.0
    tax0 = taxfn(gain_before)
    tax = tax0
    for _ in range(80):
        gap = want_net - (gross - load - (tax - tax0))
        if gap <= 1e-10:
            break
        pr, g, l = fs.draw_cash(gap, nav, year)
        if pr <= EPS:
            break
        gross += pr; gain += g; load += l
        tax = taxfn(gain_before + gain)
    return gross, gain, load, tax - tax0


def run(returns, folios=3, threshold=0.50, turnover=0.0, switch_start=4,
        huf_folios=True, windows=None):
    """`windows` = [(start_fy, boundary_fy, fraction), ...]. Several may be
    live at once; their headroom is SHARED and can be spent only once, so it
    is allocated nearest-deadline-first rather than offered to each
    (METHODOLOGY 5.3). `turnover`/`switch_start` are the single-window form."""
    if windows is None:
        windows = [(switch_start, None, turnover)] if turnover > 0.0 else []
    wins = [dict(start=a, end=(b if b is not None else 10**6), frac=f,
                 still=0.0, armed=False) for (a, b, f) in windows]
    thr_p = threshold if folios > 1 else 0.0
    thr_h = threshold if (folios > 1 and huf_folios) else 0.0
    p = FolioSet(thr_p, folios)
    p.folios[0].invest(PERSONAL_CORPUS, 1.0, 0)
    h = FolioSet(thr_h, folios)
    nav_p = nav_h = 1.0
    prows, hrows = [], []
    tp_tot = th_tot = 0.0

    for fy in range(1, 31):
        i = fy - 1
        p_start = p.value(nav_p)
        nav_p *= (1.0 + returns[i])
        p_pre = p.value(nav_p)

        h_start = h.value(nav_h)
        if fy == HUF_YEAR:
            h.folios[0].invest(HUF_WINDFALL, nav_h, fy)   # no growth that year
        elif fy > HUF_YEAR:
            nav_h *= (1.0 + HUF_RETURN)
        h_pre = h.value(nav_h)

        band, headroom, need = NILBAND[i], EXEMPT[i] - TOI[i], REQ[i] - OI[i]

        # what the HUF offers: the ration, capped by its tax-free cash capacity
        if h_pre > EPS:
            free_h = h.cash_capacity(nav_h, fy, band)
            offer = min(RATION_K * h_pre / (31 - fy), free_h)
        else:
            offer = 0.0

        # ── personal cash: cheapest folio, hard-capped at its headroom ──
        wd_p, g_cash, ld_p = p.draw_cash(max(0.0, need - offer), nav_p, fy,
                                         gain_cap=headroom)
        net_p_cash = wd_p - ld_p

        # ── HUF cash: up to its TAX-FREE capacity only ──────────────────
        cash_h = max(0.0, need - net_p_cash)
        gross_h = gain_h = ld_h = tax_h = 0.0
        if h_pre > EPS and cash_h > EPS:
            free_h = h.cash_capacity(nav_h, fy, band)
            gross_h, gain_h, ld_h = h.draw_cash(min(cash_h, free_h), nav_h, fy,
                                                gain_cap=band)

        # ── METHODOLOGY 8 step 4: both free capacities are spent, so the
        #    residual must be taxed somewhere. Personal above E costs ~100%
        #    under marginal relief and then drops to the slab rate, so its
        #    marginal cost is NON-CONVEX and greedy allocation is unsafe.
        #    Search the split rather than assuming the HUF always wins.
        tax_p = 0.0
        short = need - (net_p_cash + gross_h - ld_h)
        if short > 1e-9:
            ptax = lambda G: personal_tax(TOI[i] + G, EXEMPT[i])
            htax = lambda G: huf_tax(G, band)
            best = None
            for j in range(SPLIT_GRID + 1):
                phi = j / SPLIT_GRID
                pc, hc = copy.deepcopy(p), copy.deepcopy(h)
                gp, gnp, lp, txp = _gross_draw(pc, nav_p, fy, phi*short,
                                               ptax, g_cash)
                gh, gnh, lh, txh = _gross_draw(hc, nav_h, fy, (1-phi)*short,
                                               htax, gain_h)
                got = (gp - lp - txp) + (gh - lh - txh)
                key = (round(short - got, 6), round(txp + txh, 6))
                if best is None or key < best[0]:
                    best = (key, phi)
            phi = best[1]
            gp, gnp, lp, txp = _gross_draw(p, nav_p, fy, phi*short, ptax, g_cash)
            gh, gnh, lh, txh = _gross_draw(h, nav_h, fy, (1-phi)*short,
                                           htax, gain_h)
            wd_p += gp; g_cash += gnp; ld_p += lp; tax_p = txp
            gross_h += gh; gain_h += gnh; ld_h += lh
            net_p_cash = wd_p - ld_p - tax_p

        # ── HUF harvest: fill the rest of the nil band, rebuy ───────────
        hv_h = 0.0
        if h_pre > EPS and band - gain_h > EPS:
            hv_h, g3, l3 = h.harvest(band - gain_h, nav_h, fy)
            gain_h += g3; ld_h += l3
            hv_h = max(0.0, hv_h - l3)
            h.invest(hv_h, nav_h, fy)
        tax_h = huf_tax(gain_h, band)

        # ── personal: transition first, then harvest ────────────────────
        for w in wins:
            if fy == w['start'] and not w['armed']:
                w['still'], w['armed'] = w['frac'] * p_pre, True
        left = max(0.0, headroom - g_cash)
        sw = swg = 0.0
        for w in sorted(wins, key=lambda x: x['end']):
            if left <= EPS:
                break
            if not w['armed'] or w['still'] <= EPS:
                continue
            s_, g_, _ = p.harvest(left, nav_p, fy, amount_cap=w['still'])
            w['still'] -= s_; left -= g_; sw += s_; swg += g_
        hv_p = hvg = 0.0
        if left > EPS:
            hv_p, hvg, _ = p.harvest(left, nav_p, fy)
        p.invest(sw, nav_p, fy)
        p.invest(hv_p, nav_p, fy)

        tp_tot += tax_p
        th_tot += tax_h
        net_h = gross_h - ld_h - tax_h
        prows.append(dict(fy=fy, oi=OI[i], toi=TOI[i], start=p_start, pre=p_pre,
                          sw=sw, swg=swg, still=sum(w['still'] for w in wins),
                          wd=wd_p + hv_p, gain=g_cash + swg + hvg, tax=tax_p,
                          reinv=hv_p, end=p.value(nav_p)))
        hrows.append(dict(fy=fy, start=h_start, pre=h_pre, wd=gross_h + hv_h,
                          gain=gain_h, tax=tax_h, reinv=hv_h, end=h.value(nav_h),
                          net=net_p_cash + net_h, oi=OI[i], req=REQ[i],
                          check=net_p_cash + net_h + OI[i] - REQ[i]))
    return prows, hrows, (tp_tot, th_tot, prows[-1]["end"] + hrows[-1]["end"],
                          len(p.folios), len(h.folios))
