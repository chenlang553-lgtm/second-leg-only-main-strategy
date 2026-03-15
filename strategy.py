#!/usr/bin/env python3
"""Standalone second-leg-only variant of the v18 main strategy."""

from dataclasses import dataclass
from typing import Dict, Optional


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def round_down(value, step):
    if step <= 0:
        return value
    return int(value / step) * step


def clip_order_qty(qty, min_order_size):
    if qty is None or qty <= 0:
        return 0
    return round_down(qty, min_order_size)


def opposite_side(side):
    return "Down" if side == "Up" else "Up"


def normalized_direction(prices, scores, score_flip_threshold):
    up_signal = scores.up - scores.down
    if up_signal >= score_flip_threshold:
        return "Up"
    if up_signal <= -score_flip_threshold:
        return "Down"
    if prices.up > prices.down:
        return "Up"
    if prices.down > prices.up:
        return "Down"
    return "Neutral"


@dataclass
class Prices:
    up: float
    down: float


@dataclass
class Scores:
    up: float
    down: float


@dataclass
class Snapshot:
    now_ms: int
    time_to_expiry_sec: int
    prices: Prices
    scores: Scores
    books: Optional[Dict[str, object]] = None


@dataclass
class Portfolio:
    up_qty: float = 0
    up_cost: float = 0
    down_qty: float = 0
    down_cost: float = 0
    fees: float = 0


@dataclass
class EntryAnchor:
    side: str
    signal_price: float
    created_at_ms: int
    qty: float
    fill_avg_price: Optional[float] = None


@dataclass
class ProbeState:
    side: Optional[str] = None
    since_ms: Optional[int] = None


@dataclass
class OrderAction:
    side: str
    qty: float
    tif: str
    role: str
    limit_price: float
    reason: str


@dataclass
class StrategyConfig:
    probe_confirm_ms: int = 5000
    entry_window_min_sec: int = 60
    entry_window_max_sec: int = 300
    entry_min_price: float = 0.55
    entry_max_price: float = 0.59
    entry_score_gap_min: float = 0.22
    entry_clip_shares: int = 5
    score_flip_threshold: float = 0.08
    entry_feasibility_lookback_sec: int = 45
    entry_current_feasibility_buffer: float = 0.03
    entry_recent_feasibility_buffer: float = 0.05
    late_confirmation_enabled: bool = True
    late_confirmation_shares: int = 5
    late_confirmation_window_min_sec: int = 8
    late_confirmation_window_max_sec: int = 20
    late_confirmation_min_price: float = 0.8
    late_confirmation_max_price: float = 0.95
    late_confirmation_confirm_ms: int = 4000
    late_confirmation_score_gap_min: float = 0.28
    late_confirmation_lookback_sec: int = 6
    late_confirmation_max_flips: int = 1
    late_confirmation_stability_buffer: float = 0.03
    late_confirmation_stable_window_sec: int = 60
    late_confirmation_stable_min_price: float = 0.7
    late_confirmation_stable_final_seconds: int = 20
    early_lock_pair_sum_threshold: float = 0.9
    pair_pending_timeout_sec: int = 15
    lock_target_pnl: float = 0.5
    repair_target_pnl: float = 0.0
    repair_gain_min: float = 0.5
    repair_max_price_buffer_over_leg2: float = 0.03
    loss_reduction_min_gain: float = 0.5
    loss_reduction_max_price: float = 0.82
    max_window_worst_case_loss: float = 4.0
    max_reverse_loss: float = 3.0
    max_inventory_shares_per_side: int = 30
    repair_clip_shares: int = 5
    hard_stop_seconds: int = 2
    tick_size: float = 0.01
    min_order_size: int = 5
    fee_rate_estimate: float = 0.0
    buy_price_chase_ticks: int = 2
    main_order_price_cap: float = 0.8
    second_leg_tif: str = "GTC"
    default_tif: str = "FAK"


def average_price(cost, qty):
    return cost / qty if qty > 0 else 0.0


