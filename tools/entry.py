#!/usr/bin/env python3
"""
Entry checks on the 1H candles for one pair on the week's final list.

Ported from the set-and-forget-setups skill (analyze.py): structure is read on candle BODIES of
No Gap candles (each candle opens at the previous close), a shift of structure is a closed body
beyond the last lower high (buy) or higher low (sell), and it counts only when the swing that
made it reached the AOI. The entry candle is a closed engulfing (it must eat at least one real
opposite body) or a morning / evening star, on or after the shift candle, at or near the AOI.

Nothing here places trades or works out stops, entries or targets. It only reads candles.
"""
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

NEAR_PIPS = 20          # an entry candle counts when its wick is within the AOI's height (at least this) of the AOI
GRAB_PIPS = 40          # a shift still counts after a liquidity grab this far beyond the AOI's far side
EDGE_PIPS = 5


def weekend(t):
    """True for a quote from Friday 5 PM to Sunday 5 PM New York, which OANDA charts do not show."""
    return (datetime.fromtimestamp(t, NY) + timedelta(hours=7)).weekday() >= 5


def trading_bars(b):
    """The candles without weekend quotes."""
    keep = [i for i, t in enumerate(b["t"]) if not weekend(t)]
    return {k: [b[k][i] for i in keep] for k in "tohlc"}


class Series:
    """Closed 1H candles as No Gap candles. `forming` holds the candle that is still open, if any."""

    def __init__(self, b, now):
        b = trading_bars(b)
        t, o, h, l, c = b["t"], b["o"], b["h"], b["l"], b["c"]
        n = len(t)
        while n and t[n - 1] + 3600 > now:      # drop candles that have not closed yet
            n -= 1
        self.forming = None
        if n < len(t) and t[n] <= now:
            self.forming = dict(t=t[n], o=o[n], h=h[n], l=l[n], c=c[n])
        self.t, self.c = list(t[:n]), list(c[:n])
        self.o = ([o[0]] + self.c[:-1]) if n else []
        self.h = [max(a, b) for a, b in zip(h[:n], self.o)]
        self.l = [min(a, b) for a, b in zip(l[:n], self.o)]
        self.bh = [max(a, b) for a, b in zip(self.o, self.c)]
        self.bl = [min(a, b) for a, b in zip(self.o, self.c)]
        self.n = n
        bodies = [abs(a - b) for a, b in zip(self.o, self.c)][-150:]
        self.med_body = statistics.median(bodies) if bodies else 0

    @property
    def price(self):
        return self.forming["c"] if self.forming else (self.c[-1] if self.n else None)


