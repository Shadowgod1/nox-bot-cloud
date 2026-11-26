"""Builds the Nox Bot manual (HTML) from iler-vault/docs; printed to PDF with headless Edge.

Customer brand: "Nox Bot" (Nox service, noxsw.kz). The chapter texts come from the vault and must be
rebranded there; this script only owns the cover, the title and the chapter headings.
"""
import html
import os
import re
import sys

# needs: pip install markdown==3.7
# usage: python scripts/build-manual.py <iler-vault>/docs build/manual.html <version>
#        msedge --headless=new --no-pdf-header-footer --print-to-pdf=Nox-Bot-instrukciya-<ver>.pdf file:///.../build/manual.html
import markdown  # noqa: E402

DOCS = sys.argv[1]
OUT = sys.argv[2]
VERSION = sys.argv[3]
ORDER = [
    ("README", "О продукте"),
    ("requirements", "Системные требования"),
    ("install-windows", "Установка на Windows"),
    ("install-linux", "Установка на Ubuntu Server"),
    ("first-setup", "Первый запуск"),
    ("user-guide", "Руководство пользователя"),
    ("coins", "Коины"),
    ("technician", "Для техника Nox service"),
    ("troubleshooting", "Решение проблем"),
    ("security", "Безопасность"),
]

sections, toc = [], []
for i, (name, title) in enumerate(ORDER, 1):
    text = open(os.path.join(DOCS, name + ".md"), encoding="utf-8").read()
    # links to other docs -> in-document anchors; external links unchanged
    text = re.sub(r"\]\(([a-z-]+)\.md(#[^)]*)?\)", lambda m: f"](#sec-{m.group(1)})", text)
    text = re.sub(r"\[\[([^\]|]+)(\|([^\]]+))?\]\]", lambda m: m.group(3) or m.group(1), text)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    # first h1 of each file becomes the chapter heading with a number
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, count=1, flags=re.S)
    sections.append(f'<section class="chapter" id="sec-{name}"><h1><span class="num">{i}</span>{html.escape(title)}</h1>{body}</section>')
    toc.append(f'<li><a href="#sec-{name}"><span class="num">{i}</span>{html.escape(title)}</a></li>')

doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Nox Bot — инструкция</title>
<style>
@page {{ size: A4; margin: 18mm 16mm 20mm 16mm; }}
:root {{ --ink:#1c2321; --ink2:#55605c; --line:#d9dedb; --accent:#3f6ad8; --soft:#eef2fb; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", Arial, sans-serif; color: var(--ink); font-size: 10.5pt; line-height: 1.5; margin: 0; }}
.cover {{ height: 255mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }}
.cover .brand {{ font-size: 13pt; letter-spacing: .4em; font-weight: 700; color: var(--accent); }}
.cover h1 {{ font-size: 30pt; margin: 10mm 0 4mm; line-height: 1.15; }}
.cover p {{ color: var(--ink2); font-size: 12pt; max-width: 140mm; }}
.cover .meta {{ margin-top: 18mm; font-size: 10pt; color: var(--ink2); border-top: 1px solid var(--line); padding-top: 4mm; }}
.toc {{ page-break-after: always; }}
.toc h2 {{ font-size: 18pt; margin: 0 0 6mm; }}
.toc ol {{ list-style: none; padding: 0; margin: 0; }}
.toc li {{ border-bottom: 1px solid var(--line); padding: 2.6mm 0; font-size: 12pt; }}
.toc a {{ color: var(--ink); text-decoration: none; }}
.num {{ display: inline-block; min-width: 9mm; color: var(--accent); font-weight: 700; }}
.chapter {{ page-break-before: always; }}
.chapter > h1 {{ font-size: 20pt; border-bottom: 2px solid var(--accent); padding-bottom: 2mm; margin: 0 0 5mm; }}
h2 {{ font-size: 14pt; margin: 7mm 0 2mm; color: var(--accent); page-break-after: avoid; }}
h3 {{ font-size: 12pt; margin: 5mm 0 1.5mm; page-break-after: avoid; }}
h4 {{ font-size: 11pt; margin: 4mm 0 1mm; }}
p, li {{ orphans: 3; widows: 3; }}
a {{ color: var(--accent); }}
code {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 9.2pt; background: var(--soft); padding: .5px 3px; border-radius: 3px; word-break: break-all; }}
pre {{ background: var(--soft); border: 1px solid var(--line); border-radius: 5px; padding: 3mm; white-space: pre-wrap; word-break: break-all; page-break-inside: avoid; }}
pre code {{ background: none; padding: 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 3mm 0; font-size: 9.6pt; page-break-inside: auto; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid var(--line); padding: 1.6mm 2.2mm; text-align: left; vertical-align: top; }}
th {{ background: var(--soft); }}
blockquote {{ border-left: 3px solid var(--accent); margin: 3mm 0; padding: 1mm 4mm; color: var(--ink2); background: #f6f8fd; }}
</style></head><body>
<div class="cover"><div class="brand">NOX</div>
<h1>Nox Bot<br>Инструкция по установке и работе</h1>
<p>WhatsApp-ассистент на базе языковых моделей, который работает на компьютере или сервере вашего бизнеса.</p>
<div class="meta">Версия {html.escape(VERSION)} · для владельца, операторов и техника Nox service · noxsw.kz</div></div>
<div class="toc"><h2>Содержание</h2><ol>{''.join(toc)}</ol></div>
{''.join(sections)}
</body></html>"""
open(OUT, "w", encoding="utf-8").write(doc)
print("html ok", len(doc))
