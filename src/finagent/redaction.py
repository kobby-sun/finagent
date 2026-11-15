"""Safe-logging helpers (FIN-POL-001 s6, FIN-POL-004 s2, FIN-POL-010 s2).

Everything written to the audit trail, prompts or general logs goes through
:func:`redact`. Bank account numbers keep only the last four digits; tax ids,
e-mail addresses, phone numbers and credential-like values are masked.
"""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "account_number", "bank_account", "bsb", "iban", "swift", "bic", "routing_number",
    "tax_id", "abn", "tfn", "password", "secret", "api_key", "token", "authorization",
    "signing_secret", "signature", "phone", "contact_phone",
}

_ACCOUNT_RE = re.compile(r"\b(?:\d[ -]?){8,30}\d\b")
_BSB_RE = re.compile(r"\b\d{3}-\d{3}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d{1,4}\)?(?:[ -]?\d{2,4}){2,4}(?![\w.])")
_SECRET_RE = re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16})\b")


def mask_account(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) <= 4:
        return "****" + digits
    return "****" + digits[-4:]


def redact_text(text: str) -> str:
    if not text:
        return text
    text = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    text = _IBAN_RE.sub(lambda m: mask_account(m.group(0)), text)
    text = _ACCOUNT_RE.sub(lambda m: mask_account(m.group(0)), text)
    text = _BSB_RE.sub("***-***", text)
    text = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _PHONE_RE.sub(lambda m: "[PHONE]" if len(re.sub(r'\D', '', m.group(0))) >= 9 else m.group(0), text)
    return text


def redact(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        if isinstance(value, str) and key.lower() in {"account_number", "bank_account", "iban"}:
            return mask_account(value)
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
