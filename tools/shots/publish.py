#!/usr/bin/env python3
"""
Publish fresh TradingView screenshots to the app.

The screenshots are taken through the owner's own Chrome (their TradingView layout) and saved as
files. This script turns them into the JPGs the app shows, adds the stop / entry / target tags on
the 4H, 1H and 30m charts, and pushes everything to the `shots` branch as ONE commit that replaces the
previous one, so the repository does not grow. The app reads shots.json from that branch.

Usage:  python3 tools/shots/publish.py spec.json
spec.json: {"at": "2026-11-11T17:40:00Z",            # when the screenshots were taken (UTC)
            "shots": [{"pair": "EURNZD", "tf": "4H", "file": "/path/shot.png",
                       # only needed for the tags (4H, 1H and 30m):
                       "from": 1.94, "to": 2.02,     # visible price range of the chart pane
                       "h": 842, "top": 42,          # pane height and top, CSS pixels
                       "axis_left": 1228,            # left edge of the price axis, CSS pixels
                       "k": 0.8538,                  # screenshot-frame pixels per CSS pixel
                       "region": [536, 36, 1107, 779]}]}   # captured region, frame pixels
"""
import json, os, shutil, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
RED, GRN, INK = (217, 58, 63), (18, 140, 80), (19, 26, 43)
TFS = ("W", "D", "4H", "1H", "30m")


def tags(im, s, setup):
    """Draw the plan's stop, entry and target on a 4H, 1H or 30m screenshot."""
    need = ("from", "to", "h", "k", "region")
    if not setup or any(k not in s for k in need) or not all(isinstance(setup.get(k), (int, float)) for k in ("entry", "stop", "target")):
        return im
    W, H = im.size
    x0, y0, x1, y1 = s["region"]
    sx, sy = W / (x1 - x0), H / (y1 - y0)
    top, k = s.get("top", 42), s["k"]
    Y = lambda p: ((top + (s["to"] - p) / (s["to"] - s["from"]) * s["h"]) * k - y0) * sy
    ax = (s.get("axis_left", 1228) * k - x0) * sx                 # where the price axis starts
    pane_bottom = ((top + s["h"]) * k - y0) * sy
    dp = 3 if setup["pair"].endswith("JPY") else 4
    rr = abs(setup["entry"] - setup["target"]) / max(1e-9, abs(setup["stop"] - setup["entry"]))
    sell = setup.get("side") != "buy"
    inside = lambda y: 8 <= y <= pane_bottom - 8
    im = im.convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = ImageFont.truetype(FONT, 21)

    def tag(y, text, col, above):
        """A thin dashed line across the chart with a small label at the left edge, clear of the newest candles."""
        if not inside(y):
            return
        x = 0
        while x < ax - 4:
            d.line([x, y, min(x + 12, ax - 4), y], fill=col + (235,), width=2)
            x += 20
        bb = d.textbbox((0, 0), text, font=f)
        w, h = bb[2] - bb[0] + 16, bb[3] - bb[1] + 12
        ty = y - h - 4 if above else y + 4
        d.rounded_rectangle([8, ty, 8 + w, ty + h], radius=6, fill=col + (240,))
        d.text((16, ty + 6 - bb[1]), text, font=f, fill=(255, 255, 255, 255))

    ys, ye, yt = Y(setup["stop"]), Y(setup["entry"]), Y(setup["target"])
    tag(ys, f"STOP {setup['stop']:.{dp}f}", RED, sell)
    tag(ye, f"{'SELL' if sell else 'BUY'} {setup['entry']:.{dp}f}", INK, not sell)
    tag(yt, f"TARGET {setup['target']:.{dp}f}  1:{rr:.1f}", GRN, not sell)
    return Image.alpha_composite(im, ov)


def git(args, cwd, check=True):
    return subprocess.run(["git"] + args, cwd=cwd, check=check, capture_output=True, text=True)


def main():
    spec = json.load(open(sys.argv[1]))
    feed = json.load(open(os.path.join(ROOT, "data", "feed.json")))
    setups = {s["pair"]: s for s in feed.get("setups", [])}
    origin = git(["remote", "get-url", "origin"], ROOT).stdout.strip()
    work = tempfile.mkdtemp(prefix="shots-")
    git(["init", "-q"], work)
    git(["remote", "add", "origin", origin], work)
    manifest = {"shots": {}}
    if git(["fetch", "-q", "--depth", "1", "origin", "shots"], work, check=False).returncode == 0:
        git(["checkout", "-q", "FETCH_HEAD", "--", "."], work)
        try:
            manifest = json.load(open(os.path.join(work, "shots.json")))
        except Exception:                       # noqa: BLE001
            manifest = {"shots": {}}
    done = []
    for s in spec["shots"]:
        pair, tf = s["pair"].upper(), s["tf"]
        if tf not in TFS:
            continue
        im = Image.open(s["file"]).convert("RGB")
        if tf in ("4H", "1H", "30m") and not s.get("plain"):
            im = tags(im, s, setups.get(pair)).convert("RGB")
        name = f"{pair.lower()}-{tf.lower()}.jpg"
        im.save(os.path.join(work, name), quality=84, optimize=True)
        manifest["shots"].setdefault(pair, {})[tf] = {"file": name, "at": s.get("at") or spec["at"]}
        done.append(f"{pair} {tf}")
    # drop pairs that are no longer trade cards
    live = {p for p, s in setups.items() if s.get("status") != "no_trade"}
    for p in list(manifest["shots"]):
        if p not in live:
            for v in manifest["shots"].pop(p).values():
                try:
                    os.remove(os.path.join(work, v["file"]))
                except OSError:
                    pass
    manifest["updated"] = spec["at"]
    json.dump(manifest, open(os.path.join(work, "shots.json"), "w"), indent=1)
    git(["add", "-A"], work)
    git(["-c", "user.name=Claude", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m", f"Chart screenshots {spec['at']}"], work)
    git(["push", "-q", "-f", "origin", "HEAD:refs/heads/shots"], work)
    shutil.rmtree(work, ignore_errors=True)
    print("published:", ", ".join(done))


if __name__ == "__main__":
    main()
