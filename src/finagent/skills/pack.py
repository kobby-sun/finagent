"""Skill packs: one uniform contract for every skill folder (skills/<name>/).

    skills/<name>/
      SKILL.md        front matter = the SPEC (validated); body = human explanation of the skill
      references/     policy.md (generated from the live index) + external reference docs
      tests/          fixtures.yaml (tests of the deterministic script)

Every skill runs its DETERMINISTIC SCRIPT (src/finagent/skills/processors/) - maths, matching, limits. Skills with
``execution: code+llm`` additionally ask a model a few FIXED QUESTIONS ("code asks, model answers"):

  * code collects the evidence excerpts named in ``evidence`` (the model never fetches anything);
  * the model answers each question with one of its allowed ``answers`` plus a VERBATIM quote from the excerpts;
  * code checks the quote really appears in the excerpts, maps the answers to a verdict with ``on`` and adds the
    citations itself (the skill's policy sections + the question's reference doc + the quoted document).

The model never chooses steps, citations, numbers or verdicts - it only answers questions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .base import REGISTRY

SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"
VERDICTS = ("NO_CHANGE", "FLAG", "HOLD", "ESCALATE")
SEVERITY = {"NO_CHANGE": 0, "FLAG": 1, "HOLD": 2, "ESCALATE": 3}
CATEGORIES = ("MISSING_PO", "MISSING_RECEIPT", "PRICE_VARIANCE", "QUANTITY_VARIANCE", "DUPLICATE_RISK", "VENDOR_BLOCK",
              "BANK_CHANGE", "AUTHORITY_GAP", "TAX_QUERY", "OTHER_CONTROL_RISK")
EVIDENCE = ("docs", "docs.internal", "request.notes", "invoice.lines", "invoice.non_po", "po.lines", "po.receipts")
CHECKS = ("has_internal_doc",)          # conditions computed by code (llm_runner.CHECKS) - the model never decides them


class OnConcern(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    owner: str
    expected: str


class When(BaseModel):
    """Ask the question only when CODE says it applies: a field value (path/equals) or a named check."""
    model_config = ConfigDict(extra="forbid")
    path: Optional[str] = None            # e.g. invoice.non_po.category
    equals: Optional[str] = None
    check: Optional[Literal[CHECKS]] = None  # type: ignore[valid-type]  e.g. has_internal_doc

    @model_validator(mode="after")
    def _one(self):
        if (self.check is None) == (self.path is None or self.equals is None):
            raise ValueError("when: give either path + equals, or check")
        return self


class Question(BaseModel):
    """One fixed judgement question the model answers."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    ask: str = Field(min_length=10, max_length=400)
    answers: list[str] = Field(min_length=2, max_length=4)
    on: dict[str, Literal[VERDICTS]] = Field(default_factory=dict)   # type: ignore[valid-type]  answer -> verdict
    finding: str                                                      # finding name shown in results
    indicator: Optional[str] = None                                   # risk-indicator label when the verdict is FLAG
    reference: Optional[str] = None                                   # reference doc this question is judged against
    evidence: Optional[list[str]] = None                              # what code shows for THIS question (default: spec)
    when: Optional[When] = None                                       # ask only when this condition holds
    # what code does when the deciding answer is UNCERTAIN ("unclear", or below skills.llm.sure_threshold):
    #   warn          warning for the approver (every approval already has a human approver)
    #   ask_evidence  HOLD and ask the on_concern owner for the evidence that settles it
    #   review        HOLD for a person to review the AI judgement
    if_uncertain: Literal["warn", "ask_evidence", "review"] = "warn"

    @model_validator(mode="after")
    def _check(self):
        bad = [a for a in self.on if a not in self.answers]
        if bad:
            raise ValueError(f"{self.id}: 'on' uses answers not in 'answers': {bad}")
        if not any(self.on.get(a, "NO_CHANGE") == "NO_CHANGE" for a in self.answers):
            raise ValueError(f"{self.id}: at least one answer must mean NO_CHANGE")
        return self

    def verdict(self, answer: str) -> str:
        return self.on.get(answer, "NO_CHANGE")


class SkillPackSpec(BaseModel):
    """Front matter of SKILL.md. The same fields for every skill; the runner enforces them."""
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    kind: Literal["parser", "processor"]
    execution: Literal["code", "code+llm"]
    description: str
    policy: list[str] = Field(default_factory=list)          # DOC#sN sections this skill implements
    evidence: list[str] = Field(default_factory=list)        # excerpts code collects for the questions
    questions: list[Question] = Field(default_factory=list)
    references: list[str] = Field(default_factory=lambda: ["policy.md"])
    on_concern: Optional[OnConcern] = None                    # exception raised for HOLD / ESCALATE answers

    @field_validator("evidence")
    @classmethod
    def _evidence(cls, v):
        bad = [k for k in v if k not in EVIDENCE]
        if bad:
            raise ValueError(f"unknown evidence {bad} (allowed: {EVIDENCE})")
        return v


