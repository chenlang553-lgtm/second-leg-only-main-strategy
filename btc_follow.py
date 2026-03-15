#!/usr/bin/env python3
"""Helpers for Polymarket BTC 5m slug rotation."""

import time


def parse_window_start_from_slug(slug):
    parts = str(slug).split("-")
    if len(parts) < 1:
        return None
    try:
        return int(parts[-1])
    except (TypeError, ValueError):
        return None


def current_btc_5m_start_ts(now_ms=None):
    now_ms = int(now_ms or time.time() * 1000)
    now_sec = now_ms // 1000
    return now_sec - (now_sec % 300)


def btc_5m_slug_from_start_ts(start_ts):
    return "btc-updown-5m-{}".format(int(start_ts))


def current_btc_5m_slug(now_ms=None):
    return btc_5m_slug_from_start_ts(current_btc_5m_start_ts(now_ms))


def next_btc_5m_slug(current_slug):
    start = parse_window_start_from_slug(current_slug)
    if start is None:
        return None
    return btc_5m_slug_from_start_ts(start + 300)


def is_btc_5m_slug(slug):
    return str(slug).startswith("btc-updown-5m-") and parse_window_start_from_slug(slug) is not None


def btc_5m_candidate_slugs(now_ms=None):
    current_start = current_btc_5m_start_ts(now_ms)
    return [
        btc_5m_slug_from_start_ts(current_start),
        btc_5m_slug_from_start_ts(current_start - 300),
        btc_5m_slug_from_start_ts(current_start + 300),
    ]
