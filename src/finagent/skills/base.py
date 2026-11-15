"""Skill framework.

A *skill* is a small, deterministic, reusable unit with a declared contract:

* ``kind``: ``parser`` (raw input -> normalised document) or ``processor``
  (normalised evidence -> findings / calculations / exceptions / votes);
* ``compliance_refs``: the corpus sections that govern it, e.g.
  ``("FIN-POL-002", "s2")``. These are injected into every result as
  citations (resolved against the RAG index) and snapshotted into
  ``skills/<name>/references/policy.md`` by ``scripts/skills/build_skill_docs.py`` (the procedure validates against it);
* ``retrieval_queries``: what the RAG step should search for when the
  skill participates in a run, so policy evidence is retrieved, not assumed.

Skills never call tools, never write state and never talk to the model:
they are pure functions of their inputs, which makes them unit-testable and
usable outside the agent (``scripts/run_skill.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..schemas import Outcome


@dataclass
class Finding:
    control: str
    rule: str                       # e.g. "FIN-POL-002 s2"
    status: str                     # PASS | FAIL | UNKNOWN | NOT_APPLICABLE
    detail: str
    policy_refs: list[tuple[str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # tool evidence ids / attachment ids


@dataclass
class ExceptionItem:
    category: str
    failed_rule: str
    expected: str
    observed: str
    owner: str
    policy_refs: list[tuple[str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class Vote:
    outcome: Outcome
    reason: str
    skill: str


@dataclass
class SkillOutput:
    skill: str
    findings: list[Finding] = field(default_factory=list)
    calculations: list[dict] = field(default_factory=list)
    exceptions: list[ExceptionItem] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)         # {"fact": str, "evidence": [ids]}
    unknowns: list[dict] = field(default_factory=list)      # {"item","impact","resolution"}
    risk_indicators: list[dict] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)     # approval slots
    data: dict[str, Any] = field(default_factory=dict)      # skill-specific payload
    compliance: list[dict] = field(default_factory=list)    # injected policy citations

    def finding(self, *a, **kw):
        self.findings.append(Finding(*a, **kw))

    def exception(self, *a, **kw):
        self.exceptions.append(ExceptionItem(*a, **kw))

    def vote(self, outcome: Outcome, reason: str):
        self.votes.append(Vote(outcome, reason, self.skill))


@dataclass
class SkillSpec:
    name: str
    kind: str                                   # parser | processor
    description: str
    compliance_refs: list[tuple[str, str]]
    retrieval_queries: list[str]
    func: Callable[..., Any]
    version: str = "1.0"
    mandatory: bool = False                     # policy-required control: cannot be removed from the workflow
    mandatory_basis: str = ""                   # the policy clause that makes it mandatory
    produces: tuple[str, ...] = ()              # data keys other skills may depend on


REGISTRY: dict[str, SkillSpec] = {}


def skill(name: str, kind: str, description: str, compliance_refs: list[tuple[str, str]],
          retrieval_queries: list[str] | None = None, *, version: str = "1.0", mandatory: bool = False,
          mandatory_basis: str = "", produces: tuple[str, ...] = ()):
    """Register a skill. Re-registering the same name replaces it (plugins can override built-ins)."""
    def deco(fn):
        REGISTRY[name] = SkillSpec(name, kind, description, compliance_refs, retrieval_queries or [], fn, version,
                                   mandatory, mandatory_basis, produces)
        fn.skill_name = name
        return fn
    return deco


def resolve_compliance(index_chunks: list[dict], refs: list[tuple[str, str]]) -> list[dict]:
    """Inject governing policy text for a skill from the indexed corpus."""
    out = []
    for doc_id, sec in refs:
        for c in index_chunks:
            if c["document_id"] == doc_id and c["chunk_id"].split("#")[1].split(".")[0] == sec:
                out.append({"chunk_id": c["chunk_id"], "document_id": doc_id, "version": c["version"],
                            "section": c["section"], "trust": c["trust"], "text": c["text"]})
    return out
