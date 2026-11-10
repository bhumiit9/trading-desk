#!/usr/bin/env python3
"""
Save TradingView OHLCV tool results to disk so analyze.py can read them.

The TradingView MCP tool (mcp-tv-get-ohlcv) returns bars into the conversation,
not to a file. Claude Code stores every tool call + result in the session
transcript (~/.claude/projects/**/*.jsonl). This script scans recent
transcripts, pairs each get-ohlcv call (symbol, interval) with its result and
writes one merged file per symbol/timeframe:

    <out>/<EXCHANGE_TICKER>_<TF>.json   e.g. OANDA_EURUSD_D.json

Usage:
    python3 cache_ohlcv.py --out ./sf-data            # last 24h of transcripts
    python3 cache_ohlcv.py --out ./sf-data --hours 72
    python3 cache_ohlcv.py --out ./sf-data --file saved_result.json --symbol OANDA:EURUSD --interval 1D

If no transcript is available (other environments), save the tool's JSON
result to a file yourself and use --file/--symbol/--interval.
"""
import argparse, glob, json, os, re, sys, time

TF_MAP = {"1W": "W", "1D": "D", "4h": "4h", "4H": "4h", "2h": "2h", "1h": "1h", "1H": "1h",
          "30m": "30m", "15m": "15m", "5m": "5m", "1m": "1m"}


def norm_bars(bars):
    """Return dict of lists t,o,h,l,c from columns or rows format."""
    if isinstance(bars, dict) and "t" in bars:
        return {k: list(bars[k]) for k in ("t", "o", "h", "l", "c")}
    out = {k: [] for k in ("t", "o", "h", "l", "c")}
    if isinstance(bars, list):
        for b in bars:
            if isinstance(b, dict):
                for k in out:
                    out[k].append(b[k])
            elif isinstance(b, (list, tuple)) and len(b) >= 5:
                for k, v in zip(("t", "o", "h", "l", "c"), b[:5]):
                    out[k].append(v)
    return out


def parse_result_text(text):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # large results may be persisted to a file and only referenced
    m = re.search(r"(/[^\s\"']+tool-results/[^\s\"']+\.(?:txt|json))", text)
    if m and os.path.exists(m.group(1)):
        raw = open(m.group(1)).read()
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
            return obj
        except Exception:
            return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
        return obj
    except Exception:
        return None


def iso_to_epoch(s):
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def scan_transcripts(hours):
    root = os.path.expanduser("~/.claude/projects")
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    cutoff = time.time() - hours * 3600
    files = [f for f in files if os.path.getmtime(f) >= cutoff]
    calls, results = {}, []
    for f in sorted(files, key=os.path.getmtime):
        for line in open(f, errors="ignore"):
            if "get-ohlcv" not in line and "tool_result" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            ts = iso_to_epoch(d.get("timestamp", "")) or os.path.getmtime(f)
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and str(b.get("name", "")).endswith("get-ohlcv"):
                    calls[b["id"]] = b.get("input", {})
                elif b.get("type") == "tool_result":
                    results.append((b.get("tool_use_id"), b.get("content"), ts))
    found = []
    for tid, content, ts in results:
        if tid not in calls:
            continue
        inp = calls[tid]
        texts = []
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        for tx in texts:
            obj = parse_result_text(tx)
            if not obj or "bars" not in obj:
                continue
            sym = obj.get("symbol") or inp.get("symbol")
            itv = obj.get("interval") or inp.get("interval")
            found.append((sym, itv, norm_bars(obj["bars"]), ts))
    return found


def merge_write(out, sym, itv, bars, fetched_at):
    tf = TF_MAP.get(itv, itv)
    fn = os.path.join(out, f"{sym.replace(':', '_')}_{tf}.json")
    merged = {}
    old_fetch = 0
    if os.path.exists(fn):
        old = json.load(open(fn))
        old_fetch = old.get("fetched_at", 0)
        for i, t in enumerate(old["t"]):
            merged[t] = (old["o"][i], old["h"][i], old["l"][i], old["c"][i])
    for i, t in enumerate(bars["t"]):
        merged[t] = (bars["o"][i], bars["h"][i], bars["l"][i], bars["c"][i])
    ts = sorted(merged)
    data = {"symbol": sym, "tf": tf, "fetched_at": max(fetched_at, old_fetch),
            "t": ts, "o": [merged[t][0] for t in ts], "h": [merged[t][1] for t in ts],
            "l": [merged[t][2] for t in ts], "c": [merged[t][3] for t in ts]}
    json.dump(data, open(fn, "w"))
    return fn, len(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--file")
    ap.add_argument("--symbol")
    ap.add_argument("--interval")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.file:
        obj = parse_result_text(open(a.file).read())
        if not obj:
            sys.exit("could not parse " + a.file)
        sym = a.symbol or obj.get("symbol")
        itv = a.interval or obj.get("interval")
        fn, n = merge_write(a.out, sym, itv, norm_bars(obj["bars"]), os.path.getmtime(a.file))
        print(f"{fn}  bars={n}")
        return
    found = scan_transcripts(a.hours)
    if not found:
        sys.exit("No get-ohlcv results found in recent transcripts. Fetch data first, or use --file.")
    written = {}
    for sym, itv, bars, ts in found:  # chronological, later fetches overwrite overlapping bars
        fn, n = merge_write(a.out, sym, itv, bars, ts)
        written[fn] = n
    for fn, n in sorted(written.items()):
        print(f"{fn}  bars={n}")


if __name__ == "__main__":
    main()
