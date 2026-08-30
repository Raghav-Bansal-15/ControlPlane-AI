"""Per-intent adaptive threshold store.

Reviewer overrides nudge the effective strictness threshold per intent.
This module persists deltas to logs/feedback_state.json so calibration
survives restarts. Global delta is deprecated; per-intent is correct.
"""
import json
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "logs" / "feedback_state.json"
STORE.parent.mkdir(parents=True, exist_ok=True)


def _path(store_path=None) -> Path:
    return Path(store_path) if store_path is not None else STORE


def _load(store_path=None) -> dict:
    path = _path(store_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save(data: dict, store_path=None):
    path = _path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def get_delta(intent: str, store_path=None) -> float:
    data = _load(store_path)
    return float(data.get(intent, 0.0))


def apply_delta(intent: str, delta: float, store_path=None):
    data = _load(store_path)
    data[intent] = round(float(data.get(intent, 0.0)) + delta, 3)
    # clamp to [-0.3, +0.5] to avoid runaway
    data[intent] = max(-0.3, min(0.5, data[intent]))
    _save(data, store_path)
    return data[intent]


def apply_review_feedback(intent: str, resolution: str, store_path=None) -> float:
    """Persist the calibrated change produced by a human review outcome."""
    changes = {"approved": -0.02, "upheld": 0.02}
    if resolution not in changes:
        raise ValueError(f"Unknown review resolution: {resolution}")
    return apply_delta(intent, changes[resolution], store_path=store_path)


def all_deltas(store_path=None) -> dict:
    return _load(store_path)


def reset_all(store_path=None) -> None:
    _save({}, store_path)
