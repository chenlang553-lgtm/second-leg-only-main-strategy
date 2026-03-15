#!/usr/bin/env python3
"""Standalone Polymarket websocket market data adapter."""

import json
import queue
import threading
import time

from strategy import Prices, Scores, Snapshot

try:
    import websocket
except ImportError:
    websocket = None


def _to_levels(levels):
    out = []
    for level in levels or []:
        try:
            price = float(level["price"])
            size = float(level["size"])
        except Exception:
            continue
        if 0 <= price <= 1 and size > 0:
            out.append({"price": price, "size": size})
    return out


def _best_bid(levels):
    return max((level["price"] for level in levels), default=None)


def _best_ask(levels):
    return min((level["price"] for level in levels), default=None)


def _midpoint(book):
    best_bid = book.get("bestBid")
    best_ask = book.get("bestAsk")
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    if book.get("lastTradePrice") is not None:
        return book["lastTradePrice"]
    if best_bid is not None:
        return best_bid
    if best_ask is not None:
        return best_ask
    return None


class PolymarketMarketDataFeed(object):
    def __init__(self, market_slug, title, up_token_id, down_token_id, end_time_ms, ws_url=None):
        if websocket is None:
            raise RuntimeError("websocket-client is required for live market data")
        self.market_slug = market_slug
        self.title = title
        self.up_token_id = up_token_id
        self.down_token_id = down_token_id
        self.end_time_ms = end_time_ms
        self.ws_url = ws_url or "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.books = {}
        self.queue = queue.Queue()
        self._ws_app = None
        self._thread = None

    def _on_open(self, ws_app):
        ws_app.send(json.dumps({
            "type": "market",
            "assets_ids": [self.up_token_id, self.down_token_id],
            "custom_feature_enabled": True,
        }))

    def _on_message(self, ws_app, raw_message):
        payload = json.loads(raw_message)
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if message.get("event_type") != "book":
                continue
            asset_id = message["asset_id"]
            bids = _to_levels(message.get("bids"))
            asks = _to_levels(message.get("asks"))
            self.books[asset_id] = {
                "bids": bids,
                "asks": asks,
                "bestBid": _best_bid(bids),
                "bestAsk": _best_ask(asks),
                "lastTradePrice": float(message["last_trade_price"]) if message.get("last_trade_price") else None,
            }
        snapshot = self.build_snapshot()
        if snapshot is not None:
            self.queue.put(snapshot)

    def _on_error(self, ws_app, error):
        self.queue.put(error)

    def build_snapshot(self):
        up_book = self.books.get(self.up_token_id)
        down_book = self.books.get(self.down_token_id)
        if not up_book or not down_book:
            return None
        up_mid = _midpoint(up_book)
        down_mid = _midpoint(down_book)
        if up_mid is None or down_mid is None:
            return None
        total = up_mid + down_mid
        scores = Scores(
            up=(up_mid / total) if total > 0 else 0.5,
            down=(down_mid / total) if total > 0 else 0.5,
        )
        now_ms = int(time.time() * 1000)
        return Snapshot(
            now_ms=now_ms,
            time_to_expiry_sec=max(0, int((self.end_time_ms - now_ms) / 1000)),
            prices=Prices(up=up_mid, down=down_mid),
            scores=scores,
            books={"up": up_book, "down": down_book},
        )

    def connect(self):
        self._ws_app = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
        )
        self._thread = threading.Thread(target=self._ws_app.run_forever, daemon=True)
        self._thread.start()

    def close(self):
        if self._ws_app:
            self._ws_app.close()
