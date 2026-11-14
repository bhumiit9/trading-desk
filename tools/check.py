#!/usr/bin/env python3
"""
Hourly check for the Set & Forget Desk app.

All state lives in data/feed.json (what the app shows). Each notification is
also written to data/outbox/ as one small JSON file; pushing a new outbox file
to GitHub makes the "Send notifications" workflow deliver it to the phones.

  python3 tools/check.py need                which price bars to fetch now (symbol, interval, count)
  python3 tools/check.py run [--now ISO]     watch the week's final list (data/week.json) on the 1h bars in data/bars:
                                             AOI arrival and entry candle notifications (--now replays a past moment)
  python3 tools/check.py setup PAIR ...      change a watched setup (status/step/note) and notify
  python3 tools/check.py alert PAIR TEXT     log + notify a free-text note
  python3 tools/check.py status              print where each final-list pair stands

Bars: data/bars/OANDA_<PAIR>_<1h|4h|D>.json as written by
      python3 tools/cache_ohlcv.py --out data/bars --hours 1
      ({"fetched_at":unix,"t":[unix sec, bar open],"o":[],"h":[],"l":[],"c":[]}; the last bar may be forming).
      The app draws its charts from these files.

This script never places trades. It only reads prices and writes notes.
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta, time as dtime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "data", "feed.json")
OUTBOX = os.path.join(ROOT, "data", "outbox")
BARS = os.path.join(ROOT, "data", "bars")
WEEK = os.path.join(ROOT, "data", "week.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entry                                    # noqa: E402  the 1H shift and entry-candle checks
PERIOD = {"1h": 3600}
KEEP_BARS = {"1h": 500, "4h": 200, "1d": 220, "1w": 80}   # the last three are built by fetch_prices.py for the in-app chat
SEED = {"1h": 150}
IST = ZoneInfo("Asia/Kolkata")
NY = ZoneInfo("America/New_York")
KEEP_ALERTS = 80
KEEP_PIPS = 10          # a zone stays "inside" until price is this far outside it
STALE_SEC = 3 * 3600    # newest bar must be this fresh, or the pair is skipped
DISCLAIMER = "Rule-based analysis, not financial advice. The trade decision is yours."


def load():
    return json.load(open(FEED))


def save(feed, now):
    feed["updated"] = iso(now)
    feed["alerts"] = feed.get("alerts", [])[:KEEP_ALERTS]
    tmp = FEED + ".tmp"
    json.dump(feed, open(tmp, "w"), indent=1)
    os.replace(tmp, FEED)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def dp(pair):
    return 3 if pair.endswith("JPY") else 5


def fp(pair, v):
    return f"{v:.{dp(pair)}f}"


def entry_window_open(now):
    """About 11pm-11am New York, Sunday night to Thursday night (+ Friday until 1am)."""
    t = now.astimezone(NY)
    wd, h = t.weekday(), t.hour + t.minute / 60     # Mon=0 .. Sun=6
    if wd == 5:
        return False
    if wd == 6:
        return h >= 23
    if wd == 4:
        return h < 1
    return h >= 23 or h < 11


def add_alert(feed, now, pair, title, text, seq, push=True):
    """Log to the app's notification list and, unless push is False, queue a push to the phones."""
    feed.setdefault("alerts", []).insert(0, {"at": iso(now), "pair": pair, "text": text})
    if not push:
        return None
    os.makedirs(OUTBOX, exist_ok=True)
    name = f"{now.astimezone(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{pair or 'DESK'}-{seq}.json"
    json.dump({"at": iso(now), "title": title, "body": text, "tag": f"{pair}-{seq}-{int(now.timestamp())}", "url": "./"},
              open(os.path.join(OUTBOX, name), "w"), indent=1)
    return name


def merge_ranges(zs):
    """Union overlapping zones -> list of (lo, hi, [ids])."""
    out = []
    for z in sorted(zs, key=lambda z: z["lo"]):
        if out and z["lo"] <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], z["hi"]), out[-1][2] + [z["id"]])
        else:
            out.append((z["lo"], z["hi"], [z["id"]]))
    return out


