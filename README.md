# Set & Forget Desk

A small installable web app for the week's watch list: your picks, Claude's picks and the final agreed list, each pair with its AOIs, charts drawn from prices (No gaps candles, AOIs marked, no stop, entry or target lines), plus phone alerts for the final list.

- `index.html`, `sw.js`, `manifest.webmanifest`: the app (add it to the iPhone Home Screen or the Mac Dock).
- `data/week.json`: the week's lists (`yours`, `claude`, `final`), each pair with its side and AOI levels. Edit it with `tools/week.py` (see below). Past weeks are kept in `data/weeks/`.
- `data/feed.json`: the watch state of the final list and the notification log. `data/bars/`: hourly candles, rolled up into 4H, daily and weekly (the app draws its charts from these).
- `charts/`: older annotated screenshots, no longer shown.
- `.github/workflows/keeper.yml`: the hourly timer. GitHub's own schedule skips most slots, so this workflow waits for each slot itself (:32 past the hour, 9:32 AM to 9:32 PM India time, Monday to Friday) and starts the hourly check.
- `.github/workflows/check.yml`: the hourly check. It reads prices (`tools/fetch_prices.py`), checks the final list's pairs against their AOIs (`tools/check.py`), saves the result and sends the notifications.
- `data/outbox/`: one file per notification. A new file here makes the "Send notifications" workflow push it to the registered devices.
- `push/`: public keys and the encrypted device codes.
- `tools/check.py`: the hourly check. It watches only the final list and alerts when price reaches an AOI and when a closed 1H entry candle forms after a 1H shift of structure at the AOI (`tools/entry.py`). A shift alone shows in the app without a phone alert. A morning update lists where each pair stands. `tools/push/send.mjs`: the sender.

Sunday, filling the lists:

```
python3 tools/week.py new                                   # start next week's lists
python3 tools/week.py add yours  USDCHF buy 0.8166-0.8210:D
python3 tools/week.py add claude EURNZD sell 2.0041-2.0110:D --note "4 daily touches"
python3 tools/week.py final USDCHF EURNZD                   # copy agreed pairs to the final list
python3 tools/week.py show
```

The app never places trades. It is rule-based analysis, not financial advice.
