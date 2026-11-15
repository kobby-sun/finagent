from datetime import date
from decimal import Decimal

from finagent.agent.calc import add_business_days, business_days_between, q
from finagent.redaction import mask_account, redact, redact_text
from finagent.tools.finance_tools import normalise_invoice_ref


def test_decimal_rounding_half_up():
    assert q(Decimal("0.005")) == Decimal("0.01")
    assert q(Decimal("404.001")) == Decimal("404.00")


def test_business_days():
    thu = date(2026, 11, 12)
    assert add_business_days(thu, 3) == date(2026, 11, 17)          # skips weekend
    assert business_days_between(date(2026, 11, 11), thu) == 1
    assert business_days_between(date(2026, 11, 13), date(2026, 11, 16)) == 1  # Fri -> Mon


def test_invoice_ref_normalisation():
    assert normalise_invoice_ref("EOS-00123") == normalise_invoice_ref("EOS00123") == "EOS123"
    assert normalise_invoice_ref("inv/ac 7781") == "INVAC7781"


def test_bank_details_masked_everywhere():
    assert mask_account("062-000 1234 8842") == "****8842"
    text = redact_text("Pay BSB 062-000 account 0620001012348842 or email jo.smith@supplier.example")
    assert "0620001012348842" not in text and "****8842" in text
    assert "jo.smith@" not in text
    d = redact({"payment_details": {"account_number": "0620001012348842", "bsb": "062-000"}, "api_key": "sk-abc"})
    assert d["payment_details"]["account_number"] == "****8842"
    assert d["payment_details"]["bsb"] == "[REDACTED]" and d["api_key"] == "[REDACTED]"
