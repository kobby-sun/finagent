#!/usr/bin/env python3
"""Build an index.html gallery for a folder of run diagrams (PNG thumbnails + links to PDF/SVG).

    python scripts/dev/diagram_gallery.py data/diagrams data/eval/diagrams
"""
import html
import sys
from collections import defaultdict
from pathlib import Path


def build(folder: Path) -> Path:
    groups = defaultdict(dict)
    for f in sorted(folder.iterdir()):
        if f.suffix in (".png", ".pdf", ".svg"):
            groups[f.stem][f.suffix[1:]] = f.name
    cards = []
    for stem, files in sorted(groups.items()):
        thumb = files.get("png") or files.get("svg")
        links = " · ".join(f'<a href="{html.escape(n)}">{k.upper()}</a>' for k, n in sorted(files.items()))
        status = "fail" if stem.endswith("_FAIL") else ""
        cards.append(f'<div class="card {status}"><a href="{html.escape(thumb or "")}"><img loading="lazy" src="{html.escape(thumb or "")}"></a>'
                     f'<div class="cap"><b>{html.escape(stem)}</b><br>{links}</div></div>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>finagent run diagrams - {html.escape(folder.name)}</title>
<style>body{{font-family:Helvetica,Arial,sans-serif;margin:24px;background:#f6f7f9;color:#212836}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #d5dae3;border-radius:10px;overflow:hidden}}.card.fail{{border:2px solid #c74040}}
.card img{{width:100%;display:block;border-bottom:1px solid #eee}}.cap{{padding:10px;font-size:13px}}</style></head>
<body><h2>finagent run diagrams - {html.escape(str(folder))}</h2><p>{len(groups)} run(s). Click a thumbnail for full size.</p>
<div class="grid">{''.join(cards)}</div></body></html>"""
    out = folder / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["data/diagrams", "data/eval/diagrams"]:
        p = Path(arg)
        if p.is_dir():
            print(build(p))
