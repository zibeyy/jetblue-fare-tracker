"""Fetch and parse Google Flights results — including the "top flights".

fast-flights 3.0.2 has a bug that matters a lot for a price tracker: its
parser reads only payload[3][0] ("other flights") and silently drops
payload[2][0], Google's top/best-ranked section — which is usually where the
cheapest itinerary lives. This module reuses fast-flights' fetcher and models
but parses BOTH sections. Field indices mirror fast_flights.parser.parse_js
for the pinned version in requirements.txt; re-check them if that pin moves.
"""
from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from fast_flights import FlightsNotFound
from fast_flights.fetcher import fetch_flights_html
from fast_flights.model import (
    Airline,
    Airport,
    Alliance,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)
from fast_flights.parser import ResultList

import json

RESULT_SECTIONS = (2, 3)  # 2 = top/best flights, 3 = everything else


def _parse_itinerary(k) -> Flights:
    flight = k[0]
    price = k[1][0][1]
    legs = []
    for single in flight[2]:
        legs.append(SingleFlight(
            from_airport=Airport(code=single[3], name=single[4]),
            to_airport=Airport(code=single[6], name=single[5]),
            departure=SimpleDatetime(date=single[20], time=single[8]),
            arrival=SimpleDatetime(date=single[21], time=single[10]),
            duration=single[11],
            plane_type=single[17],
        ))
    extras = flight[22]
    return Flights(
        type=flight[0],
        price=price,
        airlines=flight[1],
        flights=legs,
        carbon=CarbonEmission(typical_on_route=extras[8], emission=extras[7]),
    )


def _itinerary_key(it: Flights):
    return (
        it.price,
        tuple(
            (leg.from_airport.code, tuple(leg.departure.date or ()),
             tuple(leg.departure.time or ()))
            for leg in it.flights
        ),
    )


def get_flights_full(query) -> ResultList:
    """Drop-in replacement for fast_flights.get_flights (default fetcher)."""
    html = fetch_flights_html(query)
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError("Google response had no ds:1 data script — "
                           "layout change or block page")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)

    meta = JsMetadata(
        alliances=[Alliance(code=c, name=n) for c, n in payload[7][1][0]],
        airlines=[Airline(code=c, name=n) for c, n in payload[7][1][1]],
    )

    results = ResultList()
    seen = set()
    skipped = 0
    for idx in RESULT_SECTIONS:
        section = payload[idx] if len(payload) > idx else None
        if not (isinstance(section, list) and section
                and isinstance(section[0], list)):
            continue
        for k in section[0]:
            try:
                itin = _parse_itinerary(k)
            except (IndexError, TypeError):
                skipped += 1  # one odd itinerary shouldn't kill the check
                continue
            key = _itinerary_key(itin)
            if key in seen:
                continue
            seen.add(key)
            results.append(itin)
    if skipped:
        print(f"    note: {skipped} itinerar{'y' if skipped == 1 else 'ies'} "
              f"could not be parsed and were skipped")
    results.metadata = meta
    return results
