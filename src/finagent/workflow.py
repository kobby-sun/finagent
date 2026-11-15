"""Config-driven skill pipeline (``config/workflow.yaml``).

The agent graph's RECONCILE node does not hard-code which controls run. It
asks the :class:`Pipeline` for the ordered list of skills applicable to the
case, so the workflow can be re-ordered, cut down or extended by editing YAML
(or by plugins registering new skills / conditions) - with guard-rails:

* unknown skills, parser skills in the processor pipeline, duplicate entries
  and dependencies on later/disabled skills are rejected;
* skills registered ``mandatory=True`` cannot be disabled or omitted - they
  are the policy-required controls (FIN-POL-001 s5 etc.);
* the resolved pipeline is hashed and recorded on each run.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .skills import builtin  # noqa: F401  (registers built-in parsers + processors)
from .skills.base import REGISTRY

# --------------------------------------------------------------------------- conditions
CONDITIONS: dict[str, Callable[[dict, Any], bool]] = {}


def condition(name: str):
    def deco(fn):
        CONDITIONS[name] = fn
        return fn
    return deco


def _inv(state: dict) -> dict:
    return state.get("invoice") or {}


@condition("always")
def _always(state, settings):
    return True


@condition("is_credit_note")
def _is_credit(state, settings):
    return state["request"].get("document_type") == "credit_note" or _inv(state).get("doc_kind") == "credit_note"


@condition("is_invoice")
def _is_invoice(state, settings):
    return not _is_credit(state, settings)


@condition("has_po")
def _has_po(state, settings):
    return _is_invoice(state, settings) and bool(state["request"].get("po_ref") or _inv(state).get("po_ref"))


@condition("no_po")
def _no_po(state, settings):
    return _is_invoice(state, settings) and not _has_po(state, settings)


@condition("foreign_currency")
def _foreign(state, settings):
    ccy = _inv(state).get("currency") or state["request"]["currency"]
    return ccy != settings.get("agent.base_currency", "AUD")


# --------------------------------------------------------------------------- model
@dataclass
class Step:
    skill: str
    stage: str
    when: str = "always"
    retrieve_when: str | None = None
    requires: list[str] = field(default_factory=list)
    enabled: bool = True


class WorkflowError(ValueError):
    pass


class Pipeline:
    def __init__(self, cfg: dict, source: str = "<dict>"):
        self.cfg = cfg
        self.source = source
        self.name = cfg.get("name", "workflow")
        self.version = str(cfg.get("version", "0"))
        self.steps: list[Step] = []
        for stage in cfg.get("stages", []):
            for s in stage.get("skills", []):
                self.steps.append(Step(skill=s["skill"], stage=stage["name"], when=s.get("when", "always"),
                                       retrieve_when=s.get("retrieve_when"), requires=list(s.get("requires", [])),
                                       enabled=bool(s.get("enabled", True))))
        self.validate()

    # ---------------------------------------------------------------- validation
    def validate(self) -> None:
        errors: list[str] = []
        seen: list[str] = []
        enabled = {s.skill for s in self.steps if s.enabled}
        for s in self.steps:
            spec = REGISTRY.get(s.skill)
            if spec is None:
                errors.append(f"unknown skill '{s.skill}' (registered: {sorted(k for k, v in REGISTRY.items() if v.kind == 'processor')})")
                continue
            if spec.kind != "processor":
                errors.append(f"'{s.skill}' is a {spec.kind}; only processor skills belong in the pipeline")
            if s.skill in seen:
                errors.append(f"skill '{s.skill}' listed more than once")
            for cond in filter(None, (s.when, s.retrieve_when)):
                if cond not in CONDITIONS:
                    errors.append(f"'{s.skill}': unknown condition '{cond}' (known: {sorted(CONDITIONS)})")
            if s.enabled:
                for dep in s.requires:
                    if dep not in seen or dep not in enabled:
                        errors.append(f"'{s.skill}' requires '{dep}', which must be enabled and listed earlier")
            if spec.mandatory and not s.enabled:
                errors.append(f"'{s.skill}' is a mandatory control ({spec.mandatory_basis}) and cannot be disabled")
            seen.append(s.skill)
        for name, spec in REGISTRY.items():
            if spec.kind == "processor" and spec.mandatory and name not in seen:
                errors.append(f"mandatory control '{name}' ({spec.mandatory_basis}) is missing from the workflow")
        if errors:
            raise WorkflowError(f"invalid workflow {self.source}:\n  - " + "\n  - ".join(errors))

    # ---------------------------------------------------------------- planning
    def plan(self, state: dict, settings) -> list[Step]:
        return [s for s in self.steps if s.enabled and CONDITIONS[s.when](state, settings)]

    def retrieval_plan(self, state: dict, settings) -> list[Step]:
        return [s for s in self.steps if s.enabled and CONDITIONS[s.retrieve_when or s.when](state, settings)]

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.describe(), sort_keys=True).encode()).hexdigest()[:12]

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version,
                "steps": [{"stage": s.stage, "skill": s.skill, "skill_version": REGISTRY[s.skill].version, "when": s.when,
                           "retrieve_when": s.retrieve_when, "requires": s.requires, "enabled": s.enabled,
                           "mandatory": REGISTRY[s.skill].mandatory} for s in self.steps]}


def load_plugins(modules: list[str], root: Path | None = None) -> list[str]:
    """Import plugin modules (e.g. "plugins.skills.my_check"). The project root is put on sys.path so plugins load
    the same way from the CLI, the API, helper scripts and tests, whatever the current directory is."""
    import sys
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    loaded = []
    for m in modules or []:
        importlib.import_module(m)
        loaded.append(m)
    return loaded


def load_pipeline(settings, path: str | Path | None = None) -> Pipeline:
    p = Path(path or settings.get("app.workflow_file") or settings.root / "config" / "workflow.yaml")
    if not p.is_absolute():
        p = settings.root / p
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    load_plugins(cfg.get("plugins", []) + list(settings.get("app.plugins") or []), settings.root)
    return Pipeline(cfg, str(p))
