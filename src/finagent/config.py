"""Configuration loading.

Settings come from ``config/settings.yaml`` (or ``FINAGENT_CONFIG``) and can be
overridden per key with ``FINAGENT__SECTION__KEY`` environment variables. The
orchestration code only ever reads the resolved :class:`Settings` object, so the
model provider / model id is never hard-coded in the workflow.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.yaml"


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low in {"null", "none", ""}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _apply_env_overrides(cfg: dict, environ: dict[str, str]) -> dict:
    for key, raw in environ.items():
        if not key.startswith("FINAGENT__"):
            continue
        path = [p.lower() for p in key[len("FINAGENT__"):].split("__") if p]
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(raw)
    return cfg


class Settings:
    """Thin attribute wrapper around the resolved configuration dict."""

    def __init__(self, raw: dict, root: Path):
        self.raw = raw
        self.root = root

    # -- helpers -----------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted: str) -> Path:
        value = Path(str(self.get(dotted)))
        return value if value.is_absolute() else (self.root / value).resolve()

    @property
    def data_dir(self) -> Path:
        d = self.path("app.data_dir")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.get("app.db_file", "finagent.db")

    @property
    def audit_jsonl_path(self) -> Path:
        return self.data_dir / self.get("app.audit_jsonl", "audit.jsonl")

    @property
    def index_path(self) -> Path:
        p = self.data_dir / self.get("rag.index_file", "index/bm25_index.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def tool_setting(self, tool: str, key: str) -> Any:
        override = (self.get("tools.overrides", {}) or {}).get(tool, {}) or {}
        if key in override:
            return override[key]
        return self.get(f"tools.{ 'default_timeout_s' if key == 'timeout_s' else key }")

    def now(self) -> datetime:
        fixed = self.get("app.fixed_now")
        if fixed:
            dt = datetime.fromisoformat(str(fixed))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def with_overrides(self, overrides: dict) -> "Settings":
        raw = copy.deepcopy(self.raw)
        for dotted, value in overrides.items():
            node = raw
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return Settings(raw, self.root)

    def public_manifest(self) -> dict:
        """Non-secret configuration snapshot recorded with every run."""
        return {
            "llm": {k: v for k, v in (self.get("llm") or {}).items() if k != "providers"},
            "llm_provider_settings": (self.get("llm.providers") or {}).get(self.get("llm.provider"), {}),
            "rag": {k: self.get(f"rag.{k}") for k in ("top_k", "mode", "fusion", "vector", "superseded_penalty", "untrusted_penalty", "caller_role")},
            "agent": self.get("agent"),
            "workflow_file": self.get("app.workflow_file") or "config/workflow.yaml",
        }


def load_settings(config_path: str | Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ if environ is None else environ)
    path = Path(config_path or environ.get("FINAGENT_CONFIG") or DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    raw = _apply_env_overrides(raw, environ)
    return Settings(raw, PROJECT_ROOT)