class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer: str
    quote: str = Field(default="", max_length=400)


class Answers(BaseModel):
    """What a model returns: {"answers": {"<question id>": {"answer": "...", "quote": "..."}}}"""
    model_config = ConfigDict(extra="ignore")
    answers: dict[str, Answer]


# ============================================================================================ packs
@dataclass
class SkillPack:
    name: str
    dir: Path
    spec: SkillPackSpec
    body: str
    references: dict[str, str] = field(default_factory=dict)    # "ref:<skill>/<file>" -> text


_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_CACHE: dict[str, tuple[float, SkillPack]] = {}


class SkillPackError(ValueError):
    pass


def load_pack(name: str, base: Path | None = None) -> SkillPack:
    d = (base or SKILLS_DIR) / name
    md = d / "SKILL.md"
    if not md.exists():
        raise SkillPackError(f"{md} missing")
    stamp = max([md.stat().st_mtime] + [p.stat().st_mtime for p in d.glob("references/*")])
    if name in _CACHE and _CACHE[name][0] == stamp:
        return _CACHE[name][1]
    m = _FM.match(md.read_text(encoding="utf-8"))
    if not m:
        raise SkillPackError(f"{md}: front matter (--- ... ---) missing")
    try:
        spec = SkillPackSpec.model_validate(yaml.safe_load(m.group(1)))
    except ValidationError as exc:
        raise SkillPackError(f"{md}: invalid spec: {exc}") from exc
    if spec.name != name:
        raise SkillPackError(f"{md}: spec name {spec.name} != folder {name}")
    refs = {}
    for r in spec.references:
        p = d / "references" / r
        if not p.exists():
            raise SkillPackError(f"{name}: reference {r} missing in {d / 'references'}")
        refs[f"ref:{name}/{r}"] = p.read_text(encoding="utf-8")
    for q in spec.questions:
        if q.reference and q.reference not in spec.references:
            raise SkillPackError(f"{name}: question {q.id} uses reference {q.reference} not listed in references")
    pack = SkillPack(name, d, spec, m.group(2).strip(), refs)
    _CACHE[name] = (stamp, pack)
    return pack


def validate_all(base: Path | None = None) -> list[str]:
    """Every built-in skill must have a valid pack whose spec agrees with the code registry."""
    errors = []
    for name, reg in REGISTRY.items():
        builtin = reg.func.__module__.startswith("finagent.")
        if not builtin and not ((base or SKILLS_DIR) / name / "SKILL.md").exists():
            continue        # plugin without a pack: runs its script only
        try:
            p = load_pack(name, base)
        except SkillPackError as exc:
            errors.append(str(exc))
            continue
        if p.spec.kind != reg.kind:
            errors.append(f"{name}: spec kind {p.spec.kind} != registered {reg.kind}")
        if set(p.spec.policy) != {f"{d}#{s}" for d, s in reg.compliance_refs}:
            errors.append(f"{name}: spec policy != code compliance_refs")
        if reg.kind == "parser" and p.spec.execution != "code":
            errors.append(f"{name}: parsers must be execution: code")
        if p.spec.execution == "code+llm":
            if not p.spec.questions or not p.spec.evidence:
                errors.append(f"{name}: code+llm skills need questions and evidence")
            if any(v in ("HOLD", "ESCALATE") for q in p.spec.questions for v in q.on.values()) and p.spec.on_concern is None:
                errors.append(f"{name}: HOLD / ESCALATE answers need on_concern")
            for q in p.spec.questions:
                if not q.reference:
                    errors.append(f"{name}: question {q.id} must name the reference it is judged against")
                if q.evidence and not set(q.evidence) <= set(p.spec.evidence):
                    errors.append(f"{name}: question {q.id} evidence must be a subset of the skill's evidence")
                if q.evidence and set(q.evidence) - set(EVIDENCE):
                    errors.append(f"{name}: question {q.id} unknown evidence {sorted(set(q.evidence) - set(EVIDENCE))}")
            if any(q.if_uncertain == "ask_evidence" for q in p.spec.questions) and p.spec.on_concern is None:
                errors.append(f"{name}: if_uncertain ask_evidence needs on_concern (who to ask)")
        elif p.spec.questions:
            errors.append(f"{name}: execution code cannot have questions")
    return errors
