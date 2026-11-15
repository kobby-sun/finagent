"""Skill test harness: every skill ships with fixtures that prove its behaviour on
real scenario data and on boundary variants.

Fixture files live next to the skill documentation::

    skills/<skill_name>/tests/fixtures.yaml

Processor fixture::

    - id: twm-price-boundary-exact
      description: variance exactly at the AUD 40 threshold passes (<=)
      case: FIN-007                 # build the evidence bundle from this scenario (real tools, mock backends)
      patch:                        # optional dotted-path edits (list indexes allowed; "docs.+" appends)
        invoice.lines.0.amount: "4040.00"
        invoice.subtotal: "4040.00"
      expect:
        votes: []                   # exact multiset of outcome votes
        exceptions: []              # exact multiset of exception categories
        findings: {three_way_match: PASS}       # subset: control -> status
        calculations: {price_variance: true}    # name prefix -> passed
        data: {amount_aud: "4433.00"}           # subset of skill data (string compare)
        risk_indicators_min: 0

Parser fixture::

    - id: csv-basic
      input: {file: mock_data/cases/FIN-005/attachments/EOS-5512.csv, content_type: text/csv}
      expect: {doc_kind: invoice, invoice_number: EOS-5512, total: "3300.00", lines: 1, parser: invoice_csv_parser}

Dependencies are honoured: the harness runs the skills listed before the target
in ``config/workflow.yaml`` (that apply to the case) and passes their outputs as
``prior``, exactly as the engine does.
"""
from __future__ import annotations

import base64
import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..schemas import ProcessingRequest
from .base import REGISTRY, SkillOutput
from .parsers.registry import parse_attachment

SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"


@dataclass
class FixtureResult:
    skill: str
    fixture_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    description: str = ""


