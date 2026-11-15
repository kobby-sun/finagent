"""Policy parameters: every number / list the controls enforce, with the policy section it comes from.

The code-based controls (skills/processors/controls.py) read these through ``P(ev, name)`` instead of literals, so
every policy number is in one reviewed place. Values live in ``config/policy_parameters.yaml`` (git-reviewed); changing
one is part of a policy maintenance release (docs/governance.md), never a live change. ``cite: null`` marks an *implementation
interpretation*: the policy gives no number, the value was chosen when the control was built (see docs/governance.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default: Any
    cite: str | None            # "FIN-POL-002#s2" or None (implementation interpretation)
    description: str
    unit: str = ""


CATALOG: dict[str, ParamSpec] = {p.name: p for p in [
    ParamSpec("vendor.new_vendor_days", 30, "FIN-POL-003#s3",
              "A vendor younger than this is a higher-risk transaction (Financial Control co-approval).", "days"),
    ParamSpec("vendor.bank_change_hold_business_days", 2, "FIN-POL-004#s2",
              "Payment hold after a verified bank-account change.", "business days"),
    ParamSpec("vendor.screening_max_age_hours", 24, "FIN-POL-009#s4",
              "Sanctions screening must be at most this old for an overseas bank account.", "hours"),
    ParamSpec("sod.recent_change_business_days", 5, "FIN-POL-001#s4",
              "Whoever created/changed the vendor record may not approve for this many business days.", "business days"),
    ParamSpec("sod.distinct_people_above_aud", "25000", "FIN-POL-001#s4",
              "Above this amount requester, receipter and approver must be different people.", "AUD"),
    ParamSpec("match.goods_abs_aud", "50", "FIN-POL-002#s2", "Goods price tolerance, absolute.", "AUD"),
    ParamSpec("match.goods_pct", "0.01", "FIN-POL-002#s2", "Goods price tolerance, share of the PO line value.", "ratio"),
    ParamSpec("match.services_abs_aud", "100", "FIN-POL-002#s2", "Services price tolerance, absolute.", "AUD"),
    ParamSpec("match.services_pct", "0.02", "FIN-POL-002#s2", "Services price tolerance, share of the PO line value.", "ratio"),
    ParamSpec("match.freight_abs_aud", "75", "FIN-POL-002#s2", "Freight variance when the PO permits freight.", "AUD"),
    ParamSpec("match.tax_tolerance_aud", "0.05", None,
              "Rounding tolerance for tax vs PO tax rate (policy says 'assessed separately').", "AUD"),
    ParamSpec("credit.tax_tolerance_aud", "0.05", None, "Rounding tolerance for credit-note GST.", "AUD"),
    ParamSpec("duplicates.fuzzy_amount_pct", "0.005", None,
              "Amounts this close count as a fuzzy-duplicate signal ('probable match' interpretation).", "ratio"),
    ParamSpec("duplicates.fuzzy_date_window_days", 14, None,
              "Invoice dates this close count as a fuzzy-duplicate signal.", "days"),
    ParamSpec("non_po.allowed_categories", ["emergency", "lease", "membership", "statutory", "utilities"], "FIN-POL-012#s1", "Categories that may be invoiced without a PO."),
    ParamSpec("non_po.repeat_window_days", 90, "FIN-POL-012#s4", "Look-back window for repeated non-PO use.", "days"),
    ParamSpec("non_po.repeat_threshold", 2, "FIN-POL-012#s4",
              "This many non-PO invoices (incl. the current one) from one supplier in the window -> escalate.", "invoices"),
    ParamSpec("non_po.director_max_aud", "10000", "FIN-POL-012#s3",
              "Non-PO up to this amount: Department Director; above: + Procurement Governance.", "AUD"),
    ParamSpec("fraud.escalation_indicator_count", 2, "FIN-POL-005#s3",
              "Distinct fraud indicators that trigger escalation.", "indicators"),
    ParamSpec("payment.default_terms_days", 30, "FIN-POL-006#s1", "Standard payment terms when none are agreed.", "days"),
    ParamSpec("payment.run_weekdays", [1, 3], "FIN-POL-006#s2", "Standard payment-run weekdays (0 = Monday).", "weekday"),
]}


def coerce(name: str, value: Any) -> Any:
    """Normalise a parameter value to the catalog's type (Decimal for money/ratios, int for counts, sorted list)."""
    spec = CATALOG[name]
    d = spec.default
    if isinstance(d, list):
        if not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{name}: expected a list")
        return sorted({(int(v) if isinstance(d[0], int) else str(v).lower()) for v in value})
    if isinstance(d, int):
        if isinstance(value, bool) or Decimal(str(value)) != int(Decimal(str(value))):
            raise ValueError(f"{name}: expected a whole number, got {value!r}")
        return int(Decimal(str(value)))
    return format(Decimal(str(value)).normalize(), "f")


def as_decimal(v: Any) -> Decimal:
    return Decimal(str(v))


def load_baseline(path: Path) -> dict[str, Any]:
    """Catalog defaults overlaid with config/policy_parameters.yaml."""
    values = {k: coerce(k, s.default) for k, s in CATALOG.items()}
    if path.exists():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for k, v in (doc.get("parameters") or {}).items():
            if k not in CATALOG:
                raise ValueError(f"{path}: unknown parameter '{k}' (catalog: governance/params.py)")
            values[k] = coerce(k, v["value"] if isinstance(v, dict) else v)
    return values


DEFAULTS = {k: coerce(k, s.default) for k, s in CATALOG.items()}


def P(ev: dict, name: str) -> Any:
    """Read an effective parameter from the evidence bundle (falls back to the catalog default for standalone use)."""
    return (ev.get("params") or {}).get(name, DEFAULTS[name])


def PD(ev: dict, name: str) -> Decimal:
    return as_decimal(P(ev, name))
