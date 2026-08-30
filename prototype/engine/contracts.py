"""STEP 2 — Fact Contracts.

A versioned, policy-defined checklist of the facts, sources and
freshness an answer REQUIRES before it may be spoken.
No proof, no confident claim.

Contracts are authored as versioned YAML (contracts.yaml) so compliance
teams can change policy without code releases. This module loads that
registry, validates it, and exposes FACT_CONTRACTS for the pipeline.
If the registry is missing or unversioned, loading fails closed.
"""
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class Requirement:
    fact: str            # logical fact key, e.g. "loan.status"
    system: str          # LOAN_DB | CONTRACTS | POLICY | CRM | KB | DOCS
    max_age_days: int | None   # freshness SLA; None = any age acceptable
    critical: bool = True      # missing a critical fact -> ABSTAIN

REGISTRY_VERSION: str = "unknown"
FACT_CONTRACTS: dict[str, list[Requirement]] = {}


def _load():
    global REGISTRY_VERSION, FACT_CONTRACTS
    yaml_path = Path(__file__).with_name("contracts.yaml")
    if not yaml_path.exists():
        raise RuntimeError(f"Fact contract registry missing: {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text())
    ver = data.get("registry_version")
    if not ver:
        raise RuntimeError("Fact contract registry is unversioned — refuses to load without a version.")
    REGISTRY_VERSION = str(ver)
    raw = data.get("contracts", {})
    out: dict[str, list[Requirement]] = {}
    for intent, reqs in raw.items():
        lst = []
        for r in reqs or []:
            if "fact" not in r or "system" not in r:
                raise ValueError(f"Contract {intent} entry missing fact/system: {r}")
            lst.append(Requirement(
                fact=r["fact"],
                system=r["system"],
                max_age_days=r.get("max_age_days"),
                critical=bool(r.get("critical", True)),
            ))
        out[intent] = lst
    FACT_CONTRACTS = out


# Load at import — fail fast if unversioned
_load()
