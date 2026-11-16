#!/usr/bin/env python3
"""
Confluence score for each pair on the week's lists: the Set & Forget checklist (Appendix B of the
set-and-forget-setups skill), scored with the skill's own analyzer code so the app shows the same
score the chat gives.

The 20 boxes and their points (a plain sum, so the percentage is the points; A+ is 100 or more):
  Weekly and Daily: in favor 10, at or rejected the AOI 10, touching the 50 EMA 5,
                    candle rejection 10, PSP rejection 10, pattern 10
  4H:               in favor 10, touching the 50 EMA 5, candle rejection 10, PSP rejection 10, pattern 10
  Entry (2H/1H):    round psychological level 5, shift of structure 10, engulfing or morning/evening star 10
Only what is happening now counts, a rejection needs a CLOSED candle, and a timeframe against the
trade adds only its EMA or candle rejection. The AOIs are the ones on the list, not new ones.
Patterns (head and shoulders, double tops and bottoms) are never ticked here: they need eyes on the chart.

  python3 tools/confluence.py [PAIR ...]      print the scores for the week's lists (or some pairs)
  python3 tools/confluence.py --save          also save them in data/feed.json ("conf"), where the app reads them
                                              (tools/check.py run and tools/week.py do this themselves)

The helpers below are copied from the skill's analyze.py (bodies of No Gap candles). This file
never places trades and works out no stops or targets.
"""
import json, math, os, statistics, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
# seconds from bar open until that bar is CLOSED (weekly FX bar closes Fri 5pm NY)
CLOSE_AFTER = {"W": 5 * 86400, "D": 86400, "4h": 14400, "2h": 7200, "1h": 3600, "30m": 1800}
LOOKBACK = {"W": 300, "D": 600, "4h": 1500, "2h": 400, "1h": 500, "30m": 500}
WEIGHTS = {
    "trend": {"W": 10, "D": 10, "4h": 10},
    "aoi": 10, "ema": 5, "candle": 10, "psp": 10, "rpl": 5, "pattern": 10,
    "sos": 10, "engulf": 10,
}
GRADES = [(100, "A+"), (90, "B+"), (80, "C+"), (70, "D+")]

# ---------------------------------------------------------------- helpers
def pip_size(sym):
    s = sym.upper()
    if "JPY" in s or "HUF" in s:
        return 0.01
    if "XAU" in s:
        return 0.1
    if "XAG" in s:
        return 0.01
    return 0.0001


def fmt_t(t):
    return datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d %H:%M NY")


def fmt_d(t):
    return datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d")


def ema(vals, n=50):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


class Series:
    """Closed bars with no-gap bodies. `forming` holds the live bar if any."""

    def __init__(self, d, tf, nogap=True, now=None):
        self.tf = tf
        t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
        fetched = d.get("fetched_at") or time.time()
        self.forming = None
        if t and t[-1] + CLOSE_AFTER[tf] > fetched:
            self.forming = dict(t=t[-1], o=o[-1], h=h[-1], l=l[-1], c=c[-1])
            t, o, h, l, c = t[:-1], o[:-1], h[:-1], l[:-1], c[:-1]
        n0 = max(0, len(t) - LOOKBACK[tf])
        self.t, self.c = t[n0:], c[n0:]
        self.h, self.l = h[n0:], l[n0:]
        if nogap:  # No Gap Candles: open = previous close
            oo = [o[n0]] + c[n0:-1] if len(c) > n0 else []
            self.o = oo
            self.h = [max(a, b) for a, b in zip(self.h, oo)]
            self.l = [min(a, b) for a, b in zip(self.l, oo)]
        else:
            self.o = o[n0:]
        self.bh = [max(a, b) for a, b in zip(self.o, self.c)]
        self.bl = [min(a, b) for a, b in zip(self.o, self.c)]
        self.n = len(self.c)
        self.ema = ema(c[:len(c)])[n0:] if self.n else []
        bodies = [abs(a - b) for a, b in zip(self.o, self.c)][-150:]
        self.med_body = statistics.median(bodies) if bodies else 0
        self.fetched = fetched

    @property
    def price(self):
        return self.forming["c"] if self.forming else (self.c[-1] if self.n else None)


