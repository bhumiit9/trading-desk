#!/usr/bin/env python3
"""
The week's lists in data/week.json: the trader's picks ("yours"), Claude's picks ("claude") and the
final agreed list ("final"). Only the final list is watched by the hourly check.

  python3 tools/week.py new [--monday 2026-11-16]          start a new week (the old one is kept in data/weeks/)
  python3 tools/week.py add LIST PAIR SIDE LO-HI[:TF] ...   add or replace a pair; LIST is yours, claude or final
        e.g.  python3 tools/week.py add claude EURNZD sell 2.0041-2.0110:D --note "4 daily touches"
  python3 tools/week.py remove LIST PAIR
  python3 tools/week.py final PAIR [PAIR ...] [--from yours|claude]
        copy pairs from the picks into the final list. A pair in both lists with the same side gets the AOIs
        of both; when the sides differ, say which list to take it from with --from.
  python3 tools/week.py show

Each AOI is LO-HI with an optional timeframe (W or D), for example 1.0850-1.0875:W. Levels are body
levels from the chart. A stop, entry or target is never written here.
"""
import argparse, json, os, re, sys
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEK = os.path.join(ROOT, "data", "week.json")
ARCHIVE = os.path.join(ROOT, "data", "weeks")
BARS = os.path.join(ROOT, "data", "bars")
LISTS = ("yours", "claude", "final")
NAMES = {"yours": "Your picks", "claude": "Claude's picks", "final": "Final list"}
IST = ZoneInfo("Asia/Kolkata")


