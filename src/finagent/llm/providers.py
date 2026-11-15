"""Model abstraction.

``LLMProvider.complete(system, user, meta)`` returns raw text plus provenance
(provider, model, request id, token usage). Provider and model are selected
from configuration only (``llm.provider``, ``llm.providers.*``) - the
orchestration code never names a model.

Providers
---------
* ``mock``      - deterministic, offline "rules-echo" model used for tests and
                  evaluation; supports fault injection (malformed output,
                  unsafe outcome) to exercise validation and policy guards.
* ``ollama``    - REAL local model over HTTP (e.g. qwen2.5:3b-instruct or the
                  LoRA fine-tuned ``finagent-ap`` from training/).
* ``anthropic`` - REAL Claude API via the official ``anthropic`` SDK.
* ``bedrock``   - REAL Claude on Amazon Bedrock via ``AnthropicBedrockMantle``.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: dict = field(default_factory=dict)
    tokens: list | None = None          # [(token, logprob)] when the provider reports them (Ollama) - used to measure certainty


class LLMError(Exception):
    pass


class LLMProvider:
    name = "base"

    def __init__(self, settings):
        self.settings = settings

    @property
    def model(self) -> str:
        return str(self.settings.get(f"llm.providers.{self.name}.model") or self.settings.get("llm.model"))

    def complete(self, system: str, user: str, meta: dict) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------------------- mock
class MockProvider(LLMProvider):
    """Deterministic stand-in for a model. Reads the structured payload embedded
    in the prompt and writes a schema-valid draft that echoes the rules
    engine. Fault injection comes from ``mock_data/faults.json`` -> ``llm``."""
    name = "mock"

    def __init__(self, settings, fault_lookup=None):
        super().__init__(settings)
        self.fault_lookup = fault_lookup or (lambda case_id: {})

    @property
    def model(self) -> str:
        return str(self.settings.get("llm.model") or "finagent-rules-echo-v1")

    def complete(self, system: str, user: str, meta: dict) -> LLMResponse:
        t0 = time.perf_counter()
        if meta.get("purpose") == "skill":
            text = json.dumps(self._skill_reply(user, meta))
            return LLMResponse(text, "mock", self.model, f"mock-{uuid.uuid4().hex[:10]}", int((time.perf_counter() - t0) * 1000),
                               {"input_tokens": len(user) // 4, "output_tokens": len(text) // 4},
                               [(text, 0.0)])          # the mock is always certain (probability 1)
        fault = self.fault_lookup(meta.get("case_id", "")) or {}
        attempt = int(meta.get("attempt", 1))
        rid = f"mock-{uuid.uuid4().hex[:10]}"
        if attempt <= int(fault.get("malformed_attempts", 0)):
            text = "Sure! Here is my analysis:\n{\"summary\": \"Invoice looks fine\", \"proposed_outcome\": \"APPROVE\", "
            return LLMResponse(text, "mock", self.model, rid, int((time.perf_counter() - t0) * 1000), {"output_tokens": 30})
        m = re.search(r"<case_payload>\s*(\{.*?\})\s*</case_payload>", user, re.S)
        payload = json.loads(m.group(1)) if m else {}
        rules = payload.get("rules_assessment", {})
        outcome = fault.get("unsafe_outcome") or rules.get("outcome", "HOLD_FOR_INFORMATION")
        allowed = payload.get("allowed_source_ids", [])
        exceptions = rules.get("exceptions", [])
        draft = {
            "summary": (f"Rules assessment for case {payload.get('case', {}).get('case_id')} is {rules.get('outcome')}. "
                        + (f"Primary exception: {rules.get('primary_exception')}. " if rules.get("primary_exception") else
                           "All controls passed; human approval is still required before posting. ")
                        + f"{len(exceptions)} exception(s) recorded."),
            "proposed_outcome": outcome,
            "rationale": [f"{e['category']}: {e['failed_rule']} - expected {e['expected']}, observed {e['observed']}"
                          for e in exceptions[:6]] or ["Invoice, purchase order, receipt, vendor and duplicate checks passed "
                                                       "with deterministic calculations within tolerance."],
            "cited_source_ids": [s for s in allowed if not s.startswith(("ADV-", "FIN-POL-003-OLD"))][:6] or allowed[:1],
            "inferences": [i for i in rules.get("notes", [])][:3],
            "unknowns": [u["item"] for u in rules.get("unknowns", [])][:5],
            "risk_notes": [r["indicator"] for r in rules.get("risk_indicators", [])][:5],
            "additional_queries": [],
            "confidence": 0.9 if not exceptions else 0.8,
        }
        return LLMResponse(json.dumps(draft), "mock", self.model, rid, int((time.perf_counter() - t0) * 1000),
                           {"input_tokens": len(user) // 4, "output_tokens": len(json.dumps(draft)) // 4})


    def _skill_reply(self, user: str, meta: dict) -> dict:
        """Skill questions (skills/llm_runner.py). A recording in skills.llm.recordings_dir/<skill>/<case>[@attempt].yaml
        replays scripted replies in call order (small model, its repair, large model, ...; the last one repeats) - used
        by the judgement scenarios. Otherwise the mock gives the safe answer to every question. It never reads evidence."""
        import yaml
        from pathlib import Path
        base = Path(self.settings.get("skills.llm.recordings_dir", "mock_data/skill_recordings"))
        base = base if base.is_absolute() else self.settings.root / base
        skill, case, n = meta.get("skill"), meta.get("case_id"), int(meta.get("call_no", 1))
        for name in (f"{case}@{meta.get('attempt_no', 1)}.yaml", f"{case}.yaml"):
            p = base / str(skill) / name
            if p.exists():
                replies = yaml.safe_load(p.read_text(encoding="utf-8")) or []
                return replies[min(n, len(replies)) - 1]
        return {"answers": {q: {"answer": a, "quote": ""} for q, a in (meta.get("safe_answers") or {}).items()}}


# ---------------------------------------------------------------------------- ollama
class OllamaProvider(LLMProvider):
    name = "ollama"

    def complete(self, system: str, user: str, meta: dict) -> LLMResponse:
        base = self.settings.get("llm.providers.ollama.base_url", "http://localhost:11434")
        t0 = time.perf_counter()
        body = {
            "model": self.model, "stream": False, "format": "json",
            "options": {"temperature": float(self.settings.get("llm.temperature", 0.0)),
                        "num_predict": int(self.settings.get("llm.max_output_tokens", 2000)),
                        # Ollama's default context (2-4k tokens) silently truncates long prompts (skill questions carry
                        # policy text, references and script results) - set it explicitly
                        "num_ctx": int(self.settings.get("llm.providers.ollama.num_ctx", 16384))},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "logprobs": meta.get("purpose") == "skill",     # token probabilities -> how sure the answer is
        }
        try:
            r = httpx.post(f"{base}/api/chat", json=body, timeout=float(self.settings.get("llm.request_timeout_s", 60)))
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc
        data = r.json()
        return LLMResponse(data.get("message", {}).get("content", ""), "ollama", self.model,
                           f"ollama-{uuid.uuid4().hex[:10]}", int((time.perf_counter() - t0) * 1000),
                           {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")},
                           [(t.get("token", ""), float(t.get("logprob", 0))) for t in data.get("logprobs") or []] or None)


# ---------------------------------------------------------------------------- anthropic / bedrock
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install 'finagent[anthropic]' to use the anthropic provider") from exc
        return anthropic.Anthropic(timeout=float(self.settings.get("llm.request_timeout_s", 60)), max_retries=2)

    def complete(self, system: str, user: str, meta: dict) -> LLMResponse:
        t0 = time.perf_counter()
        client = self._client()
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=int(self.settings.get("llm.max_output_tokens", 2000)) * 4,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.settings.get(f"llm.providers.{self.name}.effort", "low")},
            )
        except Exception as exc:  # SDK raises typed errors; all are surfaced as LLMError for the retry loop
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        if getattr(msg, "stop_reason", None) == "refusal":
            raise LLMError("model refused the request")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        usage = {"input_tokens": getattr(msg.usage, "input_tokens", None), "output_tokens": getattr(msg.usage, "output_tokens", None)}
        return LLMResponse(text, self.name, self.model, getattr(msg, "id", ""), int((time.perf_counter() - t0) * 1000), usage)


class BedrockProvider(AnthropicProvider):
    name = "bedrock"

    def _client(self):
        try:
            from anthropic import AnthropicBedrockMantle
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install 'finagent[anthropic]' to use the bedrock provider") from exc
        return AnthropicBedrockMantle(aws_region=self.settings.get("llm.providers.bedrock.region"))


def pair_id(small, large) -> str:
    """Identity of the model pair that answers skill questions, as listed in skills.llm.approved."""
    return f"{small.name}/{small.model}" + (f"+{large.name}/{large.model}" if large is not None else "")


def probe_pair(small, large) -> dict:
    """Are the skill models usable now, and has this PAIR passed the acceptance gate? (/healthz, start-up; never raises)"""
    pid = pair_id(small, large)
    approved = pid in (small.settings.get("skills.llm.approved") or [])
    out = {"pair": pid, "approved": approved, "small": _reach(small), "large": _reach(large) if large is not None else None}
    if not approved:
        out["approval_hint"] = (f"skill model pair {pid} has not passed the model acceptance gate: run make skill-check "
                                f"SMALL={small.name}/{small.model}" + (f" LARGE={large.name}/{large.model}" if large else ""))
    return out


def _reach(llm: LLMProvider) -> dict:
    if llm.name != "ollama":
        return {"provider": llm.name, "model": llm.model, "reachable": True}
    base = llm.settings.get("llm.providers.ollama.base_url", "http://localhost:11434")
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=2).json().get("models", [])
        have = any(m.get("name") == llm.model or m.get("model") == llm.model for m in tags)
        return {"provider": "ollama", "model": llm.model, "reachable": True, "model_pulled": have,
                **({} if have else {"hint": f"ollama pull {llm.model}"})}
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        return {"provider": "ollama", "model": llm.model, "reachable": False, "error": type(exc).__name__,
                "hint": f"start it: ollama serve & ollama pull {llm.model} - until then skill procedures cannot "
                        "be answered and cases are HELD (or set skills.llm.use_mock: true for demos)"}


def make_provider(settings, fault_lookup=None) -> LLMProvider:
    name = settings.get("llm.provider", "mock")
    if name == "mock":
        return MockProvider(settings, fault_lookup)
    if name == "ollama":
        return OllamaProvider(settings)
    if name == "anthropic":
        return AnthropicProvider(settings)
    if name == "bedrock":
        return BedrockProvider(settings)
    raise ValueError(f"unknown llm.provider '{name}'")
