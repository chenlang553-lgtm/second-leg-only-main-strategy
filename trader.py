#!/usr/bin/env python3
"""Live Polymarket CLOB execution wrapper."""

import os

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderType
from py_clob_client.order_builder.constants import BUY


class LiveTrader(object):
    def __init__(
        self,
        host,
        chain_id,
        private_key,
        funder,
        signature_type=0,
        tick_size="0.01",
        neg_risk=False,
        creds=None,
    ):
        if creds is None:
            temp_client = ClobClient(host, key=private_key, chain_id=chain_id)
            creds = temp_client.create_or_derive_api_creds()
        self.client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            creds=creds,
            signature_type=signature_type,
            funder=funder,
        )
        self.tick_size = tick_size
        self.neg_risk = neg_risk

    @classmethod
    def from_env(cls):
        private_key = os.getenv("PRIVATE_KEY")
        funder = (
            os.getenv("FUNDER_ADDRESS")
            or os.getenv("POLY_FUNDER")
            or os.getenv("WALLET_ADDRESS")
        )
        if not private_key:
            raise ValueError("PRIVATE_KEY is required for live mode")
        if not funder:
            raise ValueError(
                "FUNDER_ADDRESS or POLY_FUNDER or WALLET_ADDRESS is required for live mode"
            )
        host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
        chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
        signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
        tick_size = os.getenv("POLY_TICK_SIZE", "0.01")
        neg_risk = os.getenv("POLY_NEG_RISK", "false").lower() == "true"
        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        api_passphrase = os.getenv("PASSPHRASE")
        creds = None
        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        return cls(
            host=host,
            chain_id=chain_id,
            private_key=private_key,
            funder=funder,
            signature_type=signature_type,
            tick_size=tick_size,
            neg_risk=neg_risk,
            creds=creds,
        )

    @staticmethod
    def derive_api_creds_from_env():
        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            raise ValueError("PRIVATE_KEY is required to derive API credentials")
        host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
        chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
        temp_client = ClobClient(host, key=private_key, chain_id=chain_id)
        return temp_client.create_or_derive_api_creds()

    def buy_market(self, token_id, price, amount=1.0, tif="FAK"):
        order_type = OrderType.FAK if tif == "FAK" else OrderType.FAK
        return self.client.create_and_post_market_order(
            token_id=token_id,
            side=BUY,
            amount=amount,
            price=price,
            options={
                "tick_size": self.tick_size,
                "neg_risk": self.neg_risk,
            },
            order_type=order_type,
        )
