"""Noisy variants of a request, for METAMORPHIC testing: noise must not change the outcome.

Each transform adds realistic mess but keeps the key facts readable (label lines such as "Invoice Number:" and the
line table are left intact). If noise does destroy a key fact, the case must be HELD - never guessed.

    ocr        OCR-style character errors in free-text lines of supplier documents
    chatty     long, misspelt requester notes with half-remembered (wrong) details
    clutter    irrelevant attachments: a marketing brochure and a newsletter
    forward    supplier e-mails wrapped in a forwarded chain with signature and legal disclaimer
    duplicate  a second copy of the first attachment, and the attachment order reversed
    max        all of the above at once

Used by tests/scenarios/test_noise_robustness.py (scripts, mock) and scripts/dev/skill_model_check.py --noise (models).
"""
from __future__ import annotations

import copy
import re

KINDS = ("ocr", "chatty", "clutter", "forward", "duplicate", "max")

_OCR = str.maketrans({"l": "1", "O": "0", "o": "0", "e": "c", "m": "rn", "S": "5", "i": "!"})
_CHATTY = ("hiya - pls proces this one asap-ish, its the one we talkd about last wk i think?? PO might be PO-99999 or "
           "smth like that, cant remember. teh supplier was v nice on the phone. also reminder the kitchen fridge gets "
           "cleaned friday!! anyway, lmk if u need anything, ta. ")
_BROCHURE = ("SPRING CATALOGUE - New season range now available! Save up to 20% on selected lines. Free delivery on "
             "orders over $500. Follow us for updates. This brochure is not an invoice.")
_NEWSLETTER = ("Supplier newsletter, November edition: our warehouse moved to a new site; office hours over the holiday "
               "period; meet our new account managers. No action is required.")
_FWD = ("---------- Forwarded message ---------\nFrom: Accounts team\nDate: Mon, 09 Nov 2026\nSubject: FW: see below\n\n"
        "Hi team, forwarding for processing. Thanks!\n\n> ")
_SIG = ("\n\nKind regards,\nAccounts Receivable\nLevel 3, 100 Example Street\n\nThis e-mail and any attachments are "
        "confidential and intended solely for the addressee. If you received it in error please delete it. Please "
        "consider the environment before printing this e-mail.\n")


def _ocr_text(text: str) -> str:
    """Garble free-text lines only: lines with a label (':') or a table ('|') stay readable."""
    return "\n".join(ln if (":" in ln or "|" in ln or not ln.strip()) else ln.translate(_OCR) for ln in text.split("\n"))


def _is_text(a: dict) -> bool:
    return a.get("encoding") != "base64" and a.get("content_type", "text/plain") in ("text/plain", "message/rfc822")


def ocr(req: dict) -> dict:
    for a in req.get("attachments") or []:
        if _is_text(a) and a.get("source", "supplier") in ("supplier", "email"):
            a["content"] = _ocr_text(a["content"])
    return req


def chatty(req: dict) -> dict:
    req["notes"] = (_CHATTY + (req.get("notes") or ""))[:5000]
    return req


def clutter(req: dict) -> dict:
    att = req.setdefault("attachments", [])
    for name, body in (("spring_brochure.txt", _BROCHURE), ("newsletter_nov.txt", _NEWSLETTER)):
        if len(att) < 10:
            att.append({"filename": name, "content_type": "text/plain", "content": body, "source": "supplier"})
    return req


def forward(req: dict) -> dict:
    for a in req.get("attachments") or []:
        if a.get("content_type") == "message/rfc822" and "\n\n" in a["content"]:
            head, body = a["content"].split("\n\n", 1)
            a["content"] = f"{head}\n\n{_FWD}{body.replace(chr(10), chr(10) + '> ')}{_SIG}"
    return req


def duplicate(req: dict) -> dict:
    att = req.setdefault("attachments", [])
    if att and len(att) < 10:
        dup = copy.deepcopy(att[0])
        dup["filename"] = re.sub(r"^", "copy_of_", dup["filename"])
        att.append(dup)
    att.reverse()
    return req


def variant(request: dict, kind: str) -> dict:
    """A noisy copy of ``request`` (the original is not modified)."""
    req = copy.deepcopy(request)
    for k in (("ocr", "chatty", "forward", "clutter", "duplicate") if kind == "max" else (kind,)):
        req = {"ocr": ocr, "chatty": chatty, "clutter": clutter, "forward": forward, "duplicate": duplicate}[k](req)
    return req
