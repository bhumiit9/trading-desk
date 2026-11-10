# Set & Forget Desk

A small installable web app: this week's trades as TradingView screenshots with entry, stop and target marked, plus trade notifications.

- `index.html`, `sw.js`, `manifest.webmanifest`: the app (add it to the iPhone Home Screen or the Mac Dock).
- `charts/`: one annotated screenshot per trade. `data/feed.json`: zones, this week's plan and the notification log. `data/bars/`: recent candles for the hourly check.
- `data/outbox/`: one file per notification. A new file here makes the "Send notifications" workflow push it to the registered devices.
- `push/`: public keys and the encrypted device codes.
- `tools/check.py`: the hourly check (zone arrivals, prices, notes). `tools/push/send.mjs`: the sender.

The app never places trades. It is rule-based analysis, not financial advice.
