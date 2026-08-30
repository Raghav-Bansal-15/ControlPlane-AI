"""Small shared helpers."""
from datetime import date


def months_between(start_iso: str, end: date) -> int:
    """Whole months between an ISO date string and a reference date."""
    y, m, d = (int(x) for x in start_iso.split("-"))
    months = (end.year - y) * 12 + (end.month - m)
    if end.day < d:
        months -= 1
    return max(months, 0)


def inr(n) -> str:
    """Format an integer in Indian grouping: 2000000 -> ₹20,00,000."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail])