def nearest(doc, price):
    """(distance in pips, zone) for the closest zone price is NOT inside; None if none."""
    best = None
    for z in doc["zones"]:
        if z["lo"] <= price <= z["hi"]:
            continue
        d = min(abs(price - z["lo"]), abs(price - z["hi"])) / doc["pip"]
        if best is None or d < best[0]:
            best = (d, z)
    return best


def bars_file(symbol, tf, folder=None):
    return os.path.join(folder or BARS, f"{symbol.replace(':', '_')}_{tf}.json")


def cmd_need(a):
    """Print the TradingView fetches this run needs: one line per call."""
    now = time.time()
    feed = load()
    lines = []
    for doc in feed.get("zones", []):
        sym = doc["symbol"]
        for tf, per in PERIOD.items():
            fn = bars_file(sym, tf)
            n, last, fetched = 0, 0, 0
            if os.path.exists(fn):
                try:
                    b = json.load(open(fn)); n, last, fetched = len(b["t"]), b["t"][-1], b.get("fetched_at", 0)
                except Exception:
                    n = 0
            if n < SEED[tf] * 0.6:
                count = SEED[tf]
            elif tf == "1h":
                count = max(4, min(150, int((now - last) / per) + 3))
            else:
                final = last + per <= fetched            # the newest stored bar was already closed when fetched
                if (not final and now >= last + per) or (final and now >= last + 2 * per):
                    count = max(3, min(SEED[tf], int((now - last) / per) + 3))
                else:
                    continue
            lines.append(f"{sym}  interval={'1D' if tf == 'D' else tf}  count={count}")
    print(f"{len(lines)} fetches needed (mcp-tv-get-ohlcv, format=columns):")
    for l in lines:
        print(" ", l)


def zone_confluence(s, at_zone, now):
    """Tick or untick the "At / rejected AOI" box of the zone's timeframe on a trade card and redo its percentage.
    The boxes and their points follow the "Set & Forget - AlexG Club - Checklist" indicator."""
    conf = s.get("conf")
    if not isinstance(conf, list):
        return
    for c in conf:
        if isinstance(c, dict) and (c.get("auto") == "zone" or c.get("name") == "At the zone"):
            c["ok"] = bool(at_zone)
            c.setdefault("pts", 10)
    s["score"] = sum(int(c.get("pts") or 0) for c in conf if isinstance(c, dict) and c.get("ok"))
    s["score_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")


def tidy_bars():
    """Keep the bar files small: newest bars only, compact JSON."""
    if not os.path.isdir(BARS):
        return
    for f in os.listdir(BARS):
        if not f.endswith(".json"):
            continue
        tf = f[:-5].rsplit("_", 1)[-1]
        if tf not in KEEP_BARS:
            os.remove(os.path.join(BARS, f)); continue
        fn = os.path.join(BARS, f)
        try:
            b = json.load(open(fn))
        except Exception:
            continue
        k = KEEP_BARS[tf]
        out = {"symbol": b.get("symbol"), "tf": tf, "fetched_at": int(b.get("fetched_at", 0)), **({"upto": int(b["upto"])} if "upto" in b else {}), **{x: b[x][-k:] for x in "tohlc"}}
        txt = json.dumps(out, separators=(",", ":"))
        if txt != open(fn).read():
            open(fn, "w").write(txt)


def week_start(week_of):
    """Sunday 5 PM New York before the week's Monday: when the week's first candle opens."""
    mon = datetime.fromisoformat(week_of).date()
    return datetime.combine(mon - timedelta(days=1), dtime(17, 0), NY).timestamp()


def load_week():
    try:
        w = json.load(open(WEEK))
        return w if w.get("week_of") else None
    except Exception:
        return None


