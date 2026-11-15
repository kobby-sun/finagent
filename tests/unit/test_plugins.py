"""The example plugin extends parsers, connectors and the pipeline without core changes."""
import copy

import yaml

from finagent.skills.parsers.registry import parse_attachment
from finagent.workflow import Pipeline, load_plugins


def test_example_plugin_extends_everything(settings, tmp_path):
    load_plugins(["plugins.example_extensions"])
    xml = ("<Invoice><ID>UBL-1</ID><IssueDate>2026-11-09</IssueDate><DocumentCurrencyCode>AUD</DocumentCurrencyCode>"
           "<LineExtensionAmount>100.00</LineExtensionAmount><TaxAmount>10.00</TaxAmount><PayableAmount>110.00</PayableAmount></Invoice>")
    doc = parse_attachment({"filename": "inv.xml", "content": xml, "content_type": "application/xml"})
    assert doc["parser"] == "ubl_xml_parser" and doc["total"] == "110.00"
    from finagent.rag.ingest import CONNECTORS
    assert "json_file_feed" in CONNECTORS
    cfg = yaml.safe_load((settings.root / "config" / "workflow.yaml").read_text())
    c = copy.deepcopy(cfg)
    c["stages"][2]["skills"].append({"skill": "po_line_count_guard", "when": "has_many_lines"})
    assert "po_line_count_guard" in [s.skill for s in Pipeline(c).steps]
