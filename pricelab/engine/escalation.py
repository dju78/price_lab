"""Contract price escalation: indexation clauses applied to a payment
schedule, with the clause restated in plain language.

The arithmetic is simple; the risk is that someone misreads it. A payment
schedule that a contract manager cannot check against the contract wording
without reading code is a dispute waiting to happen, so every result
carries a summary (`EscalationResult.summary`) written for that reader: it
names the index, the vintage of the index, the lag, the order in which the
clause's parts are applied, and every period in which a cap, a collar, a
dead band or a trigger changed the payment -- with the payment it would
otherwise have been.

The clause, applied in this order for each payment period t
-----------------------------------------------------------
1. Index reading. The index `lag` periods before t, or the average of the
   `averaging` periods ending there. The base reading is taken the same way
   at `base_period`. A period whose reading is not yet published cannot be
   paid on, and is refused rather than estimated.
2. Movement. m = (reading / base reading - 1) x 100, in percent.
3. Dead band. A movement within +-d% is ignored. Beyond it, either only
   the excess passes through ("excess", the usual drafting) or the whole
   movement does ("full").
4. Trigger. The price is re-set only when the movement since the last
   re-set reaches the trigger; until then the last adjustment stands.
5. Indexation factor. Only the share f of the amount is indexed; 1 - f is
   fixed. Adjustment a = f x movement.
6. Cap and collar. a is limited to at most +cap% and at least -collar%.
7. Payment = base amount x (1 + a / 100).

Nothing is rounded until the schedule is printed; the summary states the
rounding the page applies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

__all__ = [
    "EscalationClause",
    "EscalationError",
    "EscalationResult",
    "apply_clause",
]


class EscalationError(ValueError):
    """The clause or the index cannot produce the schedule asked for."""


@dataclass(frozen=True)
class EscalationClause:
    base_amount: float
    """The contract amount per payment, before any adjustment."""
    index_name: str
    index_vintage: str
    """Which publication of the index: a run and its vintage, an official
    release and its retrieval date. Indexation on a revised index gives a
    different payment, so the vintage is part of the clause as applied."""
    base_period: pd.Timestamp
    lag: int = 0
    averaging: int = 1
    indexation_factor: float = 1.0
    dead_band_pct: float = 0.0
    dead_band_mode: Literal["excess", "full"] = "excess"
    trigger_pct: float = 0.0
    cap_pct: float | None = None
    collar_pct: float | None = None
    """The most the payment may fall below the base amount, in percent."""
    currency: str = ""

    def __post_init__(self) -> None:
        if self.base_amount <= 0:
            raise EscalationError("the base amount must be positive")
        if self.lag < 0 or self.averaging < 1:
            raise EscalationError("the lag must be zero or more and the averaging at least one "
                                  "period")
        if not 0.0 <= self.indexation_factor <= 1.0:
            raise EscalationError(
                f"the indexation factor is the indexed share of the amount, between 0 and 1; "
                f"{self.indexation_factor} was given")
        if self.dead_band_pct < 0 or self.trigger_pct < 0:
            raise EscalationError("a dead band or trigger cannot be negative")
        if self.dead_band_mode not in ("excess", "full"):
            raise EscalationError('the dead band mode is "excess" or "full"')
        for name, value in (("cap", self.cap_pct), ("collar", self.collar_pct)):
            if value is not None and value < 0:
                raise EscalationError(f"the {name} is a size in percent and cannot be negative")


@dataclass(frozen=True)
class EscalationResult:
    clause: EscalationClause
    schedule: pd.DataFrame
    events: tuple[str, ...]
    """One plain-language line for every period in which the dead band,
    the trigger, the cap or the collar changed the payment."""
    base_reading: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        """The clause as implemented, and what it did, for a non-specialist."""
        c = self.clause
        money = _money(c.currency)
        reading = ("the index" if c.averaging == 1 else
                   f"the average of the index over {c.averaging} periods ending")
        lag_text = ("in the payment period itself (no lag)" if c.lag == 0 else
                    f"{c.lag} period{'s' if c.lag != 1 else ''} before the payment period")
        lines = [
            f"How this schedule was calculated. Each payment starts from the base amount of "
            f"{money(c.base_amount)} and is adjusted by the movement in {c.index_name}, "
            f"vintage: {c.index_vintage}. The base reading is {reading} at "
            f"{c.base_period:%B %Y}: {self.base_reading:,.4f}. Each payment uses {reading} "
            f"{lag_text}.",
        ]
        fixed = 1.0 - c.indexation_factor
        lines.append(
            "The whole amount is indexed." if fixed == 0 else
            f"{c.indexation_factor:.0%} of the amount is indexed and {fixed:.0%} is fixed, so "
            f"an index movement of 10% changes the payment by "
            f"{10 * c.indexation_factor:.1f}%.")
        if c.dead_band_pct:
            lines.append(
                f"Movements within {c.dead_band_pct:g}% either way of the base reading are "
                "ignored" + ("; beyond that, only the part above the band is passed on."
                             if c.dead_band_mode == "excess" else
                             "; beyond it, the whole movement is passed on."))
        if c.trigger_pct:
            lines.append(
                f"The price is only re-set when the index has moved at least "
                f"{c.trigger_pct:g}% since the last re-set; in between, the last adjusted price "
                "stands.")
        limits = []
        if c.cap_pct is not None:
            limits.append(f"increases are capped at +{c.cap_pct:g}% of the base amount")
        if c.collar_pct is not None:
            limits.append(f"decreases are limited by a collar at -{c.collar_pct:g}%")
        if limits:
            joined = " and ".join(limits)
            lines.append(joined[:1].upper() + joined[1:] + ".")
        lines.append("The parts are applied in that order: index movement, dead band, trigger, "
                     "indexed share, then cap and collar.")
        schedule = self.schedule
        total = float(schedule["payment"].sum())
        unconstrained = float(schedule["payment_without_cap_or_collar"].sum())
        lines.append(
            f"Across {len(schedule)} payments the schedule totals {money(total)}, against "
            f"{money(c.base_amount * len(schedule))} unadjusted"
            + (f" and {money(unconstrained)} had there been no cap or collar."
               if abs(total - unconstrained) > 1e-9 else "."))
        if self.events:
            lines.append("Periods in which a limit changed the payment:")
            lines.extend(f"- {event}" for event in self.events)
        else:
            lines.append("No cap, collar, dead band or trigger changed any payment.")
        lines.append("Amounts are shown rounded to two decimal places; they are computed "
                     "unrounded.")
        return "\n".join(lines)


def _money(currency: str) -> Callable[[float], str]:
    prefix = f"{currency} " if currency else ""
    return lambda value: f"{prefix}{value:,.2f}"


def _reading(index: pd.Series, period: pd.Timestamp, averaging: int, step: int
             ) -> tuple[float, list[pd.Timestamp]]:
    periods = [period - pd.DateOffset(months=step * k) for k in range(averaging)]
    periods = [pd.Timestamp(p) for p in reversed(periods)]
    missing = [p for p in periods if p not in index.index or not np.isfinite(index[p])]
    if missing:
        raise EscalationError(
            f"the index has no published value for {', '.join(f'{p:%Y-%m}' for p in missing)}, "
            "which this payment needs; a payment is not made on an estimated index")
    return float(index[periods].mean()), periods


def apply_clause(clause: EscalationClause, index: pd.Series,
                 payment_periods: list[pd.Timestamp] | pd.DatetimeIndex, *,
                 months_per_period: int = 1) -> EscalationResult:
    """The payment schedule for `payment_periods` under `clause`."""
    series = pd.Series(index, dtype=float)
    series.index = pd.DatetimeIndex(series.index)
    step = months_per_period
    base, _ = _reading(series, pd.Timestamp(clause.base_period), clause.averaging, step)
    if base <= 0:
        raise EscalationError("the base reading of the index is not positive")
    money = _money(clause.currency)
    rows: list[dict[str, object]] = []
    events: list[str] = []
    current = 0.0            # the movement the price is currently set at (after the trigger)
    for payment in [pd.Timestamp(p) for p in payment_periods]:
        read_at = payment - pd.DateOffset(months=step * clause.lag)
        reading, used = _reading(series, pd.Timestamp(read_at), clause.averaging, step)
        movement = (reading / base - 1.0) * 100.0

        banded = movement
        in_band = abs(movement) <= clause.dead_band_pct
        if clause.dead_band_pct:
            if in_band:
                banded = 0.0
            elif clause.dead_band_mode == "excess":
                banded = movement - np.sign(movement) * clause.dead_band_pct

        triggered = abs(banded - current) >= clause.trigger_pct if clause.trigger_pct else True
        held = not triggered
        if triggered:
            current = banded
        adjustment = clause.indexation_factor * current

        limited = adjustment
        bound = ""
        if clause.cap_pct is not None and adjustment > clause.cap_pct:
            limited, bound = clause.cap_pct, "cap"
        if clause.collar_pct is not None and adjustment < -clause.collar_pct:
            limited, bound = -clause.collar_pct, "collar"
        payment_amount = clause.base_amount * (1.0 + limited / 100.0)
        unconstrained = clause.base_amount * (1.0 + adjustment / 100.0)

        when = f"{payment:%B %Y}"
        source = (f"{used[0]:%b %Y}" if len(used) == 1 else
                  f"the average of {used[0]:%b %Y} to {used[-1]:%b %Y}")
        head = (f"{when}: the index reading ({source}, {reading:,.4f}) is {movement:+.2f}% "
                "against the base")
        if clause.dead_band_pct and movement != banded:
            events.append(
                f"{head}; the {clause.dead_band_pct:g}% dead band "
                + ("absorbed all of it, so no adjustment applied." if in_band else
                   f"reduced it to {banded:+.2f}%."))
        if held:
            events.append(
                f"{head}; that is less than the {clause.trigger_pct:g}% trigger away from the "
                f"last re-set ({current:+.2f}%), so the price was not re-set.")
        if bound:
            limit = clause.cap_pct if bound == "cap" else -(clause.collar_pct or 0.0)
            events.append(
                f"{head}; with {clause.indexation_factor:.0%} indexation that is "
                f"{adjustment:+.2f}%. The {bound} of {limit:+g}% bound, so the payment is "
                f"{money(payment_amount)} instead of {money(unconstrained)} "
                f"({money(payment_amount - unconstrained)}).")
        rows.append({
            "payment_period": payment, "index_period": source, "index_reading": reading,
            "movement_pct": movement, "after_dead_band_pct": banded,
            "price_set_at_pct": current, "adjustment_pct": adjustment,
            "applied_pct": limited, "limit_bound": bound,
            "payment_without_cap_or_collar": unconstrained, "payment": payment_amount,
        })
    schedule = pd.DataFrame(rows).set_index("payment_period")
    return EscalationResult(clause=clause, schedule=schedule, events=tuple(events),
                            base_reading=base)
