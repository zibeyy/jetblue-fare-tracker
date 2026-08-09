# JetBlue BOS ⇄ LAX fare tracker

Tracks JetBlue **Blue Basic** fares for specific travel dates via Google
Flights, keeps a price history, pushes a phone notification when a fare drops
to your target, and charts everything on a dashboard.

**Cost: $0.** No cloud server, no credit card. A GitHub Actions cron job does
the checking 4× a day; ntfy.sh delivers the alerts; GitHub Pages hosts the
dashboard read-only, and a local control panel manages it.

```
GitHub Actions (cron, 4x/day)
  └─ tracker.py ── queries Google Flights (fast-flights, JetBlue nonstops)
       ├─ appends fares  → data/history.csv   (committed back to the repo)
       ├─ alert decision → data/alert_state.json
       └─ push alert     → ntfy.sh → your phone
index.html ── the dashboard, two flavors:
  · GitHub Pages: read-only charts, from anywhere
  · locally via serve.py: same page plus the control panel —
    edit watched dates/target, run a check now, sync to GitHub
```

Prices tracked are **Blue Basic** — the cheapest fare Google Flights lists per
flight. (Blue and other bundles cost more at checkout; this tracker doesn't
estimate them.)

## One-time setup (~10 minutes)

Prerequisites: a GitHub account, git, and the [GitHub CLI](https://cli.github.com)
(`gh`). Python is only needed to run the tracker locally.

### 1. Set your travel dates

Easiest way — the control panel:

```bash
.venv/Scripts/python serve.py
```

Open http://localhost:8000, use **Manage watches** to add/remove dates and set
your alert price, then **Save changes**. (Or edit the `watches` list in
[config.json](config.json) by hand — same thing.)

Each date is tracked separately (its own card and chart line, its own alerts).
Keep it to ~8 dates so every query fits comfortably in a run.

### 2. Set up phone notifications (ntfy)

1. Install the **ntfy** app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)).
2. In the app, **Subscribe to topic** and enter a topic name. The topic name
   is effectively a password — anyone who knows it can read and send your
   alerts — so use something long and random, and don't commit it to the repo.

### 3. Create the GitHub repo and push

From this folder:

```bash
gh auth login
```

```bash
git init -b main && git add -A && git commit -m "JetBlue fare tracker"
```

```bash
gh repo create jetblue-fare-tracker --public --source . --push
```

(Public keeps Actions unlimited and GitHub Pages free. The only thing that
must stay secret is the ntfy topic, which lives in a repo secret, not in code.)

### 4. Add the ntfy topic as a secret

```bash
gh secret set NTFY_TOPIC --body "your-topic-name-here"
```

### 5. Enable GitHub Pages

```bash
gh api repos/{owner}/jetblue-fare-tracker/pages -X POST -f "source[branch]=main" -f "source[path]=/"
```

(Or in the web UI: repo → Settings → Pages → Deploy from a branch → `main` / root.)

Your dashboard will be at `https://<your-username>.github.io/jetblue-fare-tracker/`
a minute or two after the first data commit.

### 6. Test it end to end

```bash
gh workflow run track.yml -f test_alert=true
```

Within a minute or two: a **test notification** arrives on your phone, the run
appears under the repo's **Actions** tab, and a `fares: …` commit lands in
`data/`. If the test notification doesn't arrive, re-check the secret and your
ntfy subscription spelling.

That's it. It now checks fares at 02:37, 08:37, 14:37, 20:37 UTC daily.

## Day-to-day

- **Alerts** arrive on your phone when a fare drops to your target or below
  (20 h cooldown per date; a further $10 drop re-alerts immediately). Tapping
  the alert opens Google Flights for that route/date.
- **Change dates or the target** from the control panel: run
  `.venv/Scripts/python serve.py`, edit in **Manage watches**, **Save**, then
  **Sync to GitHub** so the schedule follows the new list. (Editing
  `config.json` on github.com works too.) Past dates are skipped automatically.
- **Check fares now** on the panel runs a real check immediately — handy right
  after adding dates. **Sync** also uploads these manual checks so the Pages
  chart includes them.
- **The dashboard** shows a card per tracked date (current price, change,
  low/high, trend) and the full history chart with the alert line.

## Tuning (all in `config.json`)

| Key | Default | Meaning |
|---|---|---|
| `alert.price_target_usd` | 200 | Alert when the fare is ≤ this (also editable on the panel) |
| `alert.cooldown_hours` | 20 | Minimum gap between alerts per date |
| `alert.realert_extra_drop_usd` | 10 | A further drop this big re-alerts despite cooldown |
| `nonstop_only` | true | Set false to include connections |
| `airline_code` | B6 | Track a different airline entirely |

## Maintenance & troubleshooting

- **A red ✗ on a scheduled run / "every query failed":** Google changed
  something and the scraper broke — this is expected occasionally. Check
  [fast-flights](https://github.com/AWeirdDev/flights) for a new release and
  bump the pin in `requirements.txt`. GitHub emails you when a scheduled run
  fails, so silence means it's working.
- **About `flightdata.py`:** fast-flights 3.0.2 drops Google's "top flights"
  section — usually the cheapest itinerary — so this repo parses the raw
  payload itself (both sections) and only borrows fast-flights' fetcher and
  models. If you bump the fast-flights pin, confirm upstream fixed this
  (their `parse_js` must read `payload[2][0]` as well as `payload[3][0]`)
  or keep `flightdata.py` as is.
- **"Scheduled workflow disabled" email:** GitHub pauses crons after ~60 days
  without repo activity. The bot's own data commits normally prevent this;
  if it happens, one click re-enables it.
- **No alert but the price looks low:** check the cooldown settings, and see
  the Actions run log — it prints every alert decision.
- **Occasional missing data points:** a failed query in one run is retried
  3× and then skipped; the chart just won't have a point for that check.
- Google Flights has no official API and scraping is against its ToS; at a
  few queries a day the practical risk is a blocked runner IP (shows up as
  failed queries), not anything else. Be a good citizen: don't crank the
  schedule to every few minutes.

## Running locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

```bash
.venv/Scripts/python serve.py
```

Then open http://localhost:8000 — the dashboard plus the control panel
(manage watches, check now, sync). `serve.py` binds to 127.0.0.1 only.
`.venv/Scripts/python tracker.py` still works standalone for a one-off check.
A local `NTFY_TOPIC` env var (or the `ntfy_topic` config key) enables real
alerts locally; leave both unset and local runs just print would-be alerts.