def aggregate_2h(s1):
    """Build 2H bars from 1H, aligned to the FX session open (5pm NY)."""
    buckets = {}
    order = []
    for i, t in enumerate(s1["t"]):
        dtn = datetime.fromtimestamp(t, NY)
        hrs = (dtn.hour - 17) % 24
        start = t - (hrs % 2) * 3600 - dtn.minute * 60
        if start not in buckets:
            buckets[start] = [s1["o"][i], s1["h"][i], s1["l"][i], s1["c"][i]]
            order.append(start)
        else:
            b = buckets[start]
            b[1] = max(b[1], s1["h"][i]); b[2] = min(b[2], s1["l"][i]); b[3] = s1["c"][i]
    return {"t": order, "o": [buckets[k][0] for k in order], "h": [buckets[k][1] for k in order],
            "l": [buckets[k][2] for k in order], "c": [buckets[k][3] for k in order],
            "fetched_at": s1.get("fetched_at")}


def body_pivots(S, k=2):
    """Body-based swing points (elbows). Returns list of (idx, level, 'H'|'L')."""
    out = []
    for i in range(k, S.n - k):
        L, R = S.bh[i - k:i], S.bh[i + 1:i + k + 1]
        if S.bh[i] > max(L) and S.bh[i] >= max(R):
            out.append((i, S.bh[i], "H"))
        L, R = S.bl[i - k:i], S.bl[i + 1:i + k + 1]
        if S.bl[i] < min(L) and S.bl[i] <= min(R):
            out.append((i, S.bl[i], "L"))
    return out

