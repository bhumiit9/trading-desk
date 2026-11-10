# Set & Forget Desk

A small installable web app that shows this week's Set & Forget setups, the trader's own AOIs and a notification log.

- `index.html`, `sw.js`, `manifest.webmanifest`: the app (add it to the iPhone Home Screen or the Mac Dock).
- `data/feed.json`: what the app shows. Updated by an hourly check.
- `data/outbox/`: one file per notification. A new file here makes the "Send notifications" workflow push it to the registered devices.
- `push/`: public keys and the encrypted device codes.
- `tools/check.py`: the hourly check (zone arrivals, prices, notes). `tools/push/send.mjs`: the sender.

The app never places trades. It is rule-based analysis, not financial advice.