def pip(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001


def fp(pair, v):
    return f"{v:.{3 if pair.endswith('JPY') else 5}f}"


def label_for(monday):
    return f"Week of {monday.day} {monday.strftime('%b')}"


def blank(monday):
    return {"week_of": monday.isoformat(), "label": label_for(monday), "updated": now_iso(),
            "yours": [], "claude": [], "final": []}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load():
    if not os.path.exists(WEEK):
        return blank(next_monday())
    w = json.load(open(WEEK))
    for k in LISTS:
        w.setdefault(k, [])
    return w


def save(w):
    w["updated"] = now_iso()
    tmp = WEEK + ".tmp"
    json.dump(w, open(tmp, "w"), indent=1)
    os.replace(tmp, WEEK)


def next_monday(today=None):
    d = today or datetime.now(IST).date()
    return d + timedelta(days=(7 - d.weekday()) % 7)        # today when it is Monday, else the coming Monday


def last_price(pair):
    fn = os.path.join(BARS, f"OANDA_{pair}_1h.json")
    try:
        return json.load(open(fn))["c"][-1]
    except Exception:
        return None


def parse_aoi(pair, text):
    m = re.fullmatch(r"\s*([0-9.]+)\s*-\s*([0-9.]+)\s*(?::\s*(W|D|W\+D))?\s*", text, re.I)
    if not m:
        sys.exit(f"Could not read the AOI '{text}'. Write it as LO-HI or LO-HI:D, for example 1.0850-1.0875:D")
    lo, hi = sorted((float(m.group(1)), float(m.group(2))))
    if hi - lo < pip(pair):
        sys.exit(f"The AOI {text} for {pair} is less than a pip tall.")
    if (hi - lo) / pip(pair) > 300:
        sys.exit(f"The AOI {text} for {pair} is {round((hi - lo) / pip(pair))} pips tall. Check the levels.")
    p = last_price(pair)
    if p and min(abs(p - lo), abs(p - hi)) / pip(pair) > 1500:
        sys.exit(f"The AOI {text} is more than 1500 pips from {pair}'s last price {fp(pair, p)}. Check the levels.")
    z = {"lo": round(lo, 6), "hi": round(hi, 6)}
    if m.group(3):
        z["tf"] = m.group(3).upper()
    return z


def find(lst, pair):
    return next((x for x in lst if x["pair"] == pair), None)


def cmd_new(a):
    old = load() if os.path.exists(WEEK) else None
    monday = date.fromisoformat(a.monday) if a.monday else next_monday()
    if monday.weekday() != 0:
        sys.exit(f"{monday} is not a Monday.")
    if old and old.get("week_of") == monday.isoformat() and not a.force:
        sys.exit(f"The lists are already for {old['label']}. Add --force to empty them.")
    if old and any(old.get(k) for k in LISTS):
        os.makedirs(ARCHIVE, exist_ok=True)
        json.dump(old, open(os.path.join(ARCHIVE, f"{old.get('week_of', 'old')}.json"), "w"), indent=1)
    save(blank(monday))
    print(f"Started {label_for(monday)}. The lists are empty.")


def cmd_add(a):
    w = load()
    pair = a.pair.upper().replace("/", "")
    if not re.fullmatch(r"[A-Z]{6}", pair):
        sys.exit(f"{a.pair} is not a six-letter pair.")
    side = a.side.lower()
    if side not in ("buy", "sell"):
        sys.exit("SIDE must be buy or sell.")
    aois = sorted((parse_aoi(pair, x) for x in a.aoi), key=lambda z: z["lo"])
    item = {"pair": pair, "side": side, "aois": aois}
    if a.note:
        item["note"] = a.note.strip()
    lst = w[a.list]
    old = find(lst, pair)
    if old:
        lst[lst.index(old)] = item
    else:
        lst.append(item)
    save(w)
    print(f"{NAMES[a.list]}: {'replaced' if old else 'added'} {pair} {side} at "
          + ", ".join(f"{fp(pair, z['lo'])}-{fp(pair, z['hi'])}" + (f" ({z['tf']})" if z.get("tf") else "") for z in aois))


def cmd_remove(a):
    w = load()
    pair = a.pair.upper()
    old = find(w[a.list], pair)
    if not old:
        sys.exit(f"{pair} is not in {NAMES[a.list]}.")
    w[a.list].remove(old)
    save(w)
    print(f"{NAMES[a.list]}: removed {pair}.")


def cmd_final(a):
    w = load()
    for pair in [p.upper() for p in a.pairs]:
        srcs = [a.src] if a.src else ["yours", "claude"]
        found = [(k, find(w[k], pair)) for k in srcs if find(w[k], pair)]
        if not found:
            sys.exit(f"{pair} is in neither pick list. Add it with: tools/week.py add final {pair} SIDE LO-HI")
        sides = {x["side"] for _, x in found}
        if len(sides) > 1:
            sys.exit(f"{pair} is a buy in one list and a sell in the other. Say which with --from yours or --from claude.")
        aois = []
        for _, x in found:
            for z in x["aois"]:
                if not any(abs(z["lo"] - y["lo"]) < 1e-9 and abs(z["hi"] - y["hi"]) < 1e-9 for y in aois):
                    aois.append(dict(z))
        item = {"pair": pair, "side": sides.pop(), "aois": sorted(aois, key=lambda z: z["lo"]), "from": [k for k, _ in found]}
        notes = [x["note"] for _, x in found if x.get("note")]
        if notes:
            item["note"] = " / ".join(notes)
        old = find(w["final"], pair)
        if old:
            w["final"][w["final"].index(old)] = item
        else:
            w["final"].append(item)
        print(f"Final list: {pair} {item['side']} from {' and '.join(NAMES[k] for k in item['from'])}.")
    save(w)


def cmd_show(a):
    w = load()
    print(w.get("label"), f"(week_of {w.get('week_of')}, updated {w.get('updated')})")
    for k in LISTS:
        print(f"\n{NAMES[k]}:")
        if not w[k]:
            print("  (empty)")
        for x in w[k]:
            z = ", ".join(f"{fp(x['pair'], z['lo'])}-{fp(x['pair'], z['hi'])}" + (f" {z['tf']}" if z.get("tf") else "") for z in x["aois"])
            print(f"  {x['pair']:7} {x['side']:4}  {z}" + (f"  | {x['note']}" if x.get("note") else ""))


def main():
    ap = argparse.ArgumentParser(description="Edit the week's lists in data/week.json.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new"); n.add_argument("--monday"); n.add_argument("--force", action="store_true"); n.set_defaults(fn=cmd_new)
    ad = sub.add_parser("add"); ad.add_argument("list", choices=LISTS); ad.add_argument("pair"); ad.add_argument("side")
    ad.add_argument("aoi", nargs="+"); ad.add_argument("--note"); ad.set_defaults(fn=cmd_add)
    r = sub.add_parser("remove"); r.add_argument("list", choices=LISTS); r.add_argument("pair"); r.set_defaults(fn=cmd_remove)
    f = sub.add_parser("final"); f.add_argument("pairs", nargs="+"); f.add_argument("--from", dest="src", choices=("yours", "claude"))
    f.set_defaults(fn=cmd_final)
    s = sub.add_parser("show"); s.set_defaults(fn=cmd_show)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
