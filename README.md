# Set & Forget Desk

A small installable web app: charts of the trader's pairs with their own AOIs drawn on them, plus trade notifications.

- `index.html`, `sw.js`, `manifest.webmanifest`: the app (add it to the iPhone Home Screen or the Mac Dock).
- `data/feed.json`: zones, this week's plan and the notification log. `data/bars/`: the candles the charts draw. Both are updated by an hourly check.
- `vendor/lightweight-charts.js`: TradingView Lightweight Charts (Apache 2.0).
- `data/outbox/`: one file per notification. A new file here makes the "Send notifications" workflow push it to the registered devices.
- `push/`: public keys and the encrypted device codes.
- `tools/check.py`: the hourly check (zone arrivals, prices, notes). `tools/push/send.mjs`: the sender.

The app never places trades. It is rule-based analysis, not financial advice.
