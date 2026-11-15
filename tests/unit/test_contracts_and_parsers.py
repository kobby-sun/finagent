import base64

import pytest
from pydantic import ValidationError

from finagent.contracts import CorpusDocument, CorpusSection, NormalizedDocument
from finagent.rag.ingest import chunk_corpus_document, classify_trust
from finagent.skills.parsers.registry import PARSERS, parse_attachment, register_parser, unregister_parser


def test_extensions_must_be_namespaced():
    with pytest.raises(ValidationError):
        NormalizedDocument(doc_kind="invoice", source_filename="a", parser="p", content_sha256="x", extensions={"po": 1})
    NormalizedDocument(doc_kind="invoice", source_filename="a", parser="p", content_sha256="x", extensions={"x-portal.po": 1})


def test_full_bank_number_rejected_by_contract():
    with pytest.raises(ValidationError):
        NormalizedDocument(doc_kind="invoice", source_filename="a", parser="p", content_sha256="x",
                           bank_details={"account_last4": "062000101234"})


def test_new_parser_plugs_in_without_engine_change():
    @register_parser("test_xml_parser", content_types=("application/xml",), extensions=(".xml",), priority=90)
    def parse_xml(filename, content):
        return {"doc_kind": "invoice", "source_filename": filename, "invoice_number": "X-1", "total": "10.00",
                "extensions": {"x-xml.root": "Invoice"}}
    try:
        doc = parse_attachment({"filename": "a.xml", "content": "<Invoice/>", "content_type": "application/xml"})
        assert doc["parser"] == "test_xml_parser" and doc["invoice_number"] == "X-1" and doc["schema"] == "finagent.document/1"
        assert doc["extensions"]["x-xml.root"] == "Invoice"
    finally:
        unregister_parser("test_xml_parser")


def test_contract_violating_parser_degrades_safely():
    @register_parser("bad_parser", extensions=(".bad",), priority=90)
    def bad(filename, content):
        return {"doc_kind": "spaceship", "source_filename": filename}
    try:
        doc = parse_attachment({"filename": "x.bad", "content": "zzz", "content_type": "text/plain"})
        assert doc["doc_kind"] == "supporting" and "contract" in doc["parse_warnings"][0]
    finally:
        unregister_parser("bad_parser")


def test_unknown_input_falls_back_to_text():
    doc = parse_attachment({"filename": "blob.bin", "content": "hello", "content_type": "application/octet-stream"})
    assert doc["parser"] == "invoice_text_parser" and doc["doc_kind"] == "supporting"


def test_corpus_trust_ceiling_only_lowers():
    doc = CorpusDocument(document_id="FEED-1", title="t", version="1", status="current", sections=[CorpusSection(heading="1. A", text="x")],
                         trust_ceiling="untrusted")
    assert classify_trust(doc).value == "untrusted"
    doc2 = CorpusDocument(document_id="FEED-2", title="t", version="1", status="superseded", sections=[CorpusSection(heading="1. A", text="x")],
                          trust_ceiling="authoritative")
    assert classify_trust(doc2).value == "superseded"


def test_corpus_chunk_ids_stable():
    doc = CorpusDocument(document_id="FIN-POL-099", title="New", version="1.0", status="current",
                         sections=[CorpusSection(heading="2. Rules", text="Some rule.")])
    assert chunk_corpus_document(doc)[0].chunk_id == "FIN-POL-099@v1.0#s2"


def test_builtin_parsers_registered():
    assert {"invoice_json_parser", "invoice_text_parser", "invoice_csv_parser", "email_parser", "pdf_parser"} <= set(PARSERS)


def test_noise_does_not_create_false_signals():
    """Found by noisy scenario FIN-075: an e-mail disclaimer is not 'secret payment language', and a quoted
    'From:' header is not the supplier when the invoice states 'Supplier:'."""
    from finagent.skills.parsers.registry import parse_attachment
    from finagent.skills.processors.controls import SCREEN_PATTERNS
    import re
    secret = dict(SCREEN_PATTERNS)["urgent or secret payment language"]
    assert not re.search(secret, "This e-mail and any attachments are confidential and intended solely for the addressee.", re.I)
    assert re.search(secret, "Please keep this confidential and pay today.", re.I)
    body = ("From: Accounts <a@x.example>\nSubject: FW: invoice\n\n> From: Sam (Evergreen)\n\nTAX INVOICE\n"
            "Supplier: Evergreen Office Supplies Pty Ltd\nInvoice Number: EOS-1\nInvoice Date: 2026-11-09\nTotal Due: AUD 10.00\n")
    doc = parse_attachment({"filename": "x.eml", "content_type": "message/rfc822", "content": body})
    assert doc["vendor_name"] == "Evergreen Office Supplies Pty Ltd"



def test_noise_matrix_regressions():
    """Found by the noise matrix (finagent.noise): 'new account managers' is not a bank change, and an invoice quoted
    with '> ' in a forwarded e-mail still parses."""
    from finagent.skills.parsers.registry import parse_attachment
    from finagent.skills.processors.controls import SCREEN_PATTERNS
    import re
    bank = dict(SCREEN_PATTERNS)["newly changed bank details"]
    assert not re.search(bank, "Meet our new account managers.", re.I)
    assert re.search(bank, "New account ending 8842.", re.I) and re.search(bank, "please use our new bank account", re.I)
    body = ("From: Accounts <a@x.example>\nSubject: FW: invoice\n\n> TAX INVOICE\n> Supplier: Evergreen Office Supplies\n"
            "> Invoice Number: EOS-1\n> Line | SKU | Description | Category | Qty | Unit Price | Amount\n"
            "> 1 | PEN | Pens | goods | 2.00 | 5.00 | 10.00\n> Total Due: AUD 11.00\n")
    doc = parse_attachment({"filename": "x.eml", "content_type": "message/rfc822", "content": body})
    assert doc["invoice_number"] == "EOS-1" and len(doc["lines"]) == 1
