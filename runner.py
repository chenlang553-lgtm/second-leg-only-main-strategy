#!/usr/bin/env python3
"""Standalone runner for the second-leg-only main strategy."""

import argparse
import json
import sys
import time

from btc_follow import btc_5m_candidate_slugs, current_btc_5m_slug, is_btc_5m_slug, next_btc_5m_slug
from gamma import fetch_market_metadata_by_slug
from market_data import PolymarketMarketDataFeed
from strategy import Prices, Scores, SecondLegOnlyMainStrategy, Snapshot
from trader import LiveTrader


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Run the standalone second-leg-only main strategy against a "
            "JSONL snapshot stream."
        )
    )
    parser.add_argument(
        "--input",
        help="Path to a JSONL snapshot file.",
    )
    parser.add_argument(
        "--slug",
        help="Run live against a single Polymarket market slug.",
    )
    parser.add_argument(
        "--follow-btc-5m",
        action="store_true",
        help="Automatically follow the current and next BTC 5m markets.",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Continue after the first action instead of stopping immediately.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Submit real orders through py_clob_client.",
    )
    parser.add_argument(
        "--derive-creds",
        action="store_true",
        help="Derive and print API credentials from PRIVATE_KEY, then exit.",
    )
    return parser.parse_args(argv)


def load_snapshot(row):
    prices = row["prices"]
    scores = row["scores"]
    return Snapshot(
        now_ms=int(row["now_ms"]),
        time_to_expiry_sec=int(row["time_to_expiry_sec"]),
        prices=Prices(up=float(prices["up"]), down=float(prices["down"])),
        scores=Scores(up=float(scores["up"]), down=float(scores["down"])),
    )


def main(argv):
    args = parse_args(argv)
    if args.derive_creds:
        creds = LiveTrader.derive_api_creds_from_env()
        print(json.dumps({
            "api_key": creds.api_key,
            "api_secret": creds.api_secret,
            "api_passphrase": creds.api_passphrase,
        }, ensure_ascii=False))
        return 0

    strategy = SecondLegOnlyMainStrategy()
    trader = LiveTrader.from_env() if args.live else None

    def run_live_market(market_slug):
        market_strategy = SecondLegOnlyMainStrategy()
        last_status_second = None
        metadata = fetch_market_metadata_by_slug(market_slug)
        feed = PolymarketMarketDataFeed(
            market_slug=metadata["slug"],
            title=metadata["title"],
            up_token_id=metadata["up_token_id"],
            down_token_id=metadata["down_token_id"],
            end_time_ms=metadata["end_time_ms"],
        )
        feed.connect()
        try:
            while True:
                item = feed.queue.get()
                if isinstance(item, Exception):
                    raise item
                snapshot = item
                snapshot_second = snapshot.now_ms // 1000
                if snapshot_second != last_status_second:
                    last_status_second = snapshot_second
                    print(json.dumps({
                        "type": "status",
                        "market_slug": metadata["slug"],
                        "title": metadata["title"],
                        "now_ms": snapshot.now_ms,
                        "time_to_expiry_sec": snapshot.time_to_expiry_sec,
                        "prices": {
                            "up": round(snapshot.prices.up, 6),
                            "down": round(snapshot.prices.down, 6),
                        },
                        "scores": {
                            "up": round(snapshot.scores.up, 6),
                            "down": round(snapshot.scores.down, 6),
                        },
                    }, ensure_ascii=False))
                action = market_strategy.on_snapshot(snapshot)
                if action is not None:
                    payload = {
                        "type": "order_action",
                        "market_slug": metadata["slug"],
                        "side": action.side,
                        "qty": action.qty,
                        "tif": action.tif,
                        "role": action.role,
                        "limit_price": action.limit_price,
                        "reason": action.reason,
                        "now_ms": snapshot.now_ms,
                        "time_to_expiry_sec": snapshot.time_to_expiry_sec,
                    }
                    if args.live:
                        token_id = metadata["up_token_id"] if action.side == "Up" else metadata["down_token_id"]
                        amount = 1.0
                        slippage_price = 0.6
                        payload["execution"] = trader.buy_market(
                            token_id=token_id,
                            price=slippage_price,
                            amount=amount,
                            tif="FAK",
                        )
                        payload["amount"] = amount
                        payload["execution_price"] = slippage_price
                    print(json.dumps(payload, ensure_ascii=False))
                    market_strategy.mark_second_leg_filled(
                        side=action.side,
                        price=action.limit_price,
                        qty=action.qty,
                    )
                    if not args.keep_running:
                        return metadata["slug"]
                if snapshot.time_to_expiry_sec <= 0:
                    return metadata["slug"]
        finally:
            feed.close()

    def resolve_initial_market_slug():
        if args.slug:
            return args.slug
        last_error = None
        for slug in btc_5m_candidate_slugs():
            try:
                fetch_market_metadata_by_slug(slug)
                return slug
            except Exception as exc:
                last_error = exc
        raise RuntimeError(
            "Unable to resolve current BTC 5m slug from candidates {}: {}".format(
                ", ".join(btc_5m_candidate_slugs()),
                last_error,
            )
        )

    if args.slug or args.follow_btc_5m:
        market_slug = resolve_initial_market_slug()
        if args.follow_btc_5m and not args.slug:
            print(
                json.dumps(
                    {
                        "type": "startup",
                        "message": "auto-selected current BTC 5m slug",
                        "market_slug": market_slug,
                        "current_time_base": current_btc_5m_slug(),
                    },
                    ensure_ascii=False,
                )
            )
        for _ in iter(int, 1):
            completed_slug = run_live_market(market_slug)
            if not args.follow_btc_5m:
                return 0
            if not is_btc_5m_slug(completed_slug):
                return 0
            next_slug = next_btc_5m_slug(completed_slug)
            if next_slug is None:
                return 0
            print(
                json.dumps(
                    {
                        "type": "rollover",
                        "from_slug": completed_slug,
                        "to_slug": next_slug,
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                try:
                    fetch_market_metadata_by_slug(next_slug)
                    market_slug = next_slug
                    break
                except Exception:
                    time.sleep(1)
        return 0

    if not args.input:
        raise ValueError("either --input or --slug is required")

    with open(args.input, "r") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            row = json.loads(raw_line)
            snapshot = load_snapshot(row)
            action = strategy.on_snapshot(snapshot)
            if action is None:
                continue

            payload = {
                "type": "order_action",
                "side": action.side,
                "qty": action.qty,
                "tif": action.tif,
                "role": action.role,
                "limit_price": action.limit_price,
                "reason": action.reason,
                "now_ms": snapshot.now_ms,
                "time_to_expiry_sec": snapshot.time_to_expiry_sec,
            }
            if args.live:
                token_ids = row.get("token_ids") or {}
                token_id = token_ids.get(action.side.lower())
                if not token_id:
                    raise ValueError(
                        "live mode requires token_ids.up/down in each snapshot row"
                    )
                amount = 1.0
                slippage_price = 0.6
                response = trader.buy_market(
                    token_id=token_id,
                    price=slippage_price,
                    amount=amount,
                    tif="FAK",
                )
                payload["execution"] = response
                payload["amount"] = amount
                payload["execution_price"] = slippage_price

            print(json.dumps(payload, ensure_ascii=False))

            strategy.mark_second_leg_filled(
                side=action.side,
                price=action.limit_price,
                qty=action.qty,
            )
            if not args.keep_running:
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
