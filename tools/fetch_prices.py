#!/usr/bin/env python3
"""
Fetch the latest hourly candles for every pair in data/feed.json and merge them into
data/bars/OANDA_<PAIR>_1h.json. Runs inside GitHub Actions before tools/check.py.

Source: Yahoo Finance chart data (no key). Its prices can differ from OANDA's by a pip or two.
"""
import json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(ROOT, "data", "bars")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def yahoo(pair):
    last = None
    for attempt in range(4):
        host = "query1" if attempt % 2 == 0 else "query2"
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{pair}=X?interval=1h&range=5d&includePrePost=false"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            res = d["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            rows = {}
            for i, t in enumerate(res["timestamp"]):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                t = int(t) - int(t) % 3600          # the newest row can carry the current time: put it in its hour
                if t in rows:
                    r0 = rows[t]
                    rows[t] = (r0[0], max(r0[1], h), min(r0[2], l), c)
                else:
                    rows[t] = (o, h, l, c)
            return rows
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"{pair}: {last}")


def clean(pair, row):
    """Yahoo sometimes prints a wild wick; cut wicks longer than 100 pips back to the body."""
    pip = 0.01 if pair.endswith("JPY") else 0.0001
    o, h, l, c = row
    top, bot = max(o, c), min(o, c)
    if h - top > 100 * pip:
        h = top
    if bot - l > 100 * pip:
        l = bot
    dp = 3 if pair.endswith("JPY") else 5
    return tuple(round(v, dp) for v in (o, h, l, c))


def main():
    feed = json.load(open(os.path.join(ROOT, "data", "feed.json")))
    os.makedirs(BARS, exist_ok=True)
    ok, bad = 0, []
    now = int(time.time())
    for doc in feed.get("zones", []):
        pair, sym = doc["pair"], doc["symbol"]
        try:
            rows = yahoo(pair)
        except Exception as e:                      # noqa: BLE001
            bad.append(str(e)[:160]); continue
        if not rows:
            bad.append(f"{pair}: no rows"); continue
        fn = os.path.join(BARS, f"{sym.replace(':', '_')}_1h.json")
        m = {}
        if os.path.exists(fn):
            try:
                b = json.load(open(fn))
                m = {t: (b["o"][i], b["h"][i], b["l"][i], b["c"][i]) for i, t in enumerate(b["t"])}
            except Exception:                       # noqa: BLE001
                m = {}
        for t, row in rows.items():
            m[t] = clean(pair, row)
        ts = sorted(m)[-240:]
        out = {"symbol": sym, "tf": "1h", "fetched_at": now, "source": "yahoo",
               "t": ts, "o": [m[t][0] for t in ts], "h": [m[t][1] for t in ts], "l": [m[t][2] for t in ts], "c": [m[t][3] for t in ts]}
        open(fn, "w").write(json.dumps(out, separators=(",", ":")))
        last = max(rows)
        print(f"{pair}: {len(rows)} bars, newest {time.strftime('%d %b %H:%M UTC', time.gmtime(last))} close {rows[last][3]:.5f}")
        ok += 1
        time.sleep(0.6)
    for b in bad:
        print("FAILED", b)
    if ok < max(1, len(feed.get("zones", [])) // 2):
        sys.exit("too few pairs could be read; nothing will be checked")


if __name__ == "__main__":
    main()
