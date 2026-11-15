"""Example plugin: shows every extension point without touching core code.

Enable in config/workflow.yaml:   plugins: ["plugins.example_extensions"]
(and add `- skill: po_line_count_guard` to a stage, after three_way_match).

Registers:
  * a parser for UBL-style XML e-invoices        (new input type)
  * a corpus connector for a JSON file feed      (new RAG source)
  * a workflow condition and an OPTIONAL control skill (re-organisable pipeline)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from finagent.contracts import CorpusDocument
from finagent.rag.ingest import register_connector
from finagent.schemas import Outcome
from finagent.skills.base import SkillOutput, skill
from finagent.skills.parsers.registry import register_parser
from finagent.workflow import condition


@register_parser("ubl_xml_parser", content_types=("application/xml", "text/xml"), extensions=(".xml",),
                 sniff=lambda name, text: "<Invoice" in text[:400], priority=75, version="0.1",
                 description="Minimal UBL 2.1 invoice reader (ID, IssueDate, currency, totals).")
def parse_ubl(filename: str, content: str) -> dict:
    def tag(t):
        m = re.search(rf"<(?:\w+:)?{t}[^>]*>([^<]+)</(?:\w+:)?{t}>", content)
        return m.group(1).strip() if m else None
    return {"doc_kind": "invoice", "source_filename": filename, "invoice_number": tag("ID"), "invoice_date": tag("IssueDate"),
            "currency": tag("DocumentCurrencyCode"), "subtotal": tag("LineExtensionAmount"), "tax": tag("TaxAmount"),
            "total": tag("PayableAmount"), "text": content[:20000], "extensions": {"x-ubl.customization": tag("CustomizationID")}}


@register_connector("json_file_feed")
def json_file_feed(cfg: dict, root: Path):
    """rag.sources entry: {type: json_file_feed, path: feeds/policies.json, trust_ceiling: untrusted}"""
    data = json.loads((root / cfg["path"]).read_text())
    for item in data.get("documents", []):
        doc = CorpusDocument.model_validate(item)
        if cfg.get("trust_ceiling"):
            doc.trust_ceiling = cfg["trust_ceiling"]
        yield doc


@condition("has_many_lines")
def has_many_lines(state, settings) -> bool:
    return len((state.get("invoice") or {}).get("lines", [])) > 50


@skill("po_line_count_guard", "processor", "Optional control: hold invoices with more than 50 lines for manual review.",
       compliance_refs=[("FIN-POL-007", "s2")], retrieval_queries=["required exception record failed rule expected observed"])
def po_line_count_guard(ev, prior=None) -> SkillOutput:
    out = SkillOutput("po_line_count_guard")
    n = len((ev.get("invoice") or {}).get("lines", []))
    if n > 50:
        out.finding("line_count", "FIN-POL-007 s2", "FAIL", f"{n} lines > 50", [("FIN-POL-007", "s2")])
        out.exception("OTHER_CONTROL_RISK", "Line-count guard", "<= 50 lines", str(n), "Accounts Payable", [("FIN-POL-007", "s2")])
        out.vote(Outcome.HOLD_FOR_INFORMATION, "too many lines for automated review")
    else:
        out.finding("line_count", "FIN-POL-007 s2", "PASS", f"{n} lines", [("FIN-POL-007", "s2")])
    return out