def total_cost(portfolio):
    return portfolio.up_cost + portfolio.down_cost


def total_outlay(portfolio):
    return total_cost(portfolio) + portfolio.fees


def estimate_buy_fee(price, qty, fee_rate=0.0):
    return price * qty * fee_rate


def apply_buy(portfolio, side, price, qty, fee=0.0):
    next_portfolio = Portfolio(
        up_qty=portfolio.up_qty,
        up_cost=portfolio.up_cost,
        down_qty=portfolio.down_qty,
        down_cost=portfolio.down_cost,
        fees=portfolio.fees,
    )
    if side == "Up":
        next_portfolio.up_qty += qty
        next_portfolio.up_cost += qty * price
    else:
        next_portfolio.down_qty += qty
        next_portfolio.down_cost += qty * price
    next_portfolio.fees += fee
    return next_portfolio


def simulate_buy(portfolio, side, price, qty, fee_rate=0.0):
    fee = estimate_buy_fee(price, qty, fee_rate)
    next_portfolio = apply_buy(portfolio, side, price, qty, fee)
    return next_portfolio, get_portfolio_metrics(next_portfolio), fee


def get_portfolio_metrics(portfolio):
    cost = total_cost(portfolio)
    fees = portfolio.fees or 0.0
    outlay = cost + fees
    pnl_if_up = portfolio.up_qty - outlay
    pnl_if_down = portfolio.down_qty - outlay
    min_qty = min(portfolio.up_qty, portfolio.down_qty)
    max_qty = max(portfolio.up_qty, portfolio.down_qty)
    return {
        "totalCost": cost,
        "totalFees": fees,
        "totalOutlay": outlay,
        "pnlIfUp": pnl_if_up,
        "pnlIfDown": pnl_if_down,
        "pnlMin": min(pnl_if_up, pnl_if_down),
        "pnlMax": max(pnl_if_up, pnl_if_down),
        "lockedPnl": min_qty - outlay,
        "coverage": (min_qty / outlay) if outlay > 0 else 0.0,
        "upAvg": average_price(portfolio.up_cost, portfolio.up_qty),
        "downAvg": average_price(portfolio.down_cost, portfolio.down_qty),
        "bias": portfolio.up_qty - portfolio.down_qty,
        "balanceRatio": (min_qty / max_qty) if max_qty > 0 else 0.0,
    }


def qty_to_target_pnl(current_pnl, target_pnl, price, fee_rate=0.0):
    if current_pnl >= target_pnl:
        return 0
    per_share_improvement = 1 - price * (1 + fee_rate)
    if per_share_improvement <= 0:
        return None
    return (target_pnl - current_pnl) / per_share_improvement


def leg2_price_max(portfolio, weaker_side, target_locked_pnl, fee_rate=0.0):
    weaker_qty = portfolio.up_qty if weaker_side == "Up" else portfolio.down_qty
    stronger_qty = portfolio.down_qty if weaker_side == "Up" else portfolio.up_qty
    gap_qty = stronger_qty - weaker_qty

    if gap_qty <= 0 or stronger_qty <= 0:
        return None

    numerator = stronger_qty - total_outlay(portfolio) - target_locked_pnl
    denominator = gap_qty * (1 + fee_rate)
    if denominator <= 0:
        return None

    raw = numerator / denominator
    if raw <= 0:
        return None
    return clamp(raw, 0.001, 0.99)


def floor_to_tick(price, tick_size):
    if price is None:
        return None
    return clamp(round_down(price, tick_size), tick_size, 0.99)


