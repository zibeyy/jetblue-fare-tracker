#!/usr/bin/env python3
"""JetBlue fare tracker.

Queries Google Flights (via fast-flights) for the routes/dates in config.json,
appends every observed fare to data/history.csv, and pushes an ntfy.sh
notification when the cheapest fare drops to alert.price_target_usd or below.

Google Flights lists each itinerary at its *cheapest* fare, which for JetBlue
is Blue Basic — that is the price tracked and alerted on.

Runs on a schedule from .github/workflows/track.yml; also runnable locally:
    .venv/Scripts/python tracker.py
Environment:
    NTFY_TOPIC  overrides config.json ntfy_topic (used as a GitHub secret)
    TEST_ALERT  set to "1" to send a test notification and continue
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
from fast_flights import FlightQuery, FlightsNotFound, create_query, get_flights

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.csv"
STATE_PATH = DATA_DIR / "alert_state.json"

CSV_HEADER = [
    "run_ts_utc", "direction", "travel_date", "airline", "stops",
    "depart_local", "arrive_local", "duration_min", "plane", "price_usd",
]

DEFAULT_ALERT = {
    "price_target_usd": 200,
    "cooldown_hours": 20,
    "realert_extra_drop_usd": 10,
}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("airline_code", "B6")
    cfg.setdefault("nonstop_only", True)
    alert = {**DEFAULT_ALERT, **cfg.get("alert", {})}
    if "price_target_usd" not in cfg.get("alert", {}) and "blue_target_usd" in alert:
        # legacy schema: target was on estimated Blue = Basic + upcharge
        alert["price_target_usd"] = (alert["blue_target_usd"]
                                     - alert.get("blue_upcharge_estimate_usd", 35))
        print(f"note: legacy alert keys converted -> "
              f"price_target_usd ${alert['price_target_usd']}")
    cfg["alert"] = alert
    if not cfg.get("watches"):
        sys.exit("config.json has no 'watches' — nothing to track.")
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def fmt_leg_dt(simple_dt) -> str:
    """SimpleDatetime -> 'YYYY-MM-DDTHH:MM'. Minutes are omitted upstream when :00."""
    d = list(simple_dt.date)
    t = list(simple_dt.time)
    hh = t[0] if t else 0
    mm = t[1] if len(t) > 1 else 0
    return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}T{hh:02d}:{mm:02d}"


def fetch_itineraries(cfg: dict, origin: str, dest: str, date: str):
    """One route+date query with retries. Returns a ResultList, or None if
    Google reports no matching flights (a valid answer, not an error)."""
    query = create_query(
        flights=[FlightQuery(
            date=date,
            from_airport=origin,
            to_airport=dest,
            max_stops=0 if cfg["nonstop_only"] else None,
            airlines=[cfg["airline_code"]],
        )],
        trip="one-way",
        seat="economy",
        currency="USD",
    )
    last_err: Exception | None = None
    for attempt, pause in enumerate((0, 15, 45), start=1):
        if pause:
            time.sleep(pause)
        try:
            return get_flights(query)
        except FlightsNotFound:
            return None
        except Exception as err:  # network hiccups, parse breaks, blocks
            last_err = err
            print(f"    attempt {attempt} failed: {err!r}")
    raise last_err  # all retries exhausted


def airline_name(result, code: str) -> str:
    for airline in result.metadata.airlines:
        if airline.code == code:
            return airline.name
    return code


def append_history(rows: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    is_new = not HISTORY_PATH.exists() or HISTORY_PATH.stat().st_size == 0
    with open(HISTORY_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def gflights_url(origin: str, dest: str, date: str, airline: str) -> str:
    q = f"Flights from {origin} to {dest} on {date} nonstop with {airline}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def send_ntfy(topic: str, title: str, message: str,
              click: str | None = None, priority: int = 4) -> None:
    payload: dict = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": ["airplane", "money_with_wings"],
    }
    if click:
        payload["click"] = click
    resp = requests.post("https://ntfy.sh/", json=payload, timeout=30)
    resp.raise_for_status()


def maybe_alert(cfg: dict, state: dict, key: str, origin: str, dest: str,
                travel_date: str, cheapest: dict, all_prices: list[int],
                airline: str, topic: str) -> None:
    alert_cfg = cfg["alert"]
    target = alert_cfg["price_target_usd"]
    price = cheapest["price_usd"]

    if price > target:
        return

    prior = state.get(key)
    if prior is not None:
        hours_since = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(prior["last_alert_ts"])
        ).total_seconds() / 3600
        within_cooldown = hours_since < alert_cfg["cooldown_hours"]
        big_further_drop = (
            price <= prior["last_alert_price"] - alert_cfg["realert_extra_drop_usd"]
        )
        if within_cooldown and not big_further_drop:
            print(f"    below threshold (${price}) but alerted "
                  f"{hours_since:.1f}h ago — suppressed")
            return

    dep = cheapest["depart_local"][11:]
    hours_min = f"{cheapest['duration_min'] // 60}h{cheapest['duration_min'] % 60:02d}m"
    title = f"{airline} {origin}->{dest} {travel_date}: ${price}"
    message = (
        f"Blue Basic ${price} — at or below your ${target} target\n"
        f"Cheapest: departs {dep}, {hours_min}, "
        f"{cheapest['stops']} stop(s)\n"
        f"All fares this check: {', '.join(f'${p}' for p in sorted(all_prices))}\n"
        f"Prices move — book on jetblue.com or Google Flights."
    )
    click = gflights_url(origin, dest, travel_date, airline)

    if not topic:
        print(f"    WOULD ALERT (no ntfy topic configured): {title}")
        return
    try:
        send_ntfy(topic, title, message, click=click, priority=4)
    except Exception as err:
        print(f"    ALERT SEND FAILED (will retry next run): {err!r}")
        return
    state[key] = {
        "last_alert_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_alert_price": price,
    }
    print(f"    ALERT SENT: {title}")


def main() -> int:
    cfg = load_config()
    state = load_state()
    topic = os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic") or ""
    run_ts = datetime.now(timezone.utc).isoformat(timespec="minutes")
    today = datetime.now(timezone.utc).date().isoformat()

    if os.environ.get("TEST_ALERT") == "1":
        if not topic:
            sys.exit("TEST_ALERT requested but no NTFY_TOPIC secret / "
                     "ntfy_topic config value is set.")
        send_ntfy(topic, "Fare tracker test",
                  "Notifications are wired up correctly. This is what a fare "
                  "alert will look like.", priority=4)
        print("Test notification sent.")

    watch_list = [
        (w["from"].upper(), w["to"].upper(), d)
        for w in cfg["watches"] for d in w["dates"]
    ]
    active_keys = {f"{o}-{d}|{dt}" for o, d, dt in watch_list}
    for stale in [k for k in state if k not in active_keys]:
        del state[stale]

    rows: list[dict] = []
    queries_ok = 0
    queries_failed = 0
    summary: list[str] = []

    for origin, dest, travel_date in watch_list:
        key = f"{origin}-{dest}|{travel_date}"
        print(f"[{origin} -> {dest} on {travel_date}]")
        if travel_date <= today:
            print("    date is today/past — skipping (update config.json)")
            continue

        try:
            result = fetch_itineraries(cfg, origin, dest, travel_date)
        except Exception as err:
            queries_failed += 1
            print(f"    FAILED after retries: {err!r}")
            continue
        queries_ok += 1

        if result is None or len(result) == 0:
            print("    no matching flights returned")
            summary.append(f"{key}: no flights")
            continue

        airline = airline_name(result, cfg["airline_code"])
        route_rows = []
        for itin in result:
            if not itin.price or itin.price <= 0:
                continue
            legs = itin.flights
            route_rows.append({
                "run_ts_utc": run_ts,
                "direction": f"{origin}-{dest}",
                "travel_date": travel_date,
                "airline": ", ".join(itin.airlines),
                "stops": len(legs) - 1,
                "depart_local": fmt_leg_dt(legs[0].departure),
                "arrive_local": fmt_leg_dt(legs[-1].arrival),
                "duration_min": sum(leg.duration for leg in legs),
                "plane": ", ".join(leg.plane_type for leg in legs),
                "price_usd": itin.price,
            })
        if not route_rows:
            print("    itineraries returned but none had a usable price")
            summary.append(f"{key}: no priced fares")
            continue

        rows.extend(route_rows)
        prices = [r["price_usd"] for r in route_rows]
        cheapest = min(route_rows, key=lambda r: r["price_usd"])
        summary.append(f"{key}: cheapest ${cheapest['price_usd']} "
                       f"({len(route_rows)} fares)")
        print(f"    {len(route_rows)} fares, cheapest ${cheapest['price_usd']}")

        maybe_alert(cfg, state, key, origin, dest, travel_date,
                    cheapest, prices, airline, topic)
        time.sleep(3)  # be polite between queries

    if rows:
        append_history(rows)
    save_state(state)

    print("\n=== Run summary ===")
    for line in summary:
        print(" ", line)
    print(f"queries ok: {queries_ok}, failed: {queries_failed}, "
          f"rows appended: {len(rows)}")

    if queries_ok == 0 and queries_failed > 0:
        print("Every query failed — the Google Flights scrape is likely "
              "broken or blocked. Check for a fast-flights update.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
