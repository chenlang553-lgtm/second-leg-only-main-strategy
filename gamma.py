#!/usr/bin/env python3
"""Gamma API helpers for standalone market metadata."""

import json
import urllib.parse
import urllib.request
from datetime import datetime


def _fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "second-leg-only-main-strategy/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_time_ms(value):
    if not value:
        raise RuntimeError("missing datetime value")
    normalized = str(value).replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


def fetch_market_metadata_by_slug(slug):
    url = "https://gamma-api.polymarket.com/events?slug={}".format(
        urllib.parse.quote(slug)
    )
    payload = _fetch_json(url)
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("No event returned for slug {}".format(slug))
    event = payload[0]
    markets = event.get("markets") or []
    market = None
    for entry in markets:
        if entry.get("acceptingOrders") and entry.get("active") and not entry.get("closed"):
            market = entry
            break
    if market is None:
        for entry in markets:
            if entry.get("active") and not entry.get("closed"):
                market = entry
                break
    if market is None and markets:
        market = markets[0]
    if market is None:
        raise RuntimeError("No market found inside event {}".format(slug))

    outcomes = json.loads(market["outcomes"])
    token_ids = json.loads(market["clobTokenIds"])
    outcome_map = {}
    for idx, name in enumerate(outcomes):
        outcome_map[name] = token_ids[idx]

    return {
        "slug": slug,
        "title": market.get("question") or event.get("title"),
        "end_time_ms": _parse_time_ms(market.get("endDate") or event.get("endDate")),
        "neg_risk": bool(market.get("negRisk")),
        "tick_size": str(market.get("orderPriceMinTickSize") or "0.01"),
        "min_order_size": int(market.get("orderMinSize") or 5),
        "up_token_id": outcome_map.get("Up") or outcome_map.get("Yes") or token_ids[0],
        "down_token_id": outcome_map.get("Down") or outcome_map.get("No") or token_ids[1],
    }
