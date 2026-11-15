# Item identity: is the invoiced item the ordered and received item?

_Internal guidance for the three_way_match procedure (illustrative). FIN-POL-002 prevails._

The deterministic match proves quantities and prices agree **per line**. It cannot see whether the line still
describes the **same thing**. Check identity on these points:

| Check | Same item (no concern) | Different item (HOLD for the receipter) |
|---|---|---|
| Unit of measure | "each" vs "ea"; "roll" vs "rl" | "each" ordered, "pack of 3" / "box of 12" invoiced |
| Pack size | description adds detail already in the PO | pack or bundle size appears only on the invoice |
| Grade / model | same model number | "premium", "pro", "upgraded", a different model or size |
| Service period | same period as the PO / receipt | a different month, an extra visit, "after hours" |
| Substitution | none mentioned | "substitute", "equivalent", "replacement item supplied" |

Rules:
- A changed unit or pack size changes the real quantity even when the invoiced quantity number matches - HOLD so the
  receipter confirms what was physically received.
- Similar wording is never a reason to accept a price or quantity the deterministic check failed.
