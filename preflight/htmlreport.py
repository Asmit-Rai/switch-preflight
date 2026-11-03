"""Render a verdict document as one self-contained HTML page: inline CSS, no JavaScript, nothing loaded.

Every piece of text that comes from the verdict or the project goes through `html.escape`: check details
quote file names and README text, which whoever wrote the project controls. The only links are the
repository and the commit.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .gitutil import short_sha
from .model import PASS, READY
from .pack import commit_url, project_name, read_readme, repo_url, write_pack
from .report import summary_line, verdict_line

FOOTER = "Made by Preflight. The model proposes, code decides."
PILL_CLASS = {"PASS": "pass", "FAIL": "fail", "WARN": "warn", "SKIP": "skip"}
COLUMNS = ("#", "ID", "Title", "Required", "Status", "Detail")
# The page may load nothing and run nothing, even if a piece of text slipped through unescaped.
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
# Control and bidi characters can hide or reorder text on screen, so they are shown as \uXXXX.
_INVISIBLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")

STYLE = """
:root{color-scheme:light dark;--bg:#f6f8fa;--fg:#1f2328;--muted:#59636e;--card:#fff;--line:#d1d9e0;--link:#0969da;
--pass:#1a7f37;--pass-bg:#dafbe1;--fail:#cf222e;--fail-bg:#ffebe9;--warn:#9a6700;--warn-bg:#fff8c5;--skip:#59636e;--skip-bg:#eff2f5}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--card:#151b23;--line:#3d444d;--link:#4493f8;
--pass:#3fb950;--pass-bg:#122d1c;--fail:#f85149;--fail-bg:#3a1416;--warn:#d29922;--warn-bg:#33270b;--skip:#9198a1;--skip-bg:#262c36}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main,footer{max-width:960px;margin:0 auto;padding:16px}
footer{color:var(--muted);font-size:.9rem;padding-bottom:32px}
.eyebrow{margin:8px 0 0;color:var(--muted);font-weight:600}
h1{font-size:1.6rem;margin:0 0 12px;overflow-wrap:anywhere}
h2{font-size:1.15rem;margin:28px 0 8px}
.banner{border-radius:12px;padding:18px 22px;margin:0 0 20px;color:#fff;print-color-adjust:exact;-webkit-print-color-adjust:exact}
.banner.ready{background:#1a7f37}
.banner.not-ready{background:#cf222e}
.banner p{margin:4px 0 0;overflow-wrap:anywhere}
.banner .state{margin:0;font-size:clamp(2.2rem,9vw,3.4rem);font-weight:800;line-height:1.1;letter-spacing:.02em}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
thead th{color:var(--muted);font-size:.85rem;white-space:nowrap}
.facts th{width:12rem;color:var(--muted);font-weight:600}
.checks td:nth-child(1),.checks td:nth-child(4),.checks td:nth-child(5){white-space:nowrap}
.checks td:nth-child(2),.checks td:nth-child(3){overflow-wrap:break-word}
code,.lines{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
a{color:var(--link)}
.muted{color:var(--muted)}
.pill{display:inline-block;padding:0 9px;border:1px solid;border-radius:999px;font-size:.8rem;font-weight:700;letter-spacing:.04em}
.pill.pass{color:var(--pass);background:var(--pass-bg)}
.pill.fail{color:var(--fail);background:var(--fail-bg)}
.pill.warn{color:var(--warn);background:var(--warn-bg)}
.pill.skip{color:var(--skip);background:var(--skip-bg)}
.fix{margin:6px 0 0;color:var(--muted)}
.lines{padding:12px 14px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow-wrap:anywhere}
.lines p{margin:0}
@media screen and (max-width:640px){
main,footer{padding:12px}
.facts th,.facts td{display:block;width:auto}
.facts th{border-bottom:0;padding-bottom:0}
.checks thead{display:none}
.checks,.checks tbody,.checks tr,.checks td{display:block}
.checks{border:0;background:none}
.checks tr{margin:0 0 10px;padding:6px 12px;background:var(--card);border:1px solid var(--line);border-radius:8px}
.checks td{border:0;padding:3px 0;white-space:normal}
.checks td::before{margin-right:6px;color:var(--muted);font-weight:600}
.checks td:nth-child(1)::before{content:"Check"}
.checks td:nth-child(2)::before{content:"ID"}
.checks td:nth-child(3)::before{content:"Title"}
.checks td:nth-child(4)::before{content:"Required"}
.checks td:nth-child(5)::before{content:"Status"}
.checks td:nth-child(6)::before{content:"Detail"}
}
@media print{
:root{color-scheme:light;--bg:#fff;--fg:#000;--muted:#444;--card:#fff;--line:#999;--link:#000;
--pass:#1a7f37;--pass-bg:#fff;--fail:#cf222e;--fail-bg:#fff;--warn:#7a5200;--warn-bg:#fff;--skip:#444;--skip-bg:#fff}
body{font-size:11pt}
main,footer{max-width:none;padding:0}
tr{break-inside:avoid}
thead{display:table-header-group}
}
"""


def _text(value: Any) -> str:
    """Escape for HTML; control and bidi characters become a visible \\uXXXX so nothing hides on screen."""
    return html.escape(_INVISIBLE.sub(lambda m: f"\\u{ord(m.group()):04x}", str(value)))


def _linkable(url: str) -> bool:
    """A plain http(s) URL with a host: anything else (javascript:, data:, a path) stays text."""
    if not url.isprintable() or " " in url or "\\" in url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _repository(url: str) -> str:
    """The repository fact: the URL itself is the link text, so the reader sees where it goes."""
    if _linkable(url):
        return f'<a href="{html.escape(url)}" rel="noopener noreferrer">{_text(url)}</a>'
    return _text(url)


def _commit(project: Path, commit: str | None) -> str:
    """Short sha as a link to GitHub when it is pushed, else `(not pushed yet)`; the full sha below."""
    if not commit:
        return "none (the project had no commits)"
    short = f"<code>{_text(short_sha(commit))}</code>"
    url = commit_url(project, commit)
    first = f'<a href="{html.escape(url)}" rel="noopener noreferrer">{short}</a>' if url else f"{short} (not pushed yet)"
    return f'{first}<br><code class="muted">{_text(commit)}</code>'


def _banner(doc: dict[str, Any], gate_message: str | None) -> str:
    ready = doc["verdict"] == READY
    lines = [f'<p class="state">{"READY" if ready else "NOT READY"}</p>']
    if gate_message:
        lines.append(f"<p>{_text(gate_message)}</p>")
    if not ready:
        lines.append(f"<p>Blocking: {_text(', '.join(doc['blocking']))}</p>")
    elif not gate_message:
        lines.append("<p>Every required check passed.</p>")
    if ready and doc["dirty"]:
        lines.append("<p>Note: the working tree had uncommitted changes, so the gate stays closed until they are committed.</p>")
    return f'<section class="banner {"ready" if ready else "not-ready"}">{"".join(lines)}</section>'


def _facts(doc: dict[str, Any], project: Path, name: str, meta: dict[str, Any], approved_sha: str | None) -> str:
    rules = doc["ruleset"]
    rows = [
        ("Project", _text(name)),
        ("Repository", _repository(repo_url(project, meta))),
        ("Commit", _commit(project, doc["commit"])),
        ("Verified at", _text(doc["created_at"])),
        ("Ruleset", f"{_text(rules['name'])} v{_text(rules['version'])} <code>sha256:{_text(rules['sha256'][:12])}</code>"),
    ]
    if approved_sha:
        matches = str(rules["sha256"]).lower().startswith(approved_sha.lower())
        rows.append((
            "Approved fingerprint",
            f"<code>sha256:{_text(approved_sha)}</code> " + ("(the ruleset matches)" if matches else "(the ruleset does NOT match)"),
        ))
    rows.append(("Working tree", "had uncommitted changes" if doc["dirty"] else "clean"))
    body = "\n".join(f'<tr><th scope="row">{key}</th><td>{value}</td></tr>' for key, value in rows)
    return f'<table class="facts">\n{body}\n</table>'


def _checks(doc: dict[str, Any]) -> str:
    head = "".join(f'<th scope="col">{column}</th>' for column in COLUMNS)
    rows = []
    for n, row in enumerate(doc["results"], start=1):
        status = row["status"]
        detail = _text(row["detail"])
        if status != PASS and row["hint"]:
            detail += f'<p class="fix">Fix: {_text(row["hint"])}</p>'
        rows.append(
            f"<tr><td>{n}</td><td><code>{_text(row['id'])}</code></td><td>{_text(row['title'])}</td>"
            f"<td>{'yes' if row['required'] else 'no'}</td>"
            f'<td><span class="pill {PILL_CLASS.get(status, "skip")}">{_text(status)}</span></td><td>{detail}</td></tr>'
        )
    return f'<table class="checks">\n<thead><tr>{head}</tr></thead>\n<tbody>\n' + "\n".join(rows) + "\n</tbody>\n</table>"


def render_html(
    doc: dict[str, Any],
    project: Path,
    meta: dict[str, Any] | None = None,
    gate_message: str | None = None,
    approved_sha: str | None = None,
) -> str:
    """The whole page for one verdict document.

    `gate_message` is the gate's `GATE OK: ...` line when the page is made by `pack`, and `approved_sha`
    the fingerprint the gate was checked with. The SUMMARY and VERDICT lines are exactly the report's.
    """
    meta = meta or {}
    name = project_name(project, read_readme(project), meta)
    state = "READY" if doc["verdict"] == READY else "NOT READY"
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>{state} - Preflight - {_text(name)}</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        "<main>",
        '<p class="eyebrow">Preflight report</p>',
        f"<h1>{_text(name)}</h1>",
        _banner(doc, gate_message),
        _facts(doc, project, name, meta, approved_sha),
        "<h2>Checks</h2>",
        _checks(doc),
        "<h2>Verdict</h2>",
        '<div class="lines">'
        f'<p id="summary-line">{html.escape(summary_line(doc))}</p>'
        f'<p id="verdict-line">{html.escape(verdict_line(doc))}</p>'
        "</div>",
        '<p class="muted">Compare these two lines with the last two lines of the chat report.</p>',
        "</main>",
        f"<footer>{FOOTER}</footer>",
        "</body>",
        "</html>",
        "",
    ])


def write_html(path: Path, page: str) -> None:
    """Write the page atomically (temp file, then rename), the same way as the Markdown pack."""
    write_pack(path, page)