def pip_of(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001


def aoi_text(pair, z):
    return f"{fp(pair, z['lo'])}-{fp(pair, z['hi'])}"


def at_ist(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%a %H:%M") + " IST"


def aoi_dist(pair, aois, price):
    """Pips from price to the nearest AOI (0 inside one)."""
    best = None
    for z in aois:
        d = 0 if z["lo"] <= price <= z["hi"] else min(abs(price - z["lo"]), abs(price - z["hi"]))
        best = d if best is None else min(best, d)
    return round((best or 0) / pip_of(pair))


STATUS_WORDS = {"watching": "watching", "at_aoi": "at its AOI", "shift": "1H shift seen at the AOI",
                "entry": "entry candle closed"}


def cmd_run(a):
    """Watch only the pairs on the week's final list (data/week.json): tell the phone when price reaches
    one of their AOIs and when the entry shows up (a 1H shift of structure at the AOI, then a closed
    engulfing or morning / evening star). The state the app shows is saved in feed.json under "watch"."""
    now = parse_iso(a.now) if a.now else datetime.now(timezone.utc)
    nts = now.timestamp()
    feed = load()
    tidy_bars()
    today = now.astimezone(IST).strftime("%Y-%m-%d")
    week = load_week()
    report, problems, seq = [], [], 0
    window = entry_window_open(now)
    watch = feed.get("watch")
    if not week:
        problems.append("data/week.json has no week yet, so nothing is watched.")
        week = {"week_of": None, "final": []}
    if not isinstance(watch, dict) or watch.get("week_of") != week.get("week_of"):
        watch = {"week_of": week.get("week_of"), "pairs": {}}
    states = watch.setdefault("pairs", {})
    ws = week_start(week["week_of"]) if week.get("week_of") else None
    live = ws is not None and ws - 12 * 3600 <= nts < ws + 6 * 86400
    if ws is not None and not live:
        problems.append(f"The final list is for {week.get('label')}; it is watched only during that week.")
    final = week.get("final", []) if live else []

    for item in final:
        pair, side, aois = item["pair"], item["side"], item.get("aois") or []
        key = json.dumps([side, aois], sort_keys=True)
        s = states.get(pair)
        if not isinstance(s, dict) or s.get("key") != key:            # new pair, or its side or AOIs changed
            s = states[pair] = {"key": key, "sent": [], "inside": False}
        s["side"] = side
        fn = bars_file("OANDA:" + pair, "1h", a.bars)
        if not os.path.exists(fn) or not aois:
            problems.append(f"{pair}: {'no AOIs on the final list' if not aois else 'no 1h candles yet (' + os.path.basename(fn) + ')'}")
            continue
        b = entry.trading_bars(json.load(open(fn)))
        if a.now:                                                    # replay: only candles that had opened by then
            keep = [i for i, t in enumerate(b["t"]) if t <= nts]
            b = {k: [b[k][i] for i in keep] for k in "tohlc"}
        if not b.get("t") or nts - b["t"][-1] > STALE_SEC:
            problems.append(f"{pair}: the newest candle is too old, skipped")
            continue
        pip = pip_of(pair)
        r = entry.assess(b, side, aois, pip, nts, ws,
                         lambda t: entry_window_open(datetime.fromtimestamp(t, timezone.utc)))
        price = r["price"]
        checked = parse_iso(s["checked_at"]).timestamp() if s.get("checked_at") else ws
        # did any candle since the last check reach an AOI?
        reached = None
        for i, t in enumerate(b["t"]):
            if t < ws or t + 3600 <= checked:
                continue
            lo = min(b["l"][i], b["c"][i - 1] if i else b["l"][i])
            hi = max(b["h"][i], b["c"][i - 1] if i else b["h"][i])
            reached = next((z for z in aois if lo <= z["hi"] and hi >= z["lo"]), None)
            if reached:
                break
        sent = s.setdefault("sent", [])
        told = False
        if r["early"] and r["shift"] and f"early:{r['early']['t']}" not in sent:
            e, sh = r["early"], r["shift"]
            cw = datetime.fromtimestamp(e["t"] + 3600, timezone.utc)
            why = ("on Friday, when the rules take no new entries" if cw.astimezone(NY).weekday() == 4
                   else "outside the entry window (about 11 PM to 11 AM New York)")
            text = (f"{pair} {side}: a 1H {e['kind']} closed at {at_ist(e['t'] + 3600)} at your AOI {aoi_text(pair, e['aoi'])}, "
                    f"after the 1H shift, but {why}, so it doesn't count as an entry. Still waiting for one inside the window.")
            seq += 1
            add_alert(feed, now, pair, f"{pair}: candle outside the entry window", text, seq, push=False)
            sent.append(f"early:{e['t']}")
            report.append(f"EARLY {pair} (in the app only): {text}")
        if r["entry"]:
            e, sh = r["entry"], r["shift"]
            k = f"entry:{e['t']}"
            if k not in sent:
                text = (f"{pair} {side}: a 1H {e['kind']} closed at {at_ist(e['t'] + 3600)} at your AOI {aoi_text(pair, e['aoi'])}, "
                        f"after the 1H shift (a body closed {'above' if side == 'buy' else 'below'} {fp(pair, sh['level'])} "
                        f"at {at_ist(sh['t'] + 3600)}). The checklist's entry conditions are there; the rest of the "
                        f"checklist and the decision are yours.")
                seq += 1
                add_alert(feed, now, pair, f"{pair}: entry candle closed", text, seq)
                sent += [k, f"shift:{sh['t']}"]
                told = True
                report.append(f"ENTRY {pair}: {text}")
        if r["shift"]:
            sh = r["shift"]
            k = f"shift:{sh['t']}"
            if k not in sent:
                text = (f"{pair} {side}: 1H shift of structure at your AOI {aoi_text(pair, sh['aoi'])}: a body closed "
                        f"{'above' if side == 'buy' else 'below'} {fp(pair, sh['level'])} at {at_ist(sh['t'] + 3600)}. "
                        f"Waiting for a closed entry candle.")
                seq += 1
                add_alert(feed, now, pair, f"{pair}: 1H shift at the AOI", text, seq, push=False)
                sent.append(k)
                report.append(f"SHIFT {pair}: {text}")
        if reached and not s.get("inside") and f"arrive:{today}" not in sent:
            if not told:
                text = f"{pair} {side}: price reached your AOI {aoi_text(pair, reached)} (now {fp(pair, price)})."
                if r["entry"]:
                    text += f" A 1H {r['entry']['kind']} already closed there at {at_ist(r['entry']['t'] + 3600)}."
                elif r["shift"]:
                    text += f" The 1H shift from {at_ist(r['shift']['t'] + 3600)} is still in effect; next is a closed entry candle."
                else:
                    text += " Next on the checklist: a 1H shift of structure at the AOI, then a closed entry candle."
                if not window:
                    text += " The entry window is closed now (it runs about 11 PM to 11 AM New York, Sunday night to Thursday night)."
                seq += 1
                add_alert(feed, now, pair, f"{pair} reached your AOI", text, seq)
                report.append(f"AOI   {pair}: {text}")
            sent.append(f"arrive:{today}")
        status = "entry" if r["entry"] else "shift" if r["shift"] else "at_aoi" if r["at_aoi"] else "watching"
        s.update(status=status, price=price, price_at=iso(now), checked_at=iso(now), trend_1h=r["trend"],
                 inside=bool(r["at_aoi"] or (reached and lo_hi_now(b, reached))), dist_pips=aoi_dist(pair, aois, price))
        s["arrived_at"] = iso(datetime.fromtimestamp(r["arrived"]["t"], timezone.utc)) if r["arrived"] else None
        s["shift"] = ({"at": iso(datetime.fromtimestamp(r["shift"]["t"] + 3600, timezone.utc)), "level": r["shift"]["level"]}
                      if r["shift"] else None)
        s["entry"] = ({"at": iso(datetime.fromtimestamp(r["entry"]["t"] + 3600, timezone.utc)), "kind": r["entry"]["kind"]}
                      if r["entry"] else None)
        s["sent"] = sent[-40:]

    for p in list(states):                                           # pairs taken off the final list
        if p not in {x["pair"] for x in final}:
            del states[p]
    watch["checked_at"] = iso(now)
    feed["watch"] = watch

    # morning update: the first run between 09:00 and 10:15 IST, once a day, when the final list is watched
    t_ist = now.astimezone(IST)
    mins = t_ist.hour * 60 + t_ist.minute
    if final and (a.morning or 9 * 60 <= mins <= 10 * 60 + 15) and feed.get("morning_sent") != today:
        parts = []
        for item in final:
            s = states.get(item["pair"]) or {}
            if s.get("status") in ("at_aoi", "shift", "entry"):
                parts.append(f"{item['pair']} {item['side']} {STATUS_WORDS[s['status']]}")
            elif isinstance(s.get("dist_pips"), (int, float)):
                parts.append(f"{item['pair']} {item['side']} {s['dist_pips']} pips from its AOI")
        text = "Final list: " + "; ".join(parts) + "." if parts else "Final list: no prices yet."
        seq += 1
        add_alert(feed, now, "", "Morning update", text, seq)
        feed["morning_sent"] = today
        report.append(f"MORNING {text}")

    # sent notifications older than a week are no longer needed in the outbox
    for f in os.listdir(OUTBOX) if os.path.isdir(OUTBOX) else []:
        if f.endswith(".json") and now.timestamp() - os.path.getmtime(os.path.join(OUTBOX, f)) > 7 * 86400:
            try:
                d = json.load(open(os.path.join(OUTBOX, f)))
                if now.timestamp() - parse_iso(d["at"]).timestamp() > 7 * 86400:
                    os.remove(os.path.join(OUTBOX, f))
            except Exception:
                pass
    save(feed, now)
    print(f"checked {iso(now)}  entry window {'OPEN' if window else 'CLOSED'}  final list: "
          + (", ".join(x["pair"] for x in final) or "nothing watched"))
    for line in report:
        print(line)
    if not report:
        print("NOTHING NEW")
    if problems:
        print("\nPROBLEMS:")
        for line in problems:
            print(" -", line)
    print()
    cmd_status(a, feed)


def lo_hi_now(b, z):
    """Is the newest candle still touching the AOI?"""
    return b["l"][-1] <= z["hi"] and b["h"][-1] >= z["lo"]


def cmd_status(a, feed=None):
    feed = feed or load()
    w = load_week() or {}
    states = (feed.get("watch") or {}).get("pairs", {})
    print(f"{w.get('label', 'No week')}: final list")
    if not w.get("final"):
        print("  (empty)")
    for item in w.get("final", []):
        s = states.get(item["pair"], {})
        z = ", ".join(aoi_text(item["pair"], z) for z in item.get("aois", []))
        p = s.get("price")
        print(f"  {item['pair']:7} {item['side']:4} AOI {z:28} price {fp(item['pair'], p) if p else '-':>10}  "
              f"{STATUS_WORDS.get(s.get('status'), 'not checked yet')}"
              + (f", {s['dist_pips']} pips away" if s.get("status") == "watching" and s.get("dist_pips") is not None else ""))


def cmd_setup(a):
    now = datetime.now(timezone.utc)
    feed = load()
    s = next((s for s in feed["setups"] if s["pair"] == a.pair), None)
    if not s:
        sys.exit(f"no setup for {a.pair}")
    if a.status:
        s["status"] = a.status
    if a.step is not None:
        s["step"] = a.step
    if a.note:
        s["note"] = a.note
    if a.alert:
        title = a.title or {"triggered": f"{a.pair}: trigger seen", "invalid": f"{a.pair}: idea failed",
                            "at_zone": f"{a.pair} at the zone"}.get(s.get("status"), f"{a.pair} update")
        add_alert(feed, now, a.pair, title, a.alert, int(now.timestamp()) % 1000)
    save(feed, now)
    print(f"{a.pair}: status {s.get('status')} step {s.get('step')}")


def cmd_alert(a):
    now = datetime.now(timezone.utc)
    feed = load()
    add_alert(feed, now, a.pair if a.pair != "-" else "", a.title or (f"{a.pair} update" if a.pair != "-" else "Set & Forget Desk"),
              a.text, int(now.timestamp()) % 1000)
    save(feed, now)
    print("queued")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("need"); n.set_defaults(fn=cmd_need)
    r = sub.add_parser("run"); r.add_argument("--bars", default=BARS); r.add_argument("--now"); r.add_argument("--morning", action="store_true")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("setup"); s.add_argument("pair")
    s.add_argument("--status", choices=["waiting", "at_zone", "triggered", "invalid", "no_trade"])
    s.add_argument("--step", type=int, choices=range(0, 5)); s.add_argument("--note"); s.add_argument("--alert"); s.add_argument("--title")
    s.set_defaults(fn=cmd_setup)
    al = sub.add_parser("alert"); al.add_argument("pair"); al.add_argument("text"); al.add_argument("--title")
    al.set_defaults(fn=cmd_alert)
    st = sub.add_parser("status"); st.set_defaults(fn=cmd_status)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