class SecondLegOnlyMainStrategy:
    def __init__(self, config=None):
        self.config = config or StrategyConfig()
        self.probe = ProbeState()
        self.entry_anchor = None
        self.virtual_portfolio = Portfolio()
        self.history = []
        self.current_now_ms = 0

    def _phase(self, snapshot):
        has_up = self.virtual_portfolio.up_qty > 0
        has_down = self.virtual_portfolio.down_qty > 0
        if not has_up and not has_down:
            return "PROBE" if self.probe.side else "IDLE"
        if has_up != has_down:
            return "PAIR_PENDING" if self.entry_anchor else "LEG1"
        return "FINAL" if snapshot.time_to_expiry_sec <= 20 else "ACTIVE"

    def _recent_min_price(self, side, lookback_ms):
        cutoff = self.history[-1].now_ms - lookback_ms if self.history else 0
        values = []
        for snap in reversed(self.history):
            if snap.now_ms < cutoff:
                break
            values.append(getattr(snap.prices, side.lower()))
        return min(values) if values else None

    def _recent_flip_count(self, lookback_ms):
        if not self.history:
            return 0
        current_now_ms = self.history[-1].now_ms
        previous = None
        flips = 0
        for snap in reversed(self.history):
            if current_now_ms - snap.now_ms > lookback_ms:
                break
            direction = normalized_direction(
                snap.prices,
                snap.scores,
                self.config.score_flip_threshold,
            )
            if direction == "Neutral":
                continue
            if previous and previous != direction:
                flips += 1
            previous = direction
        return flips

    def _stable_tail_lock_eligible(self, snapshot, direction):
        if direction == "Neutral":
            return False
        if snapshot.time_to_expiry_sec > self.config.late_confirmation_stable_final_seconds:
            return False
        strong_price = getattr(snapshot.prices, direction.lower())
        if (
            strong_price < self.config.late_confirmation_stable_min_price or
            strong_price >= self.config.late_confirmation_max_price
        ):
            return False
        recent_strong_min = self._recent_min_price(
            direction,
            self.config.late_confirmation_stable_window_sec * 1000,
        )
        if recent_strong_min is None:
            return False
        return recent_strong_min >= self.config.late_confirmation_stable_min_price - 1e-9

    def _update_probe(self, snapshot, direction):
        if direction == "Neutral":
            self.probe = ProbeState()
            return
        if self.probe.side != direction:
            self.probe = ProbeState(side=direction, since_ms=snapshot.now_ms)

    def _entry_leg2_feasibility(self, snapshot, direction, qty):
        insurance = opposite_side(direction)
        effective_qty = clip_order_qty(qty, self.config.min_order_size)
        if effective_qty <= 0:
            return None

        simulated_portfolio, _, _ = simulate_buy(
            self.virtual_portfolio,
            direction,
            getattr(snapshot.prices, direction.lower()),
            effective_qty,
            self.config.fee_rate_estimate,
        )
        pmax = leg2_price_max(
            simulated_portfolio,
            insurance,
            self.config.lock_target_pnl,
            self.config.fee_rate_estimate,
        )
        if pmax is None:
            return None

        current_opposite_price = getattr(snapshot.prices, insurance.lower())
        recent_opposite_min = self._recent_min_price(
            insurance,
            self.config.entry_feasibility_lookback_sec * 1000,
        )
        current_feasible_cap = min(0.99, pmax + self.config.entry_current_feasibility_buffer)
        recent_feasible_cap = min(0.99, pmax + self.config.entry_recent_feasibility_buffer)

        current_feasible = current_opposite_price <= current_feasible_cap + 1e-9
        recent_feasible = (
            recent_opposite_min is not None and
            recent_opposite_min <= recent_feasible_cap + 1e-9
        )
        if not (current_feasible and recent_feasible):
            return None

        return {
            "insurance": insurance,
            "pmax": pmax,
            "simulated_portfolio": simulated_portfolio,
        }

    def _maybe_record_virtual_entry(self, snapshot):
        if self.entry_anchor is not None:
            return

        direction = normalized_direction(
            snapshot.prices,
            snapshot.scores,
            self.config.score_flip_threshold,
        )
        self._update_probe(snapshot, direction)

        if direction == "Neutral":
            return
        if snapshot.time_to_expiry_sec < self.config.entry_window_min_sec:
            return
        if snapshot.time_to_expiry_sec > self.config.entry_window_max_sec:
            return
        if self.probe.side != direction or self.probe.since_ms is None:
            return
        if snapshot.now_ms - self.probe.since_ms < self.config.probe_confirm_ms:
            return

        entry_price = getattr(snapshot.prices, direction.lower())
        if entry_price < self.config.entry_min_price or entry_price > self.config.entry_max_price:
            return

        score_gap = abs(snapshot.scores.up - snapshot.scores.down)
        if score_gap < self.config.entry_score_gap_min:
            return

        qty = clip_order_qty(self.config.entry_clip_shares, self.config.min_order_size)
        feasibility = self._entry_leg2_feasibility(snapshot, direction, qty)
        if feasibility is None:
            return

        self.entry_anchor = EntryAnchor(
            side=direction,
            signal_price=entry_price,
            created_at_ms=snapshot.now_ms,
            qty=qty,
            fill_avg_price=entry_price,
        )
        self.virtual_portfolio = feasibility["simulated_portfolio"]

    def _resolve_buy_price(self, snapshot, side, tif, limit_price=None):
        signal_price = getattr(snapshot.prices, side.lower())
        if tif == "GTC":
            return floor_to_tick(limit_price if limit_price is not None else signal_price, self.config.tick_size)
        books = snapshot.books or {}
        book = books.get(side.lower()) or {}
        best_ask = book.get("bestAsk")
        base_price = max(signal_price, best_ask) if best_ask is not None else signal_price
        chased = base_price + self.config.tick_size * self.config.buy_price_chase_ticks
        resolved = min(chased, limit_price) if limit_price is not None else chased
        price = clamp(resolved, self.config.tick_size, 0.99)
        return price

    def _build_late_confirmation(self, snapshot):
        if not self.config.late_confirmation_enabled:
            return None
        if self.entry_anchor is not None:
            return None
        if self.virtual_portfolio.up_qty > 0 or self.virtual_portfolio.down_qty > 0:
            return None
        if snapshot.time_to_expiry_sec <= self.config.hard_stop_seconds:
            return None
        direction = normalized_direction(
            snapshot.prices,
            snapshot.scores,
            self.config.score_flip_threshold,
        )
        self._update_probe(snapshot, direction)
        if direction == "Neutral":
            return None
        if self.probe.side != direction or self.probe.since_ms is None:
            return None
        if snapshot.now_ms - self.probe.since_ms < self.config.late_confirmation_confirm_ms:
            return None
        strong_price = getattr(snapshot.prices, direction.lower())
        stable_tail_eligible = self._stable_tail_lock_eligible(snapshot, direction)
        in_window = (
            self.config.late_confirmation_window_min_sec <= snapshot.time_to_expiry_sec <= self.config.late_confirmation_window_max_sec
        )
        if not in_window and not stable_tail_eligible:
            return None
        if (
            not stable_tail_eligible and
            (strong_price < self.config.late_confirmation_min_price or strong_price >= self.config.late_confirmation_max_price)
        ):
            return None
        if strong_price >= self.config.late_confirmation_max_price:
            return None
        score_gap = abs(snapshot.scores.up - snapshot.scores.down)
        min_score_gap = (
            min(self.config.entry_score_gap_min, self.config.late_confirmation_score_gap_min)
            if stable_tail_eligible else self.config.late_confirmation_score_gap_min
        )
        if score_gap < min_score_gap:
            return None
        recent_flip_count = self._recent_flip_count(self.config.late_confirmation_lookback_sec * 1000)
        if recent_flip_count > self.config.late_confirmation_max_flips:
            return None
        recent_strong_min = self._recent_min_price(
            direction,
            self.config.late_confirmation_lookback_sec * 1000,
        )
        if (
            recent_strong_min is not None and
            recent_strong_min < strong_price - self.config.late_confirmation_stability_buffer - 1e-9
        ):
            return None
        qty = clip_order_qty(self.config.late_confirmation_shares, self.config.min_order_size)
        if qty <= 0:
            return None
        max_price = self._resolve_buy_price(
            snapshot,
            direction,
            self.config.default_tif,
            None,
        )
        if (
            self.config.main_order_price_cap is not None and
            max_price > self.config.main_order_price_cap
        ):
            return None
        return OrderAction(
            side=direction,
            qty=qty,
            tif=self.config.default_tif,
            role="late_confirmation",
            limit_price=max_price,
            reason=(
                f"late_confirmation strong_price={strong_price:.4f} "
                f"score_gap={score_gap:.4f} stable_tail={stable_tail_eligible}"
            ),
        )

    def _pair_pending_age_ms(self):
        if self.entry_anchor is None:
            return 0
        return max(0, self.current_now_ms - self.entry_anchor.created_at_ms)

    def _repair_price_cap(self, side):
        if self.entry_anchor is None or side != opposite_side(self.entry_anchor.side):
            return None
        pmax = leg2_price_max(
            self.virtual_portfolio,
            side,
            self.config.lock_target_pnl,
            self.config.fee_rate_estimate,
        )
        if pmax is None:
            return None
        return min(1.0, pmax + self.config.repair_max_price_buffer_over_leg2)

    def _candidate_failure_reason(self, action, metrics, after_metrics):
        if action is None:
            return "missing_candidate"
        if action.qty < self.config.min_order_size:
            return "qty_below_min_order_size"
        if action.limit_price is None or action.limit_price <= 0:
            return "invalid_limit_price"
        if (
            after_metrics["pnlMin"] < -self.config.max_window_worst_case_loss and
            after_metrics["pnlMin"] < metrics["pnlMin"] - 1e-9
        ):
            return "would_exceed_window_loss_stop"
        if action.role in {"leg2_limit", "early_lock", "repair", "insurance"}:
            if metrics["pnlMin"] >= 0 and after_metrics["pnlMin"] < 0:
                return "would_break_non_negative_pnl_min"
            if metrics["lockedPnl"] >= 0 and after_metrics["lockedPnl"] < 0:
                return "would_break_non_negative_locked_pnl"
            if (
                metrics["pnlMax"] > 0 and
                after_metrics["pnlIfUp"] <= 0 and
                after_metrics["pnlIfDown"] <= 0
            ):
                return "would_make_both_sides_negative"
        inventory_after = (
            self.virtual_portfolio.up_qty + action.qty
            if action.side == "Up"
            else self.virtual_portfolio.down_qty + action.qty
        )
        if inventory_after > self.config.max_inventory_shares_per_side:
            return "inventory_limit_exceeded"
        if (
            action.role not in {"repair", "insurance", "loss_reduction", "leg2_limit", "early_lock"} and
            metrics["pnlMin"] < -self.config.max_reverse_loss
        ):
            return "reverse_loss_guard_active"
        return None

    def _build_leg2_limit(self, snapshot):
        if self.entry_anchor is None:
            return None

        main_side = self.entry_anchor.side
        insurance = opposite_side(main_side)
        main_qty = self.virtual_portfolio.up_qty if main_side == "Up" else self.virtual_portfolio.down_qty
        insurance_qty = (
            self.virtual_portfolio.up_qty if insurance == "Up" else self.virtual_portfolio.down_qty
        )
        gap_qty = clip_order_qty(main_qty - insurance_qty, self.config.min_order_size)
        if gap_qty <= 0:
            return None

        pmax = leg2_price_max(
            self.virtual_portfolio,
            insurance,
            self.config.lock_target_pnl,
            self.config.fee_rate_estimate,
        )
        if pmax is None:
            return None

        signal_price = getattr(snapshot.prices, insurance.lower())
        limit_price = floor_to_tick(pmax, self.config.tick_size)
        action = OrderAction(
            side=insurance,
            qty=gap_qty,
            tif=self.config.second_leg_tif,
            role="leg2_limit",
            limit_price=limit_price,
            reason=(
                f"virtual_leg1={main_side}@{self.entry_anchor.signal_price:.4f}, "
                f"signal_price={signal_price:.4f}, pmax={pmax:.4f}"
            ),
        )
        after_portfolio, after_metrics, _ = simulate_buy(
            self.virtual_portfolio,
            insurance,
            limit_price,
            gap_qty,
            self.config.fee_rate_estimate,
        )
        failure = self._candidate_failure_reason(
            action,
            get_portfolio_metrics(self.virtual_portfolio),
            after_metrics,
        )
        if failure:
            return None
        return action

    def _build_early_lock(self, snapshot):
        if self.entry_anchor is None:
            return None

        main_side = self.entry_anchor.side
        insurance = opposite_side(main_side)
        main_qty = self.virtual_portfolio.up_qty if main_side == "Up" else self.virtual_portfolio.down_qty
        insurance_qty = (
            self.virtual_portfolio.up_qty if insurance == "Up" else self.virtual_portfolio.down_qty
        )
        gap_qty = clip_order_qty(main_qty - insurance_qty, self.config.min_order_size)
        if gap_qty <= 0:
            return None

        insurance_price = getattr(snapshot.prices, insurance.lower())
        if self.entry_anchor.signal_price + insurance_price > self.config.early_lock_pair_sum_threshold:
            return None

        pmax = leg2_price_max(
            self.virtual_portfolio,
            insurance,
            self.config.lock_target_pnl,
            self.config.fee_rate_estimate,
        )
        if pmax is None or insurance_price > pmax:
            return None

        limit_price = floor_to_tick(pmax, self.config.tick_size)
        action = OrderAction(
            side=insurance,
            qty=gap_qty,
            tif=self.config.second_leg_tif,
            role="early_lock",
            limit_price=limit_price,
            reason=(
                f"virtual_leg1={main_side}@{self.entry_anchor.signal_price:.4f}, "
                f"insurance_price={insurance_price:.4f}, pair_sum="
                f"{self.entry_anchor.signal_price + insurance_price:.4f}"
            ),
        )
        after_portfolio, after_metrics, _ = simulate_buy(
            self.virtual_portfolio,
            insurance,
            limit_price,
            gap_qty,
            self.config.fee_rate_estimate,
        )
        failure = self._candidate_failure_reason(
            action,
            get_portfolio_metrics(self.virtual_portfolio),
            after_metrics,
        )
        if failure:
            return None
        return action

    def _build_repair(self, snapshot, role="repair"):
        if self.entry_anchor is None:
            return None
        metrics = get_portfolio_metrics(self.virtual_portfolio)
        weak_side = "Up" if metrics["pnlIfUp"] <= metrics["pnlIfDown"] else "Down"
        weak_pnl = metrics["pnlIfUp"] if weak_side == "Up" else metrics["pnlIfDown"]
        weak_price = getattr(snapshot.prices, weak_side.lower())
        repair_price_cap = self._repair_price_cap(weak_side)
        if repair_price_cap is not None and weak_price > repair_price_cap + 1e-9:
            return None
        qty_raw = qty_to_target_pnl(
            weak_pnl,
            self.config.repair_target_pnl,
            weak_price,
            self.config.fee_rate_estimate,
        )
        if not qty_raw:
            return None
        qty = clip_order_qty(
            min(qty_raw, self.config.repair_clip_shares),
            self.config.min_order_size,
        )
        if qty <= 0:
            return None
        after_portfolio, after_metrics, _ = simulate_buy(
            self.virtual_portfolio,
            weak_side,
            weak_price,
            qty,
            self.config.fee_rate_estimate,
        )
        delta_min = after_metrics["pnlMin"] - metrics["pnlMin"]
        if delta_min < self.config.repair_gain_min:
            return None
        if abs(after_metrics["bias"]) > abs(metrics["bias"]):
            return None
        action = OrderAction(
            side=weak_side,
            qty=qty,
            tif=self.config.second_leg_tif,
            role=role,
            limit_price=weak_price,
            reason=(
                f"{role} weak_side={weak_side} weak_price={weak_price:.4f} "
                f"delta_min={delta_min:.4f}"
            ),
        )
        failure = self._candidate_failure_reason(action, metrics, after_metrics)
        if failure:
            return None
        return action

    def _build_loss_reduction(self, snapshot):
        if self.entry_anchor is None:
            return None
        direction = normalized_direction(
            snapshot.prices,
            snapshot.scores,
            self.config.score_flip_threshold,
        )
        if direction == "Neutral" or direction == self.entry_anchor.side:
            return None
        if snapshot.time_to_expiry_sec <= self.config.hard_stop_seconds:
            return None
        score_gap = abs(snapshot.scores.up - snapshot.scores.down)
        if score_gap < self.config.entry_score_gap_min:
            return None
        if self._pair_pending_age_ms() < self.config.pair_pending_timeout_sec * 1000:
            return None
        price = getattr(snapshot.prices, direction.lower())
        if price > self.config.loss_reduction_max_price:
            return None
        metrics = get_portfolio_metrics(self.virtual_portfolio)
        weak_pnl = metrics["pnlIfUp"] if direction == "Up" else metrics["pnlIfDown"]
        qty_raw = qty_to_target_pnl(
            weak_pnl,
            self.config.repair_target_pnl,
            price,
            self.config.fee_rate_estimate,
        )
        if not qty_raw:
            return None
        qty = clip_order_qty(
            min(qty_raw, abs(metrics["bias"]), self.config.repair_clip_shares),
            self.config.min_order_size,
        )
        if qty <= 0:
            return None
        after_portfolio, after_metrics, _ = simulate_buy(
            self.virtual_portfolio,
            direction,
            price,
            qty,
            self.config.fee_rate_estimate,
        )
        delta_min = after_metrics["pnlMin"] - metrics["pnlMin"]
        if delta_min < self.config.loss_reduction_min_gain:
            return None
        if abs(after_metrics["bias"]) >= abs(metrics["bias"]):
            return None
        if after_metrics["pnlMax"] <= 0:
            return None
        action = OrderAction(
            side=direction,
            qty=qty,
            tif=self.config.default_tif,
            role="loss_reduction",
            limit_price=self._resolve_buy_price(snapshot, direction, self.config.default_tif, None),
            reason=f"loss_reduction side={direction} delta_min={delta_min:.4f}",
        )
        failure = self._candidate_failure_reason(action, metrics, after_metrics)
        if failure:
            return None
        return action

    def _select_in_priority_order(self, groups):
        for builder in groups:
            action = builder()
            if action is not None:
                return action
        return None

    def on_snapshot(self, snapshot):
        self.history.append(snapshot)
        self.current_now_ms = snapshot.now_ms
        self._maybe_record_virtual_entry(snapshot)
        phase = self._phase(snapshot)

        late_confirmation = self._build_late_confirmation(snapshot)
        if late_confirmation is not None:
            return late_confirmation

        if phase in {"IDLE", "PROBE"}:
            return None

        if phase in {"LEG1", "PAIR_PENDING"}:
            return self._select_in_priority_order([
                lambda: self._build_early_lock(snapshot),
                lambda: self._build_leg2_limit(snapshot),
                lambda: self._build_loss_reduction(snapshot),
            ])

        if phase == "FINAL":
            return self._select_in_priority_order([
                lambda: self._build_early_lock(snapshot),
                lambda: self._build_repair(snapshot, role="insurance"),
                lambda: self._build_loss_reduction(snapshot),
            ])

        return self._select_in_priority_order([
            lambda: self._build_early_lock(snapshot),
            lambda: self._build_repair(snapshot, role="repair"),
            lambda: self._build_loss_reduction(snapshot),
        ])

    def mark_second_leg_filled(self, side, price, qty):
        self.virtual_portfolio = apply_buy(
            self.virtual_portfolio,
            side,
            price,
            qty,
            estimate_buy_fee(price, qty, self.config.fee_rate_estimate),
        )


__all__ = [
    "Prices",
    "Scores",
    "Snapshot",
    "OrderAction",
    "SecondLegOnlyMainStrategy",
    "StrategyConfig",
]
