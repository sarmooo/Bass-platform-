"""Build a small static documentation site from the Markdown chapters.

Converts every docs/*.md to a themed HTML page (intra-doc .md links rewritten to
.html), copies diagrams.html through, and writes an index. Output goes to
docs/_site, which the Pages workflow publishes. Run locally with:

    pip install markdown && python docs/build_site.py && open docs/_site/index.html
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
OUT = DOCS / "_site"

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a1a;--muted:#666;--accent:#2f6f4f;--border:#e6e6e3;--code:#f2f2ef}
@media (prefers-color-scheme:dark){:root{--bg:#16181a;--fg:#e8e8e6;--muted:#9aa0a6;--accent:#6fbf98;--border:#2a2d31;--code:#1e2124}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:46rem;margin:0 auto;padding:3rem 1.25rem 6rem}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{line-height:1.25;text-wrap:balance;margin-top:2.2em}
h1{font-size:2rem;margin-top:0}h2{font-size:1.4rem;border-bottom:1px solid var(--border);padding-bottom:.3em}
code{background:var(--code);padding:.15em .4em;border-radius:4px;font-size:.9em}
pre{background:var(--code);padding:1rem;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto}
th,td{border:1px solid var(--border);padding:.45em .7em;text-align:left}
blockquote{border-left:3px solid var(--accent);margin:1em 0;padding:.2em 1em;color:var(--muted)}
.nav{font-size:.85rem;color:var(--muted);margin-bottom:2rem}
.nav a{color:var(--muted)}
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Bass</title><style>{css}</style></head>
<body><main><div class="nav"><a href="index.html">← Bass docs</a></div>{body}</main></body></html>"""


def _title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def build() -> list[tuple[str, str]]:
    OUT.mkdir(exist_ok=True)
    md = markdown.Markdown(extensions=["extra", "toc", "tables", "fenced_code", "sane_lists"])
    pages: list[tuple[str, str]] = []
    for src in sorted(DOCS.glob("*.md")):
        text = src.read_text()
        md.reset()
        body = md.convert(text)
        # Rewrite intra-doc links (foo.md -> foo.html); leave external/parent links.
        body = re.sub(r'href="(?!https?:|\.\./|/)([^"?#]+)\.md((?:[?#][^"]*)?)"',
                      r'href="\1.html\2"', body)
        title = _title(text, src.stem)
        (OUT / f"{src.stem}.html").write_text(PAGE.format(title=title, css=CSS, body=body))
        pages.append((src.stem, title))

    diagrams = DOCS / "diagrams.html"
    has_diagrams = diagrams.exists()
    if has_diagrams:
        (OUT / "diagrams.html").write_text(diagrams.read_text())

    items = "".join(f'<li><a href="{name}.html">{title}</a></li>' for name, title in pages)
    if has_diagrams:
        items += '<li><a href="diagrams.html">Architecture diagrams</a></li>'
    index = (f"<h1>Bass — Documentation</h1>"
             f"<p>Durable, multi-tenant AI automation platform. Chapters:</p><ul>{items}</ul>")
    (OUT / "index.html").write_text(PAGE.format(title="Documentation", css=CSS, body=index))
    return pages


if __name__ == "__main__":
    built = build()
    print(f"built {len(built) + 1} pages into {OUT}")