# ---------------------------------------------------------------- structure
def structure(S, min_pb_bars=3, depth_mult=1.5):
    """
    Walk left->right on closed bars. Bull = HH+HL; bear = LH+LL. A break is a BODY CLOSE
    beyond the HL/LH. On a bull->bear break: LH = highest body of the leg (snake turn), LL
    trails down until a qualifying pullback. New HH needs a qualifying pullback (>=3
    candles, or a deep clean pullback of >=2 candles; 1-candle bounces are logs) before the next close above, then HL = lowest body
    of the leg that made the new HH. PSP = the level that was broken last (2nd snake turn).
    """
    n = S.n
    if n < 30:
        return None
    thr = depth_mult * S.med_body
    init = min(20, n // 4)
    i_hi = max(range(init), key=lambda i: S.bh[i])
    i_lo = min(range(init), key=lambda i: S.bl[i])
    if i_hi > i_lo:
        st = dict(trend="bull", ext=S.bh[i_hi], ext_i=i_hi, prot=S.bl[i_lo], prot_i=i_lo, psp=None, psp_i=None)
    else:
        st = dict(trend="bear", ext=S.bl[i_lo], ext_i=i_lo, prot=S.bh[i_hi], prot_i=i_hi, psp=None, psp_i=None)
    events = []
    for i in range(init, n):
        c = S.c[i]
        if st["trend"] == "bull":
            if c < st["prot"]:  # HL broken -> bearish shift (makes a LL)
                j = max(range(st["ext_i"], i + 1), key=lambda x: S.bh[x])
                events.append(dict(i=i, t=S.t[i], type="shift_bear", level=st["prot"], swing_i=j))
                st = dict(trend="bear", ext=S.bl[i], ext_i=i, prot=S.bh[j], prot_i=j,
                          psp=st["prot"], psp_i=st["prot_i"])
                continue
            if S.bh[i] > st["ext"]:
                # pullback starts AFTER the extreme bar (its own open would hide the base)
                k = min(range(st["ext_i"] + 1, i + 1), key=lambda x: S.bl[x])
                pb_bars = i - st["ext_i"] - 1
                depth = st["ext"] - S.bl[k]
                # 1-candle pullbacks are logs; deep pullbacks need >= 2 candles
                if pb_bars >= 1 and (pb_bars >= min_pb_bars or (pb_bars >= 2 and depth >= thr)) and k < i:
                    events.append(dict(i=i, t=S.t[i], type="HH", level=st["ext"], swing_i=k))
                    st.update(psp=st["ext"], psp_i=st["ext_i"], prot=S.bl[k], prot_i=k)
                st.update(ext=S.bh[i], ext_i=i)
        else:
            if c > st["prot"]:  # LH broken -> bullish shift (makes a HH)
                j = min(range(st["ext_i"], i + 1), key=lambda x: S.bl[x])
                events.append(dict(i=i, t=S.t[i], type="shift_bull", level=st["prot"], swing_i=j))
                st = dict(trend="bull", ext=S.bh[i], ext_i=i, prot=S.bl[j], prot_i=j,
                          psp=st["prot"], psp_i=st["prot_i"])
                continue
            if S.bl[i] < st["ext"]:
                k = max(range(st["ext_i"] + 1, i + 1), key=lambda x: S.bh[x])
                pb_bars = i - st["ext_i"] - 1
                depth = S.bh[k] - st["ext"]
                if pb_bars >= 1 and (pb_bars >= min_pb_bars or (pb_bars >= 2 and depth >= thr)) and k < i:
                    events.append(dict(i=i, t=S.t[i], type="LL", level=st["ext"], swing_i=k))
                    st.update(psp=st["ext"], psp_i=st["ext_i"], prot=S.bh[k], prot_i=k)
                st.update(ext=S.bl[i], ext_i=i)
    st["events"] = events
    if st["trend"] == "bull":
        st.update(HH=st["ext"], HH_t=S.t[st["ext_i"]], HL=st["prot"], HL_t=S.t[st["prot_i"]])
    else:
        st.update(LL=st["ext"], LL_t=S.t[st["ext_i"]], LH=st["prot"], LH_t=S.t[st["prot_i"]])
    # current retracement extreme since the structure extreme (potential HL / LH)
    rng = range(st["ext_i"], n)
    if st["trend"] == "bull":
        k = min(rng, key=lambda x: S.bl[x]); st["pullback_extreme"] = S.bl[k]
    else:
        k = max(rng, key=lambda x: S.bh[x]); st["pullback_extreme"] = S.bh[k]
    st["bars_since_extreme"] = n - 1 - st["ext_i"]
    gov_t = S.t[st["prot_i"]]
    st["governing_age_days"] = (S.fetched - gov_t) / 86400
    st["shifts_last_n"] = sum(1 for e in events if e["type"].startswith("shift") and e["i"] > n - 80)
    return st


def touched_zone(S, z, pip, bars=1, tol=3):
    """Did price (wick, incl. live bar) reach the zone in the last `bars` closed bars?"""
    hs = S.h[-bars:] + ([S.forming["h"]] if S.forming else [])
    ls = S.l[-bars:] + ([S.forming["l"]] if S.forming else [])
    return any(l <= z["top"] + tol * pip and h >= z["bottom"] - tol * pip for h, l in zip(hs, ls))


# ---------------------------------------------------------------- candles
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
    """Engulfing in direction d; returns number of prior BODIES engulfed (0 = none)."""
    if i < 1 or (S.c[i] - S.o[i]) * d <= 0:
        return 0
    cnt = real = 0
    for j in range(i - 1, max(-1, i - 8), -1):
        if S.bh[j] <= S.bh[i] + 1e-12 and S.bl[j] >= S.bl[i] - 1e-12:
            cnt += 1
            if not is_doji(S, j) and (S.c[j] - S.o[j]) * d < 0:
                real += 1  # a real opposite-colour body was engulfed
        else:
            break
    return cnt if real else 0  # dojis alone don't count; it must engulf a real body


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


def candle_rejection(S, d, zone=None, pip=0.0001, lookback=1):
    """Closed-candle rejection in direction d at the zone. Returns (name, rating) or None."""
    best = None
    for i in range(S.n - 1, max(S.n - 1 - lookback, 1), -1):
        if zone:
            # wick must reach the zone; body must NOT close beyond the far side
            if d > 0 and not (S.l[i] <= zone["top"] + 3 * pip and S.c[i] >= zone["bottom"] - 1 * pip):
                continue
            if d < 0 and not (S.h[i] >= zone["bottom"] - 3 * pip and S.c[i] <= zone["top"] + 1 * pip):
                continue
        found = None
        if pin(S, i, d) and pin(S, i - 1, d):
            found = ("multiple rejections", 9)
        elif star(S, i, d):
            found = ("morning star" if d > 0 else "evening star", 9)
        elif engulf(S, i, d):
            found = (f"{'bullish' if d > 0 else 'bearish'} engulfing ({engulf(S, i, d)} bodies)", 8)
        elif pin(S, i, d):
            found = ("hammer/pin bar" if d > 0 else "shooting star/pin bar", 8)
        if found:
            found = (found[0] + f" @ {fmt_t(S.t[i])}", found[1])
            if not best or found[1] > best[1]:
                best = found
    return best


def ema_touch(S, d, pip, tol=3):
    """Last closed candle touching the 50 EMA and closing on the trade side (rejection)."""
    i = S.n - 1
    e = S.ema[i]
    touch = S.l[i] - tol * pip <= e <= S.h[i] + tol * pip
    ok = touch and ((S.c[i] > e) if d > 0 else (S.c[i] < e))
    return ok, e


def level_rejected(S, lvl, d, pip, bars=2, tol=3):
    for i in range(S.n - 1, max(S.n - 1 - bars, 0), -1):
        if d > 0 and S.l[i] <= lvl + tol * pip and S.c[i] > lvl:
            return True
        if d < 0 and S.h[i] >= lvl - tol * pip and S.c[i] < lvl:
            return True
    return False


def rpls_in(z, pip, tol=5):
    step = 50 * pip
    lo, hi = z["bottom"] - tol * pip, z["top"] + tol * pip
    k = math.ceil(lo / step)
    out = []
    while k * step <= hi:
        out.append(round(k * step, 6)); k += 1
    return out


# ---------------------------------------------------------------- entry TFs
def entry_tf(S, d, zone, pip):
    st = structure(S)
    if not st:
        return None
    res = dict(tf=S.tf, trend=st["trend"])
    want = "bull" if d > 0 else "bear"
    if st["trend"] != want:
        lvl = st["LH"] if d > 0 else st["HL"]
        res.update(status="WAITING for SoS", sos_level=lvl,
                   note=f"needs a {S.tf} BODY CLOSE {'above LH' if d > 0 else 'below HL'} {lvl:.5f}")
        return res
    shifts = [e for e in st["events"] if e["type"] == ("shift_bull" if d > 0 else "shift_bear")]
    if not shifts:
        res.update(status="trend in direction but no shift found in lookback")
        return res
    e = shifts[-1]
    j = e["swing_i"]
    # extreme wick of the swing that created the SoS (lowest low / highest high of the leg)
    seg = range(max(0, j - 3), min(S.n, e["i"] + 1))
    wick = min(S.l[x] for x in seg) if d > 0 else max(S.h[x] for x in seg)
    at_zone = None
    if zone:
        if d > 0:
            at_zone = wick <= zone["top"] + 5 * pip and wick >= zone["bottom"] - 40 * pip
        else:
            at_zone = wick >= zone["bottom"] - 5 * pip and wick <= zone["top"] + 40 * pip
    bars_ago = S.n - 1 - e["i"]
    res.update(status="SoS CONFIRMED", sos_time=fmt_t(e["t"]), sos_level=e["level"], bars_ago=bars_ago,
               swing_wick=wick, sos_at_aoi=at_zone, potential_HL_LH=st["pullback_extreme"])
    return res


def grade(p):
    for th, g in GRADES:
        if p >= th:
            return g
    return "below D+ (no trade per checklist)"



# ---------------------------------------------------------------- the desk's bars
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(ROOT, "data", "bars")
WEEK = os.path.join(ROOT, "data", "week.json")
FEED = os.path.join(ROOT, "data", "feed.json")
BOXES = [  # (tf, key, name, pts) in the checklist's order
    ("W", "trend", "In favor", 10), ("W", "aoi", "At or rejected the AOI", 10), ("W", "ema", "Touching the 50 EMA", 5),
    ("W", "candle", "Candle rejection", 10), ("W", "psp", "PSP rejection", 10), ("W", "pattern", "Pattern", 10),
    ("D", "trend", "In favor", 10), ("D", "aoi", "At or rejected the AOI", 10), ("D", "ema", "Touching the 50 EMA", 5),
    ("D", "candle", "Candle rejection", 10), ("D", "psp", "PSP rejection", 10), ("D", "pattern", "Pattern", 10),
    ("4H", "trend", "In favor", 10), ("4H", "ema", "Touching the 50 EMA", 5), ("4H", "candle", "Candle rejection", 10),
    ("4H", "psp", "PSP rejection", 10), ("4H", "pattern", "Pattern", 10),
    ("Entry", "rpl", "Round number level", 5), ("Entry", "sos", "Shift of structure", 10),
    ("Entry", "engulf", "Engulfing or morning/evening star", 10),
]


def _weekend(t):
    return (datetime.fromtimestamp(t, NY) + timedelta(hours=7)).weekday() >= 5


def _week_bucket(t):
    """Sunday 5 PM New York that starts the week holding t."""
    sh = datetime.fromtimestamp(t, NY) + timedelta(hours=7)
    day = sh.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((day - timedelta(days=day.weekday()) - timedelta(hours=7)).timestamp())


def _read(fn):
    try:
        return json.load(open(fn))
    except Exception:                           # noqa: BLE001
        return None


def _cut(b, now):
    """Only candles that had opened by `now` (for replays)."""
    if not b or now is None:
        return b
    keep = [i for i, t in enumerate(b["t"]) if t <= now]
    out = {k: [b[k][i] for i in keep] for k in "tohlc"}
    out["fetched_at"] = min(b.get("fetched_at") or now, now)
    return out


def load_bars(pair, folder=None, now=None):
    """Candles per timeframe from data/bars. The weekly history is the rolled-up weekly file, with
    the longer weekly file (_1wx, read once a day) in front of it for the older weeks."""
    folder = folder or BARS
    f = lambda tf: _read(os.path.join(folder, f"OANDA_{pair}_{tf}.json"))
    raw = {}
    w, wx = f("1w"), f("1wx")
    if w and w.get("t"):
        if wx and wx.get("t"):
            first = w["t"][0]
            old = {}
            for j, t in enumerate(wx["t"]):
                k = _week_bucket(t + 12 * 3600)
                if k < first:
                    old[k] = tuple(wx[x][j] for x in "ohlc")
            ts = sorted(old)
            w = {"t": ts + w["t"], **{x: [old[t][i] for t in ts] + w[x] for i, x in enumerate("ohlc")},
                 "fetched_at": w.get("fetched_at")}
        raw["W"] = w
    for tf, name in (("D", "1d"), ("4h", "4h")):
        b = f(name)
        if b and b.get("t"):
            raw[tf] = b
    h = f("1h")
    if h and h.get("t"):
        keep = [i for i, t in enumerate(h["t"]) if not _weekend(t)]
        raw["1h"] = {**{k: [h[k][i] for i in keep] for k in "tohlc"}, "fetched_at": h.get("fetched_at")}
        raw["2h"] = aggregate_2h(raw["1h"])
    return {tf: _cut(b, now) for tf, b in raw.items()}


# ---------------------------------------------------------------- the score
STALE_DAYS = {"W": 365, "D": 180}


def recent_structure(S, tf):
    """The skill's exception for old structure (S24): when the point that governs the W (D) trend is
    over a year (half a year) old, read the trend from the recent candles instead."""
    st = structure(S)
    if not st or tf not in STALE_DAYS or st["governing_age_days"] <= STALE_DAYS[tf]:
        return st
    for n in (120, 60):
        if S.n <= n:
            continue
        sub = Series({"t": S.t[-n:], "o": S.o[-n:], "h": S.h[-n:], "l": S.l[-n:], "c": S.c[-n:],
                      "fetched_at": S.fetched}, S.tf, nogap=False)
        sub.ema = S.ema[-n:]
        s2 = structure(sub)
        if s2:
            s2["recent"] = n
            st = s2
            if s2["governing_age_days"] <= STALE_DAYS[tf]:
                break
    return st


def score(raw, pair, side, aois, now=None):
    """Score one list item (pair, side and its AOIs) against the checklist. Returns the boxes,
    the points, the grade and whether price is at one of the AOIs now."""
    pip = pip_size(pair)
    d = 1 if side == "buy" else -1
    want = "bull" if d > 0 else "bear"
    S = {tf: Series(r, tf) for tf, r in raw.items() if r and len(r.get("t") or []) > 1}
    if "W" not in S or "D" not in S or "4h" not in S or not aois:
        return None
    ST = {tf: recent_structure(S[tf], tf) for tf in ("W", "D", "4h")}
    price = next(S[tf].price for tf in ("1h", "4h", "D", "W") if tf in S)
    got, why = {}, {}

    def tick(tf, key, text):
        got[(tf, key)] = True
        why[(tf, key)] = text

    # the list's AOIs; one is "at" when price is in it now or a W/D candle (this one or the last closed one) reached it
    zones = []
    for a in aois:
        tfs = ["W", "D"] if a.get("tf") == "W+D" else [a.get("tf") or "D"]
        z = dict(bottom=a["lo"], top=a["hi"], tfs=tfs, tf=a.get("tf") or "D")
        z["inside"] = a["lo"] - 3 * pip <= price <= a["hi"] + 3 * pip
        z["at"] = z["inside"] or any(touched_zone(S[t], z, pip) for t in ("W", "D") if t in S)
        z["dist"] = 0 if z["at"] else round(min(abs(price - a["lo"]), abs(price - a["hi"])) / pip)
        zones.append(z)
    at = [z for z in zones if z["at"]]
    use = at or sorted(zones, key=lambda z: z["dist"])[:1]
    union = dict(bottom=min(z["bottom"] for z in use), top=max(z["top"] for z in use))
    at_tfs = {t for z in at for t in z["tfs"]}
    zone_txt = lambda: "-".join(f"{v:.{3 if pip == 0.01 else 5}f}" for v in (union["bottom"], union["top"]))
    psp_inside = lambda p: p is not None and union["bottom"] - 5 * pip <= p <= union["top"] + 5 * pip

    for tf, lab in (("W", "W"), ("D", "D"), ("4h", "4H")):
        st = ST.get(tf)
        if st and st["trend"] == want:
            tick(lab, "trend", f"{'Bullish' if d > 0 else 'Bearish'} structure"
                 + (f" (recent {st['recent']} candles; the older structure is stale)" if st.get("recent") else ""))
    for tf in ("W", "D"):
        st = ST.get(tf)
        if not st:
            continue
        with_trend = st["trend"] == want
        if with_trend and tf in at_tfs:
            tick(tf, "aoi", f"Price is in the AOI {zone_txt()}" if any(z["inside"] for z in at) else
                 f"{'This or last week' if tf == 'W' else 'Today or yesterday'}'s candle reached the AOI {zone_txt()}")
            if psp_inside(st["psp"]) and level_rejected(S[tf], st["psp"], d, pip):
                tick(tf, "psp", f"PSP {st['psp']:.5g} inside the AOI was rejected")
        cr = candle_rejection(S[tf], d, union, pip, lookback=1 if tf == "W" else 2) if at else None
        if cr:
            tick(tf, "candle", cr[0].split(" @ ")[0])
        ok, e = ema_touch(S[tf], d, pip)
        if ok:
            tick(tf, "ema", f"Last closed candle touched the 50 EMA ({e:.5g}) and closed on the trade side")
    if at:
        rpl = [r for r in rpls_in(union, pip) if any(level_rejected(S[tf], r, d, pip) for tf in ("D", "4h"))]
        if rpl:
            tick("Entry", "rpl", f"Round number {rpl[0]:g} inside the AOI was rejected")
    ok, e = ema_touch(S["4h"], d, pip)
    if ok:
        tick("4H", "ema", f"Last closed candle touched the 50 EMA ({e:.5g}) and closed on the trade side")
    cr = candle_rejection(S["4h"], d, union, pip, lookback=3) if at else None
    if cr:
        tick("4H", "candle", cr[0].split(" @ ")[0])
    st4 = ST.get("4h")
    if at and st4 and st4["trend"] == want and psp_inside(st4["psp"]) and level_rejected(S["4h"], st4["psp"], d, pip, bars=3):
        tick("4H", "psp", f"PSP {st4['psp']:.5g} was rejected")
    # entry: a shift of structure on 2H or 1H whose swing reached the AOI, still in effect
    sos = None
    if at:
        for tf in ("2h", "1h"):
            if tf in S:
                e = entry_tf(S[tf], d, union, pip)
                if e and e.get("status") == "SoS CONFIRMED" and e.get("sos_at_aoi"):
                    sos = e
                    break
    if sos:
        tick("Entry", "sos", f"{sos['tf'].upper()} body closed {'above' if d > 0 else 'below'} {sos['sos_level']:.5g}")
        for tf in ("4h", "2h", "1h"):
            if tf not in S:
                continue
            Sx, sig = S[tf], None
            for i in range(Sx.n - 1, max(Sx.n - 7, 0), -1):
                k = engulf(Sx, i, d)
                if k:
                    sig = f"{tf.upper()} {'bullish' if d > 0 else 'bearish'} engulfing"
                    break
                if star(Sx, i, d):
                    sig = f"{tf.upper()} {'morning' if d > 0 else 'evening'} star"
                    break
            if sig:
                tick("Entry", "engulf", sig)
                break
    boxes = [dict(tf=tf, name=name, pts=pts, ok=bool(got.get((tf, key))),
                  **({"why": why[(tf, key)]} if (tf, key) in why else {}))
             for tf, key, name, pts in BOXES]
    total = sum(b["pts"] for b in boxes if b["ok"])
    near = sorted(zones, key=lambda z: z["dist"])[0]
    where = "in" if any(z["inside"] for z in zones) else "reached" if at else "away"
    return dict(pair=pair, side=side, score=total, grade=grade(total).split(" (")[0], where=where,
                dist_pips=0 if at else near["dist"], price=price,
                trend={lab: (ST[tf] or {}).get("trend") for tf, lab in (("W", "W"), ("D", "D"), ("4h", "4H"))},
                boxes=boxes)


def week_items(week):
    """One entry per pair and side across the lists (final first, then yours, then Claude's)."""
    out = {}
    for k in ("final", "yours", "claude"):
        for x in week.get(k, []) or []:
            if x.get("pair") and x.get("side"):
                out.setdefault(f"{x['pair']} {x['side']}", x)
    return out


def score_week(week, folder=None, now=None):
    res = {}
    for key, x in week_items(week).items():
        try:
            r = score(load_bars(x["pair"], folder, now), x["pair"], x["side"], x.get("aois") or [], now)
        except Exception as e:                  # noqa: BLE001
            print(f"{key}: could not score ({str(e)[:120]})")
            r = None
        if r:
            res[key] = r
    return res


def update_feed(feed, week, folder=None, now=None):
    """Put the scores for the week's lists in feed["conf"]; the app shows them next to each pair."""
    t = now or time.time()
    feed["conf"] = {"week_of": week.get("week_of"),
                    "at": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "items": score_week(week, folder, now)}
    return feed["conf"]


def save_feed():
    """Score the week's lists and save them in data/feed.json."""
    feed, week = _read(FEED), _read(WEEK)
    if not feed or not week:
        return None
    conf = update_feed(feed, week)
    feed["updated"] = conf["at"]
    tmp = FEED + ".tmp"
    json.dump(feed, open(tmp, "w"), indent=1)
    os.replace(tmp, FEED)
    return conf


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if "--save" in sys.argv:
        save_feed()
    week = _read(WEEK) or {}
    want = {p.upper() for p in args}
    for key, r in score_week(week).items():
        if want and r["pair"] not in want:
            continue
        print(f"{key}: {r['score']}% {r['grade']}  {r['where']} {r['dist_pips']}p"
              f"  trend W/D/4H {r['trend']['W']}/{r['trend']['D']}/{r['trend']['4H']}")
        for b in r["boxes"]:
            if b["ok"]:
                print(f"   + {b['tf']:5} {b['name']} ({b['pts']}): {b.get('why', '')}")


if __name__ == "__main__":
    main()
