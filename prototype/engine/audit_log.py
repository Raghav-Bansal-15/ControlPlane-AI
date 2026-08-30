"""Append-only JSONL storage for complete evidence receipts."""

import hashlib
import json
import os
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "receipts.jsonl"


def _canonical_body(receipt: dict) -> bytes:
    body = {key: value for key, value in receipt.items() if key != "integrity_sha256"}
    return json.dumps(body, sort_keys=True, default=str).encode()


def verify_receipt_integrity(receipt: dict) -> bool:
    expected = hashlib.sha256(_canonical_body(receipt)).hexdigest()
    return receipt.get("integrity_sha256") == expected


def append_receipt(receipt: dict, *, path: Path | str = DEFAULT_PATH) -> None:
    """Append one complete, integrity-checked receipt to a JSONL audit file."""
    if not verify_receipt_integrity(receipt):
        raise ValueError("Receipt integrity check failed; refusing to write audit log")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, default=str, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_receipts(*, path: Path | str = DEFAULT_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    return [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