def load_fixtures(skill_name: str | None = None) -> list[tuple[str, dict]]:
    out = []
    for f in sorted(SKILLS_DIR.glob("*/tests/fixtures.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        name = doc.get("skill", f.parent.parent.name)
        if skill_name and name != skill_name:
            continue
        for fx in doc.get("fixtures", []):
            out.append((name, fx))
    return out


def _set_path(obj: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj.setdefault(p, {})
    last = parts[-1]
    if isinstance(obj, list) and last == "+":       # "docs.+" appends (e.g. add an attachment)
        obj.append(value)
    elif isinstance(obj, list):
        obj[int(last)] = value
    elif value == "__delete__":
        obj.pop(last, None)
    else:
        obj[last] = value


class SkillHarness:
    def __init__(self, app):
        self.app = app
        self.root = app.settings.root
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ bundles
    def state_for_case(self, case_id: str) -> dict:
        if case_id not in self._cache:
            req = ProcessingRequest.model_validate(json.loads((self.root / "mock_data" / "cases" / case_id / "request.json").read_text()))
            state = {"request": req.model_dump(mode="json"), "budget": {"steps": 0, "tool_calls": 0, "llm_calls": 0},
                     "actions": [], "notes": []}
            eng = self.app.engine
            rid = f"skilltest-{case_id}"
            eng.n_parse(rid, state)
            state["planned_skills"] = eng._planned_skills(state)
            eng.n_gather(rid, state)
            self._cache[case_id] = state
        return copy.deepcopy(self._cache[case_id])

    def run_processor(self, name: str, state: dict) -> SkillOutput:
        ev = self.app.engine.evidence_bundle(state)
        prior: list[SkillOutput] = []
        order = [s.skill for s in self.app.pipeline.plan(state, self.app.settings)]
        if name not in order:  # skill not applicable / not in workflow for this case: run it standalone
            order.append(name)
        for n in order:
            if n == name:
                return REGISTRY[n].func(ev, prior)
            prior.append(REGISTRY[n].func(ev, prior))
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ execution
    def run_fixture(self, skill_name: str, fx: dict) -> FixtureResult:
        spec = REGISTRY.get(skill_name)
        if spec is None:
            return FixtureResult(skill_name, fx.get("id", "?"), False, [f"skill '{skill_name}' not registered"])
        try:
            if spec.kind == "parser":
                return self._run_parser_fixture(skill_name, fx)
            state = self.state_for_case(fx["case"])
            for path, value in (fx.get("patch") or {}).items():
                _set_path(state, path, value)
            out = self.run_processor(skill_name, state)
            return FixtureResult(skill_name, fx["id"], *self._check_processor(out, fx.get("expect", {})), fx.get("description", ""))
        except Exception as exc:
            return FixtureResult(skill_name, fx.get("id", "?"), False, [f"raised {type(exc).__name__}: {exc}"], fx.get("description", ""))

    def _check_processor(self, out: SkillOutput, exp: dict) -> tuple[bool, list[str]]:
        fails = []
        if "votes" in exp:
            got = sorted(v.outcome.value for v in out.votes)
            if got != sorted(exp["votes"]):
                fails.append(f"votes: expected {sorted(exp['votes'])}, got {got}")
        if "exceptions" in exp:
            got = Counter(e.category for e in out.exceptions)
            if got != Counter(exp["exceptions"]):
                fails.append(f"exceptions: expected {sorted(exp['exceptions'])}, got {sorted(got.elements())}")
        for control, status in (exp.get("findings") or {}).items():
            st = [f.status for f in out.findings if f.control == control]
            if status not in st:
                fails.append(f"finding {control}: expected {status}, got {st or 'none'}")
        for prefix, passed in (exp.get("calculations") or {}).items():
            calcs = [c for c in out.calculations if c["name"].startswith(prefix)]
            if not calcs or any(c["passed"] is not passed for c in calcs):
                fails.append(f"calculation {prefix}*: expected passed={passed}, got {[(c['name'], c['passed']) for c in calcs]}")
        for k, v in (exp.get("data") or {}).items():
            got = out.data.get(k)
            if str(got) != str(v) and not (isinstance(v, list) and got == v):
                fails.append(f"data.{k}: expected {v!r}, got {got!r}")
        if "risk_indicators_min" in exp and len({r['indicator'] for r in out.risk_indicators}) < exp["risk_indicators_min"]:
            fails.append(f"risk indicators: expected >= {exp['risk_indicators_min']}, got {[r['indicator'] for r in out.risk_indicators]}")
        if "approval_slots" in exp and [s["slot"] for s in out.approvals] != exp["approval_slots"]:
            fails.append(f"approval slots: expected {exp['approval_slots']}, got {[s['slot'] for s in out.approvals]}")
        if "unknowns_min" in exp and len(out.unknowns) < exp["unknowns_min"]:
            fails.append(f"unknowns: expected >= {exp['unknowns_min']}, got {len(out.unknowns)}")
        # contract: every exception and finding must reference governing policy (grounding)
        for e in out.exceptions:
            if not e.policy_refs:
                fails.append(f"exception {e.category} has no policy reference")
        return (not fails), fails

    def _run_parser_fixture(self, skill_name: str, fx: dict) -> FixtureResult:
        inp = fx["input"]
        if "file" in inp:
            p = self.root / inp["file"]
            data = p.read_bytes()
            content = base64.b64encode(data).decode() if inp.get("encoding") == "base64" else data.decode("utf-8")
            filename = p.name
        else:
            content, filename = inp["content"], inp.get("filename", "inline.txt")
        doc = parse_attachment({"filename": filename, "content": content, "content_type": inp.get("content_type", "text/plain"),
                                "source": inp.get("source", "supplier"), "encoding": inp.get("encoding", "utf-8")})
        fails = []
        for k, v in (fx.get("expect") or {}).items():
            if k == "lines":
                if len(doc["lines"]) != v:
                    fails.append(f"lines: expected {v}, got {len(doc['lines'])}")
            elif k == "parser":
                if not str(doc["parser"]).startswith(v):
                    fails.append(f"parser: expected {v}*, got {doc['parser']}")
            elif k == "warnings_contain":
                if not any(v in w for w in doc["parse_warnings"]):
                    fails.append(f"parse_warnings: expected to contain {v!r}, got {doc['parse_warnings']}")
            elif k.startswith("bank_last4"):
                got = (doc.get("bank_details") or {}).get("account_last4")
                if str(got) != str(v):
                    fails.append(f"bank_last4: expected {v}, got {got}")
            elif k.startswith("email_domain"):
                got = (doc.get("email") or {}).get("from_domain")
                if got != v:
                    fails.append(f"email_domain: expected {v}, got {got}")
            elif str(doc.get(k)) != str(v):
                fails.append(f"{k}: expected {v!r}, got {doc.get(k)!r}")
        if doc.get("schema") != "finagent.document/1":
            fails.append("output not tagged with schema finagent.document/1")
        return FixtureResult(skill_name, fx["id"], not fails, fails, fx.get("description", ""))

    def run_all(self, skill_name: str | None = None) -> list[FixtureResult]:
        return [self.run_fixture(s, fx) for s, fx in load_fixtures(skill_name)]

    # ------------------------------------------------------------------ coverage
    def coverage_matrix(self) -> dict:
        """For each scenario, the status each processor skill produced (PASS/FAIL/UNKNOWN/N/A/-)."""
        scenarios = json.loads((self.root / "mock_data" / "scenarios.json").read_text())["scenarios"]
        skills = [s.skill for s in self.app.pipeline.steps if s.enabled]
        rows = []
        for sc in scenarios:
            try:
                state = self.state_for_case(sc["case_id"])
            except Exception:
                continue
            planned = set(self.app.engine._planned_skills(state))
            row = {"case_id": sc["case_id"], "title": sc["title"], "cells": {}}
            for name in skills:
                if name not in planned:
                    row["cells"][name] = "-"
                    continue
                out = self.run_processor(name, state)
                sts = {f.status for f in out.findings}
                row["cells"][name] = ("VOTE:" + ",".join(sorted({v.outcome.value.split('_')[0] for v in out.votes}))) if out.votes \
                    else ("FAIL" if "FAIL" in sts else "UNKNOWN" if "UNKNOWN" in sts else "PASS" if sts else "ran")
            rows.append(row)
        return {"skills": skills, "rows": rows}
