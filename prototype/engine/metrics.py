"""Public metrics helpers used by the batch-audit view."""

import math


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 1)


def latency_percentiles(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": _nearest_rank(values, 0.50),
        "p95_ms": _nearest_rank(values, 0.95),
    }
