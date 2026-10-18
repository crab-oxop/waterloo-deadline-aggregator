"""Time estimates with a personal calibration factor (SPEC.md § Time Estimation & Learning Loop).

Each item's base estimate is the user's own (estimated_duration) or a flat default per type. Once
enough items are marked done with an actual time, estimates are scaled by his historical
actual/estimate ratio — per course first, then per type, then overall."""
from __future__ import annotations

from dataclasses import dataclass

from .models import Item

DEFAULT_MINUTES = {"assignment": 120, "quiz": 30, "exam": 180, "meeting": 60,
                   "message": 10, "announcement": 10, "manual": 60, "reminder": 10}
MIN_SAMPLES = 3
RATIO_BOUNDS = (0.25, 4.0)  # one wild data point shouldn't make estimates absurd


def base_minutes(item: Item) -> int:
    return item.estimated_duration or DEFAULT_MINUTES.get(item.type, 60)


@dataclass
class Calibration:
    by_context: dict[str, float]
    by_type: dict[str, float]
    overall: float | None

    def ratio_for(self, item: Item) -> tuple[float, str | None]:
        """(ratio, what it's based on) — basis is None when uncalibrated."""
        if item.context in self.by_context:
            return self.by_context[item.context], item.context
        if item.type in self.by_type:
            return self.by_type[item.type], f"{item.type} items"
        if self.overall is not None:
            return self.overall, "all items"
        return 1.0, None


def _ratio(pairs: list[tuple[int, int]]) -> float | None:
    if len(pairs) < MIN_SAMPLES:
        return None
    est = sum(e for e, _ in pairs)
    act = sum(a for _, a in pairs)
    lo, hi = RATIO_BOUNDS
    return min(max(act / est, lo), hi)


def calibrate(done_items: list[Item]) -> Calibration:
    by_ctx: dict[str, list] = {}
    by_type: dict[str, list] = {}
    everything = []
    for it in done_items:
        if not it.actual_duration:
            continue
        pair = (base_minutes(it), it.actual_duration)
        everything.append(pair)
        by_type.setdefault(it.type, []).append(pair)
        if it.context:
            by_ctx.setdefault(it.context, []).append(pair)
    return Calibration(
        by_context={k: r for k, v in by_ctx.items() if (r := _ratio(v)) is not None},
        by_type={k: r for k, v in by_type.items() if (r := _ratio(v)) is not None},
        overall=_ratio(everything),
    )


def estimate_minutes(item: Item, cal: Calibration) -> int:
    ratio, _ = cal.ratio_for(item)
    return max(5, round(base_minutes(item) * ratio / 5) * 5)


def fmt_minutes(m: int | None) -> str:
    if not m:
        return ""
    h, mins = divmod(int(m), 60)
    if not h:
        return f"{mins}m"
    return f"{h}h" if not mins else f"{h}h {mins:02d}m"
