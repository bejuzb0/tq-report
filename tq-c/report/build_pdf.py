"""Build report.pdf from report.md via markdown -> HTML -> PDF (xhtml2pdf).

We render the markdown to a fragment with pandoc, wrap it in a small HTML
template with print-oriented CSS, then compile the HTML to PDF with
xhtml2pdf (pure Python, no system deps)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from xhtml2pdf import pisa

HERE = Path(__file__).resolve().parent
MD = HERE / "report.md"
HTML = HERE / "report.html"
PDF = HERE / "report.pdf"

body = subprocess.check_output(
    ["pandoc", str(MD), "-f", "markdown", "-t", "html5"],
    cwd=HERE,
).decode("utf-8")

# xhtml2pdf wants explicit absolute paths for images; rewrite the relative
# "../results/figures/..." references.
body = body.replace('src="../results/', f'src="{HERE.parent}/results/')

css = """
@page { size: letter; margin: 0.9in 0.9in 1.0in 0.9in; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.35; color: #1a1a1a; }
h1 { font-size: 17pt; margin-top: 18pt; margin-bottom: 6pt; }
h2 { font-size: 13pt; margin-top: 14pt; margin-bottom: 4pt; }
h3 { font-size: 11.5pt; margin-top: 10pt; margin-bottom: 3pt; }
p  { margin: 5pt 0; text-align: justify; }
table { border-collapse: collapse; margin: 8pt 0; font-size: 9.5pt; width: 100%; }
th, td { border: 1px solid #888; padding: 3pt 6pt; }
th { background: #eee; }
img { max-width: 100%; }
figcaption, .caption { font-size: 9pt; color: #555; font-style: italic;
                      margin-top: 2pt; }
code, pre { font-family: Menlo, Monaco, monospace; font-size: 9pt; }
pre { background: #f4f4f4; padding: 6pt; border-radius: 3pt; white-space: pre-wrap; }
"""

html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>TurboQuant on CLIP — CS 639 Report</title>
<style>{css}</style>
</head><body>{body}</body></html>"""

HTML.write_text(html)

with PDF.open("wb") as f:
    result = pisa.CreatePDF(html, dest=f)

if result.err:
    raise SystemExit(f"xhtml2pdf reported {result.err} errors")
print(f"wrote {PDF}")