def structure(S, min_pb_bars=3, depth_mult=1.5):
    """Left-to-right walk of the closed bodies (same rules as the skill's analyze.py).
    Returns the current trend and the list of events (HH, LL, shift_bull, shift_bear)."""
    n = S.n
    if n < 30:
        return None
    thr = depth_mult * S.med_body
    init = min(20, n // 4)
    i_hi = max(range(init), key=lambda i: S.bh[i])
    i_lo = min(range(init), key=lambda i: S.bl[i])
    if i_hi > i_lo:
        st = dict(trend="bull", ext=S.bh[i_hi], ext_i=i_hi, prot=S.bl[i_lo], prot_i=i_lo)
    else:
        st = dict(trend="bear", ext=S.bl[i_lo], ext_i=i_lo, prot=S.bh[i_hi], prot_i=i_hi)
    events = []
    for i in range(init, n):
        c = S.c[i]
        if st["trend"] == "bull":
            if c < st["prot"]:                                   # higher low broken: bearish shift
                j = max(range(st["ext_i"], i + 1), key=lambda x: S.bh[x])
                events.append(dict(i=i, type="shift_bear", level=st["prot"], swing_i=j))
                st = dict(trend="bear", ext=S.bl[i], ext_i=i, prot=S.bh[j], prot_i=j)
                continue
            if S.bh[i] > st["ext"]:
                k = min(range(st["ext_i"], i + 1), key=lambda x: S.bl[x])
                pb = i - st["ext_i"] - 1
                if pb >= 1 and (pb >= min_pb_bars or st["ext"] - S.bl[k] >= thr) and k > st["ext_i"]:
                    events.append(dict(i=i, type="HH", level=st["ext"], swing_i=k))
                    st.update(prot=S.bl[k], prot_i=k)
                st.update(ext=S.bh[i], ext_i=i)
        else:
            if c > st["prot"]:                                   # lower high broken: bullish shift
                j = min(range(st["ext_i"], i + 1), key=lambda x: S.bl[x])
                events.append(dict(i=i, type="shift_bull", level=st["prot"], swing_i=j))
                st = dict(trend="bull", ext=S.bh[i], ext_i=i, prot=S.bl[j], prot_i=j)
                continue
            if S.bl[i] < st["ext"]:
                k = max(range(st["ext_i"], i + 1), key=lambda x: S.bh[x])
                pb = i - st["ext_i"] - 1
                if pb >= 1 and (pb >= min_pb_bars or S.bh[k] - st["ext"] >= thr) and k > st["ext_i"]:
                    events.append(dict(i=i, type="LL", level=st["ext"], swing_i=k))
                    st.update(prot=S.bh[k], prot_i=k)
                st.update(ext=S.bl[i], ext_i=i)
    st["events"] = events
    return st


def is_doji(S, i):
    rng = S.h[i] - S.l[i]
    return rng == 0 or abs(S.c[i] - S.o[i]) <= 0.1 * rng


def pin(S, i, d):
    rng = S.h[i] - S.l[i]
    if rng <= 0:
        return False
    body = abs(S.c[i] - S.o[i])
    wick = (S.bl[i] - S.l[i]) if d > 0 else (S.h[i] - S.bh[i])
    return wick >= 0.55 * rng and wick >= 2 * body


def engulf(S, i, d):
    """Number of prior bodies a candle engulfs in direction d (0 = not an engulfing; dojis alone don't count)."""
    if i < 1 or (S.c[i] - S.o[i]) * d <= 0:
        return 0
    cnt = real = 0
    for j in range(i - 1, max(-1, i - 8), -1):
        if S.bh[j] <= S.bh[i] + 1e-12 and S.bl[j] >= S.bl[i] - 1e-12:
            cnt += 1
            if not is_doji(S, j) and (S.c[j] - S.o[j]) * d < 0:
                real += 1
        else:
            break
    return cnt if real else 0


def star(S, i, d):
    if i < 2:
        return False
    a, b = i - 2, i - 1
    ba, bb, bc = abs(S.c[a] - S.o[a]), abs(S.c[b] - S.o[b]), abs(S.c[i] - S.o[i])
    if (S.c[a] - S.o[a]) * d >= 0 or (S.c[i] - S.o[i]) * d <= 0:
        return False
    small_mid = bb <= 0.5 * ba or pin(S, b, d)
    mid_a = (S.o[a] + S.c[a]) / 2
    return small_mid and bc >= 0.5 * ba and (S.c[i] - mid_a) * d > 0


def overlaps(lo, hi, z):
    return lo <= z["hi"] and hi >= z["lo"]


def assess(b, side, aois, pip, now, week_start, in_window=None):
    """Read where one final-list pair stands. Returns a dict:
       price, trend (1H, bull/bear), at_aoi (price at an AOI now), arrived (first 1H candle of the week that
       reached an AOI: its open time and the AOI), shift (the 1H shift at an AOI that is still in effect),
       entry (the first closed entry candle this week after that shift), early (a candle that would have been
       the entry but closed outside the entry window; in_window(close_ts) decides, and such a candle doesn't count)."""
    d = 1 if side == "buy" else -1
    S = Series(b, now)
    out = dict(price=S.price, trend=None, at_aoi=None, arrived=None, shift=None, entry=None, early=None)
    if not S.n or not aois:
        return out
    # arrival: the first candle this week whose range reached an AOI
    rows = [(S.t[i], S.l[i], S.h[i]) for i in range(S.n)]
    if S.forming:
        f = S.forming
        rows.append((f["t"], min(f["l"], S.c[-1]), max(f["h"], S.c[-1])))
    for t, lo, hi in rows:
        if t < week_start:
            continue
        z = next((z for z in aois if overlaps(lo, hi, z)), None)
        if z:
            out["arrived"] = dict(t=t, aoi=z)
            break
    p = out["price"]
    last = rows[-1]
    out["at_aoi"] = next((z for z in aois if z["lo"] - 10 * pip <= p <= z["hi"] + 10 * pip
                          or (last[0] >= week_start and overlaps(last[1], last[2], z))), None)
    st = structure(S)
    if not st:
        return out
    out["trend"] = st["trend"]
    want = "bull" if d > 0 else "bear"
    if st["trend"] != want:
        return out
    shifts = [e for e in st["events"] if e["type"] == "shift_" + want]
    if not shifts:
        return out
    e = shifts[-1]                                            # the shift that started the trend now in effect
    seg = range(max(0, e["swing_i"] - 3), e["i"] + 1)
    if d > 0:
        wick = min(S.l[x] for x in seg)
        z = next((z for z in aois if z["lo"] - GRAB_PIPS * pip <= wick <= z["hi"] + EDGE_PIPS * pip), None)
    else:
        wick = max(S.h[x] for x in seg)
        z = next((z for z in aois if z["lo"] - EDGE_PIPS * pip <= wick <= z["hi"] + GRAB_PIPS * pip), None)
    if not z:
        return out                                            # the shift did not happen at an AOI
    if S.t[e["i"]] < week_start and not out["arrived"]:
        return out                                            # an older shift, and price has not been back this week
    out["shift"] = dict(t=S.t[e["i"]], level=e["level"], aoi=z)
    for i in range(e["i"], S.n):
        if S.t[i] < week_start:
            continue
        kind = None
        k = engulf(S, i, d)
        if k:
            kind = f"{'bullish' if d > 0 else 'bearish'} engulfing"
        elif star(S, i, d):
            kind = "morning star" if d > 0 else "evening star"
        if not kind:
            continue
        reach = max(z["hi"] - z["lo"], NEAR_PIPS * pip)
        lo, hi = min(S.l[i], S.l[i - 1]), max(S.h[i], S.h[i - 1])
        near = (lo <= z["hi"] + reach and lo >= z["lo"] - GRAB_PIPS * pip) if d > 0 else \
               (hi >= z["lo"] - reach and hi <= z["hi"] + GRAB_PIPS * pip)
        if not near:
            continue
        if in_window and not in_window(S.t[i] + 3600):
            out["early"] = dict(t=S.t[i], kind=kind, aoi=z)
            continue
        out["entry"] = dict(t=S.t[i], kind=kind, aoi=z)
        break
    return out
