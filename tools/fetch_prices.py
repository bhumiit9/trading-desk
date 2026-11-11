#!/usr/bin/env python3
"""
Fetch the latest hourly candles for every pair in data/feed.json and merge them into
data/bars/OANDA_<PAIR>_1h.json. Runs inside GitHub Actions before tools/check.py.

Source: Twelve Data (free plan: 8 requests a minute, 800 a day). The API key comes from the
repository secret TWELVEDATA_KEY. Its prices can differ from OANDA's by a pip or two.
Ten pairs take two requests a minute apart, so a run lasts a little over a minute.

The hourly candles are also rolled up into 4H, daily and weekly candles
(data/bars/OANDA_<PAIR>_4h.json, _1d.json, _1w.json) for the chat inside the app. The roll-up
uses the New York 5 PM day boundary, the same one OANDA charts use. The first run for a pair
asks for a long hourly history once, so those files start with months of candles.
"""
import calendar, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(ROOT, "data", "bars")
BATCH = 5            # symbols per request; two requests a minute apart stay under the free limit
NY = ZoneInfo("America/New_York")
KEEP = {"4h": 200, "1d": 220, "1w": 80}      # candles kept in each rolled-up file
HISTORY = 5000       # hourly candles asked for once, when a pair has no rolled-up files yet


def bucket(t, tf):
    """Start (UTC seconds) of the 4H, daily or weekly candle that the hourly candle at t belongs to.
    None for weekend quotes (Friday 5 PM to Sunday 5 PM New York), which OANDA charts do not show."""
    sh = datetime.fromtimestamp(t, NY) + timedelta(hours=7)        # 5 PM New York becomes midnight
    day = sh.replace(hour=0, minute=0, second=0, microsecond=0)
    if day.weekday() >= 5:
        return None
    if tf == "4h":
        start = day + timedelta(hours=4 * (sh.hour // 4))
    elif tf == "1d":
        start = day
    else:
        start = day - timedelta(days=day.weekday())
    return int((start - timedelta(hours=7)).timestamp())


def roll_up(symbol, hourly, now):
    """Merge the hourly candles into the 4H, daily and weekly files. Older stored candles are kept."""
    first = min(hourly)
    for tf, keep in KEEP.items():
        fresh = {}
        for t in sorted(hourly):
            b = bucket(t, tf)
            if b is None or b < first:          # weekend quote, or a candle that started before our hourly history did
                continue
            o, h, l, c = hourly[t]
            if b in fresh:
                f = fresh[b]
                fresh[b] = (f[0], max(f[1], h), min(f[2], l), c)
            else:
                fresh[b] = (o, h, l, c)
        fn = os.path.join(BARS, f"{symbol.replace(':', '_')}_{tf}.json")
        m = {}
        if os.path.exists(fn):
            try:
                b = json.load(open(fn))
                m = {t: (b["o"][j], b["h"][j], b["l"][j], b["c"][j]) for j, t in enumerate(b["t"])}
            except Exception:                   # noqa: BLE001
                m = {}
        m.update(fresh)
        ts = sorted(m)[-keep:]
        out = {"symbol": symbol, "tf": tf, "fetched_at": now, "upto": max(hourly) + 3600,   # upto: end of the newest hourly candle used
               "t": ts, "o": [m[t][0] for t in ts], "h": [m[t][1] for t in ts], "l": [m[t][2] for t in ts], "c": [m[t][3] for t in ts]}
        open(fn, "w").write(json.dumps(out, separators=(",", ":")))


def ready(flag):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        open(out, "a").write(f"ready={'true' if flag else 'false'}\n")


def fetch(symbols, key, size=48):
    q = urllib.parse.urlencode({"symbol": ",".join(symbols), "interval": "1h", "outputsize": size,
                                "timezone": "UTC", "order": "desc", "apikey": key})
    req = urllib.request.Request("https://api.twelvedata.com/time_series?" + q, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    if len(symbols) == 1 or "values" in d or "code" in d:
        d = {symbols[0]: d} if len(symbols) == 1 else {s: d for s in symbols}
    return d


def main():
    key = os.environ.get("TWELVEDATA_KEY", "").strip()
    if not key:
        print("The price key (repository secret TWELVEDATA_KEY) is not set, so prices were not read and nothing was checked.")
        ready(False)
        return
    feed = json.load(open(os.path.join(ROOT, "data", "feed.json")))
    docs = feed.get("zones", [])
    os.makedirs(BARS, exist_ok=True)
    now, ok, bad = int(time.time()), 0, []
    for i in range(0, len(docs), BATCH):
        part = docs[i:i + BATCH]
        if i:
            time.sleep(62)
        syms = [f"{d['pair'][:3]}/{d['pair'][3:]}" for d in part]
        new = any(not os.path.exists(os.path.join(BARS, f"{d['symbol'].replace(':', '_')}_1d.json")) for d in part)
        try:
            res = fetch(syms, key, HISTORY if new else 48)
        except Exception as e:                      # noqa: BLE001
            bad += [f"{d['pair']}: {str(e)[:120]}" for d in part]
            continue
        for d, s in zip(part, syms):
            r = res.get(s) or {}
            vals = r.get("values")
            if not vals:
                bad.append(f"{d['pair']}: {str(r.get('message') or r.get('status') or 'no data')[:160]}")
                continue
            rows = {}
            for v in vals:
                try:
                    t = calendar.timegm(time.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S"))
                    rows[t - t % 3600] = tuple(float(v[k]) for k in ("open", "high", "low", "close"))
                except Exception:                   # noqa: BLE001
                    continue
            if not rows:
                bad.append(f"{d['pair']}: unreadable rows")
                continue
            fn = os.path.join(BARS, f"{d['symbol'].replace(':', '_')}_1h.json")
            m = {}
            if os.path.exists(fn):
                try:
                    b = json.load(open(fn))
                    m = {t: (b["o"][j], b["h"][j], b["l"][j], b["c"][j]) for j, t in enumerate(b["t"])}
                except Exception:                   # noqa: BLE001
                    m = {}
            m.update(rows)
            try:
                roll_up(d["symbol"], m, now)
            except Exception as e:              # noqa: BLE001
                print(f"{d['pair']}: could not build the 4H, daily and weekly candles ({str(e)[:120]})")
            ts = sorted(m)[-240:]
            out = {"symbol": d["symbol"], "tf": "1h", "fetched_at": now, "source": "twelvedata",
                   "t": ts, "o": [m[t][0] for t in ts], "h": [m[t][1] for t in ts], "l": [m[t][2] for t in ts], "c": [m[t][3] for t in ts]}
            open(fn, "w").write(json.dumps(out, separators=(",", ":")))
            last = max(rows)
            print(f"{d['pair']}: {len(rows)} bars, newest {time.strftime('%d %b %H:%M UTC', time.gmtime(last))} close {rows[last][3]}")
            ok += 1
    for b in bad:
        print("FAILED", b)
    if ok == 0:
        ready(False)
        sys.exit("no pair could be read; nothing was checked")
    ready(True)


if __name__ == "__main__":
    main()
