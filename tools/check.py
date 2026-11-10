#!/usr/bin/env python3
"""
Hourly check for the Set & Forget Desk app.

All state lives in data/feed.json (what the app shows). Each notification is
also written to data/outbox/ as one small JSON file; pushing a new outbox file
to GitHub makes the "Send notifications" workflow deliver it to the phones.

  python3 tools/check.py run --bars DIR      zone arrivals + prices from 1h bars
  python3 tools/check.py setup PAIR ...      change a watched setup (status/step/note) and notify
  python3 tools/check.py alert PAIR TEXT     log + notify a free-text note
  python3 tools/check.py status              print where price is against every zone

Bars: DIR/OANDA_<PAIR>_1h.json as written by tools/cache_ohlcv.py
      ({"t":[unix sec, bar open],"o":[],"h":[],"l":[],"c":[]}; last bar may be forming).

This script never places trades. It only reads prices and writes notes.
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "data", "feed.json")
OUTBOX = os.path.join(ROOT, "data", "outbox")
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


def add_alert(feed, now, pair, title, text, seq):
    """Log to the app's notification list and queue a push."""
    feed.setdefault("alerts", []).insert(0, {"at": iso(now), "pair": pair, "text": text})
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


def cmd_run(a):
    now = parse_iso(a.now) if a.now else datetime.now(timezone.utc)
    feed = load()
    setups = {s["pair"]: s for s in feed.get("setups", [])}
    today = now.astimezone(IST).strftime("%Y-%m-%d")
    window = entry_window_open(now)
    report, deep, problems, seq = [], [], [], 0

    for doc in feed.get("zones", []):
        pair = doc["pair"]
        fn = os.path.join(a.bars, f"{doc['symbol'].replace(':', '_')}_1h.json")
        if not os.path.exists(fn):
            problems.append(f"{pair}: no 1h bars file ({os.path.basename(fn)})")
            continue
        b = json.load(open(fn))
        if not b.get("t"):
            problems.append(f"{pair}: empty bars")
            continue
        if now.timestamp() - b["t"][-1] > STALE_SEC:
            problems.append(f"{pair}: newest bar is {int((now.timestamp() - b['t'][-1]) / 3600)}h old, skipped")
            continue
        pip = doc["pip"]
        checked = parse_iso(doc["checked_at"]).timestamp()
        prev_price = doc.get("price")
        price = b["c"][-1]
        # bars that were still open at, or opened after, the last check
        idx = [i for i, t in enumerate(b["t"]) if t + 3600 > checked]
        if not idx:
            idx = [len(b["t"]) - 1]
        lo_seen = min(b["l"][i] for i in idx)
        hi_seen = max(b["h"][i] for i in idx)
        # last closed bar (the newest bar counts as closed only once its hour has passed)
        last_closed = len(b["t"]) - 1 if b["t"][-1] + 3600 <= now.timestamp() else len(b["t"]) - 2
        prev_inside = set(doc.get("inside", []))
        alerted = doc.setdefault("alerted", {})

        reached = [z for z in doc["zones"] if lo_seen <= z["hi"] and hi_seen >= z["lo"]]
        new = [z for z in reached if z["id"] not in prev_inside and alerted.get(z["id"]) != today]

        inside = []
        for z in doc["zones"]:
            contains = z["lo"] <= price <= z["hi"]
            touched_last = last_closed >= 0 and b["l"][last_closed] <= z["hi"] and b["h"][last_closed] >= z["lo"]
            near = (z["lo"] - KEEP_PIPS * pip) <= price <= (z["hi"] + KEEP_PIPS * pip)
            if contains or touched_last or (z["id"] in prev_inside and near):
                inside.append(z["id"])

        s = setups.get(pair)
        watched = s and s.get("status") in ("waiting", "at_zone", "triggered")
        plan_reached = False
        if watched and isinstance(s.get("zone_lo"), (int, float)):
            plan_reached = lo_seen <= s["zone_hi"] and hi_seen >= s["zone_lo"]
            s["price"], s["price_at"] = price, iso(now)

        covered_plan = False
        for lo, hi, ids in merge_ranges(new):
            seq += 1
            if prev_price is None:
                how = "reached"
            elif prev_price < lo:
                how = "came up into"
            elif prev_price > hi:
                how = "came down into"
            else:
                how = "is back in"
            where = "" if lo <= price <= hi else " and has moved out again"
            text = f"Price {how} your AOI {fp(pair, lo)}-{fp(pair, hi)}{where} (now {fp(pair, price)})."
            if watched:
                overlaps = s["zone_lo"] <= hi and s["zone_hi"] >= lo
                if overlaps:
                    covered_plan = True
                    text += (f" On the plan as a {s['side']}: next it needs a 1H shift of structure at the zone, "
                             f"then a closed engulfing.")
                else:
                    text += (f" On the plan as a {s['side']}, but this is not the plan's zone "
                             f"({fp(pair, s['zone_lo'])}-{fp(pair, s['zone_hi'])}).")
            elif s and s.get("note"):
                text += f" Not on this week's plan: {s['note']} Information only."
            else:
                text += " Not on this week's plan. Information only."
            if not window:
                text += " Entry window is closed now."
            add_alert(feed, now, pair, f"{pair} reached your AOI", text, seq)
            for i in ids:
                alerted[i] = today
            report.append(f"NEW  {pair}: {text}")

        if watched and plan_reached:
            if s.get("status") == "waiting":
                s["status"], s["step"] = "at_zone", max(1, int(s.get("step") or 0))
                if not covered_plan:
                    seq += 1
                    text = (f"Price reached the plan's {s['side']} zone {fp(pair, s['zone_lo'])}-{fp(pair, s['zone_hi'])} "
                            f"(now {fp(pair, price)}). Next: a 1H shift of structure, then a closed engulfing.")
                    if not window:
                        text += " Entry window is closed now."
                    add_alert(feed, now, pair, f"{pair} at the {s['side']} zone", text, seq)
                    report.append(f"NEW  {pair}: {text}")
            deep.append(f"{pair} ({s['side']}, status {s['status']}, step {s.get('step', 0)}): price is at the plan zone "
                        f"{fp(pair, s['zone_lo'])}-{fp(pair, s['zone_hi'])}. Check the 1H/30m shift of structure and a closed "
                        f"engulfing, then record it with: tools/check.py setup {pair} --step N [--status triggered] --alert \"...\"")
        elif watched and s.get("status") == "at_zone" and int(s.get("step") or 0) <= 1 and not (
                s["zone_lo"] - KEEP_PIPS * pip <= price <= s["zone_hi"] + KEEP_PIPS * pip):
            s["status"], s["step"] = "waiting", 0
            report.append(f"INFO {pair}: left the plan zone without a shift; back to waiting.")
        if watched and isinstance(s.get("invalid_level"), (int, float)):
            above = "above" in (s.get("invalid_note") or "")
            beyond = any((b["c"][i] > s["invalid_level"]) if above else (b["c"][i] < s["invalid_level"]) for i in idx)
            if beyond:
                deep.append(f"{pair}: a 1H candle closed {'above' if above else 'below'} the idea-fails level "
                            f"{fp(pair, s['invalid_level'])}. Confirm on the 4H ({s.get('invalid_note')}); if confirmed: "
                            f"tools/check.py setup {pair} --status invalid --alert \"...\"")

        doc["inside"], doc["price"], doc["checked_at"] = inside, price, iso(now)

    # morning update: first run between 09:00 and 10:15 IST, once a day
    t_ist = now.astimezone(IST)
    mins = t_ist.hour * 60 + t_ist.minute
    morning = (a.morning or (9 * 60 <= mins <= 10 * 60 + 15)) and feed.get("morning_sent") != today
    if morning:
        ins = [d["pair"] for d in feed["zones"] if d.get("inside")]
        parts = []
        for s in sorted(setups.values(), key=lambda s: s.get("order", 99)):
            if s.get("status") in ("waiting", "at_zone", "triggered") and isinstance(s.get("price"), (int, float)):
                pip = 0.01 if s["pair"].endswith("JPY") else 0.0001
                p = s["price"]
                dist = 0 if s["zone_lo"] <= p <= s["zone_hi"] else min(abs(p - s["zone_lo"]), abs(p - s["zone_hi"])) / pip
                parts.append(f"{s['pair']} {s['side']} " + ("at zone" if dist == 0 else f"{dist:.0f} pips from zone"))
        text = "Plan: " + ("; ".join(parts) if parts else "no watched setups") + ". "
        text += ("Inside your AOIs now: " + ", ".join(ins) + "." if ins else "No pair is inside one of your AOIs right now.")
        seq += 1
        add_alert(feed, now, "", "Morning update", text, seq)
        feed["morning_sent"] = today
        report.append(f"MORNING {text}")

    save(feed, now)
    print(f"checked {iso(now)}  entry window {'OPEN' if window else 'CLOSED'}")
    for line in report:
        print(line)
    if not report:
        print("NOTHING NEW")
    if deep:
        print("\nDEEP CHECK NEEDED:")
        for line in deep:
            print(" -", line)
    if problems:
        print("\nPROBLEMS:")
        for line in problems:
            print(" -", line)
    print()
    cmd_status(a, feed)


def cmd_status(a, feed=None):
    feed = feed or load()
    print("pair     price       state")
    for doc in feed["zones"]:
        pair, price = doc["pair"], doc.get("price")
        if price is None:
            continue
        ins = [z for z in doc["zones"] if z["id"] in doc.get("inside", [])]
        if ins:
            st = "INSIDE " + ", ".join(f"{fp(pair, z['lo'])}-{fp(pair, z['hi'])}" for z in ins)
        else:
            n = nearest(doc, price)
            st = "-" if not n else (f"{n[0]:.0f} pips {'below' if price < n[1]['lo'] else 'above'} "
                                    f"{fp(pair, n[1]['lo'])}-{fp(pair, n[1]['hi'])}")
        print(f"{pair:8} {fp(pair, price):>10}  {st}")


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
    r = sub.add_parser("run"); r.add_argument("--bars", required=True); r.add_argument("--now"); r.add_argument("--morning", action="store_true")
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
