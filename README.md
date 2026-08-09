# JetBlue BOS ⇄ LAX fare tracker

Tracks JetBlue fares for specific travel dates via Google Flights, keeps a
price history, pushes a phone notification when the **Blue** fare is estimated
at or below your target, and charts the history on a GitHub Pages dashboard.

**Cost: $0.** No server, no credit card. A GitHub Actions cron job does the
checking 4× a day; ntfy.sh delivers the alerts; GitHub Pages hosts the chart.

```
GitHub Actions (cron, 4x/day)
  └─ tracker.py ── queries Google Flights (fast-flights, JetBlue nonstops)
       ├─ appends fares  → data/history.csv   (committed back to the repo)
       ├─ alert decision → data/alert_state.json
       └─ push alert     → ntfy.sh → your phone
index.html (GitHub Pages) ── charts data/history.csv
```

## Important: what price this tracks

Google Flights lists each flight at its *cheapest* fare — for JetBlue that is
**Blue Basic**. The Blue fare you actually want is estimated as
`price + blue_upcharge_estimate_usd` (default **$35**). An alert fires when
`Blue Basic + upcharge ≤ blue_target_usd` (default **$200**), i.e. when Blue
Basic drops to **$165**. Check jetblue.com once for your dates, note the real
Basic→Blue difference, and adjust `blue_upcharge_estimate_usd` in
[config.json](config.json). Always verify the exact Blue price on jetblue.com
before booking.

## One-time setup (~10 minutes)

Prerequisites: a GitHub account, git, and the [GitHub CLI](https://cli.github.com)
(`gh`). Python is only needed to run the tracker locally.

### 1. Edit `config.json`

Set your real travel dates (the shipped ones are placeholders):

```json
"watches": [
  { "from": "BOS", "to": "LAX", "dates": ["2026-11-13", "2026-11-20"] },
  { "from": "LAX", "to": "BOS", "dates": ["2026-11-16", "2026-11-23"] }
]
```

Each date is tracked separately (its own line on the chart, its own alerts).
Keep it under ~8 dates total so every query fits comfortably in a run.

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

- **Alerts** arrive on your phone only when the estimated Blue price crosses
  your target (with a 20 h cooldown per date; a further $10 drop re-alerts
  immediately). Tapping the alert opens Google Flights for that route/date.
- **Change dates or thresholds** any time by editing `config.json` on
  github.com directly — the next run picks it up. Past dates are skipped
  automatically.
- **The chart** shows the cheapest fare per check, one line per route+date,
  with the alert threshold drawn as a dashed reference line.

## Tuning (all in `config.json`)

| Key | Default | Meaning |
|---|---|---|
| `alert.blue_target_usd` | 200 | Alert when estimated Blue ≤ this |
| `alert.blue_upcharge_estimate_usd` | 35 | Your calibrated Basic→Blue difference |
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
- **"Scheduled workflow disabled" email:** GitHub pauses crons after ~60 days
  without repo activity. The bot's own data commits normally prevent this;
  if it happens, one click re-enables it.
- **No alert but the price looks low:** the alert compares *estimated Blue*
  (Basic + upcharge) to the target — check the math and the cooldown, and see
  the Actions run log, which prints every decision.
- **Occasional missing data points:** a failed query in one run is retried
  3× and then skipped; the chart just won't have a point for that check.
- Google Flights has no official API and scraping is against its ToS; at a
  few queries a day the practical risk is a blocked runner IP (shows up as
  failed queries), not anything else. Be a good citizen: don't crank the
  schedule to every few minutes.

## Running locally (optional)

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

```bash
.venv/Scripts/python tracker.py
```

```bash
python -m http.server 8000
```

Then open http://localhost:8000 for the dashboard. A local `NTFY_TOPIC` env
var (or the `ntfy_topic` key in config) enables real alerts locally; leave
both unset for a dry run that prints would-be alerts.
