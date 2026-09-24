#!/usr/bin/env python3
"""Generate the static website in site/ from the dataset, its notes and its schema.

Each record gets a page at block/{hash}/, so a search for a block hash can find it.
The index lists every record; notes/ renders docs/notes.md and reported/ lists the reported blocks.
"""

import html
import json
import posixpath
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from itertools import dropwhile, takewhile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import markdown
from markdown.extensions.toc import TocExtension
from bitcoin.core import CBlockHeader, CTransaction, b2lx

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "invalid-blocks.jsonl"
REPORTED_PATH = REPO_ROOT / "data" / "reported-blocks.jsonl"
NOTES_PATH = REPO_ROOT / "docs" / "notes.md"
SCHEMA_PATH = REPO_ROOT / "docs" / "schema.md"
BLOCKS_DIR = REPO_ROOT / "blocks"
PROOFS_DIR = REPO_ROOT / "proofs"
CSS_PATH = Path(__file__).with_name("website.css")
JS_PATH = Path(__file__).with_name("website.js")
OUT_DIR = REPO_ROOT / "site"

SITE_URL = "https://bitcoin-data.github.io/invalid-blocks"
REPO_URL = "https://github.com/bitcoin-data/invalid-blocks"
BLOB_URL = f"{REPO_URL}/blob/main"
RAW_URL = f"{REPO_URL}/raw/main"
BLOCK_ROOT = "../../"  # block pages sit at block/{hash}/

RELATIVE_HREF = re.compile(r'href="(?!https?://|mailto:|#)([^"]+)"')
BLOCK_FILE = re.compile(r"\.\./blocks/\d+-([0-9a-f]{64})\.bin")
HEADING = re.compile(r'<h3 id="([^"]+)">(.*?)</h3>')

esc = html.escape


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def utc(timestamp: int, fmt: str = "%Y-%m-%d %H:%M:%S UTC") -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(fmt)


def root_of(path: str) -> str:
    return "../" * path.count("/") or "./"


def block_href(root: str, block_hash: str) -> str:
    return f"{root}block/{block_hash}/"


def dl(rows: list[tuple[str, str]]) -> str:
    return '<dl class="kv">' + "".join(f"<dt>{key}</dt><dd>{value}</dd>" for key, value in rows) + "</dl>"


def schema_rows(schema: str, heading: str) -> dict[str, list[str]]:
    """Read the first Markdown table under heading, keyed by its first cell."""
    lines = schema.split(heading, 1)[1].splitlines()
    table = list(takewhile(lambda line: line.startswith("|"), dropwhile(lambda line: not line.startswith("|"), lines)))
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in table[2:]]
    return {row[0].strip("`"): row for row in cells}


def rule_text(schema: str) -> dict[str, tuple[str, str]]:
    """Map each rule to its Core check and CI evidence from the schema tables, rendered for a block page."""
    checks = schema_rows(schema, "## Rules and Core reject strings")
    evidence = schema_rows(schema, "## Evidence enforced by CI")

    def render(cell: str) -> str:
        inline = markdown.markdown(cell).removeprefix("<p>").removesuffix("</p>")
        return rewrite_links(inline, BLOCK_ROOT)

    return {rule: (render(checks[rule][2]), render(evidence[rule][1])) for rule in checks}


def rewrite_links(body: str, root: str) -> str:
    """Resolve relative hrefs rendered from docs/*.md for a page at root: block files to block pages, notes to notes/, the rest to GitHub."""

    def resolve(match: re.Match[str]) -> str:
        path, _, anchor = match.group(1).partition("#")
        fragment = f"#{anchor}" if anchor else ""
        block = BLOCK_FILE.fullmatch(path)
        if block:
            return f'href="{block_href(root, block.group(1))}"'
        if path == "notes.md":
            return f'href="{root}notes/{fragment}"'
        return f'href="{BLOB_URL}/{posixpath.normpath(posixpath.join("docs", path))}{fragment}"'

    return RELATIVE_HREF.sub(resolve, body)


def render_notes(notes: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Render docs/notes.md without its title, with its contents list and its incident sections' heights and block-page excerpts."""
    converter = markdown.Markdown(extensions=["tables", TocExtension(toc_depth="2-3")])
    body = converter.convert(notes)
    body = re.sub(r"<h1[^>]*>.*?</h1>\s*", "", body, count=1)
    body = body.replace("<table>", '<div class="table"><table>').replace("</table>", "</table></div>")
    incidents = next((t for t in converter.toc_tokens if t["name"] == "Incident notes"), None)
    if incidents is None:
        raise ValueError(f"{NOTES_PATH} has no Incident notes section")
    sections = []
    for token in incidents["children"]:
        paragraph = re.search(rf'<h3 id="{re.escape(token["id"])}">.*?</h3>\s*<p>(.*?)</p>', body, re.S)
        sections.append({
            "id": token["id"],
            "html": token["html"],
            "heights": [int(h) for h in re.findall(r"\d+", token["name"].split(" - ")[0])],
            "excerpt": rewrite_links(paragraph.group(1), BLOCK_ROOT) if paragraph else "",
        })
    return body, converter.toc, sections


def link_heading_heights(body: str, root: str, records: list[dict[str, Any]]) -> str:
    """Link the heights in each incident heading to their block pages, where a height names a single record."""
    heights = Counter(r["height"] for r in records)
    single = {r["height"]: r["hash"] for r in records if heights[r["height"]] == 1}

    def link(height: re.Match[str]) -> str:
        block_hash = single.get(int(height.group(0)))
        return f'<a href="{block_href(root, block_hash)}">{height.group(0)}</a>' if block_hash else height.group(0)

    def heading(match: re.Match[str]) -> str:
        heights, dash, title = match.group(2).partition(" - ")
        return f'<h3 id="{match.group(1)}">{re.sub(r"\d+", link, heights)}{dash}{title}</h3>'

    return HEADING.sub(heading, body)


def note_links(records: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each record hash to the incident note naming its height, or naming a block with the same failing_prevout."""
    by_height = {height: incident for incident in incidents for height in incident["heights"]}
    links = {r["hash"]: by_height[r["height"]] for r in records if r["height"] in by_height}
    spends = {r["hash"]: r.get("context", {}).get("failing_prevout") for r in records}
    by_spend = {spends[block_hash]: incident for block_hash, incident in links.items() if spends[block_hash]}
    return {block_hash: by_spend[spend] for block_hash, spend in spends.items() if spend in by_spend} | links


# Evidence kinds, in index order, with their index card labels; a kind's badge reads as the name with spaces.
EVIDENCE_CARDS = {
    "full-block": "with full block",
    "spend-proof": "with P2SH spend proof",
    "coinbase-proof": "with coinbase proof",
    "header-only": "header only",
}


def stem(record: dict[str, Any]) -> str:
    return f"{record['height']}-{record['hash']}"


def evidence_on_file(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Name the kind of evidence on file for a record, with its proof file when it has one."""
    if (BLOCKS_DIR / f"{stem(record)}.bin").exists():
        return "full-block", None
    path = PROOFS_DIR / f"{stem(record)}.json"
    if not path.exists():
        return "header-only", None
    proof = json.loads(path.read_text())
    coinbase = CTransaction.deserialize(bytes.fromhex(proof["transaction"])).is_coinbase()
    return ("coinbase-proof" if coinbase else "spend-proof"), proof


def badge(kind: str, href: str = "", label: str = "") -> str:
    """Badge styled as kind; its text is label, or the kind with spaces for hyphens."""
    text = esc(label) if label else kind.replace("-", " ")
    if href:
        return f'<a class="badge {kind}" href="{href}">{text}</a>'
    return f'<span class="badge {kind}">{text}</span>'


def site_url(path: str) -> str:
    return f"{SITE_URL}/{path.removesuffix('index.html')}"


def page(path: str, title: str, description: str, body: str) -> str:
    root = root_of(path)

    def nav(href: str, label: str) -> str:
        current = ' aria-current="page"' if path == f"{href}index.html" else ""
        return f'<a href="{root}{href}"{current}>{label}</a>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{site_url(path)}">
<link rel="stylesheet" href="{root}style.css">
<script src="{root}website.js"></script>
</head>
<body>
<div class="wrap">
<nav>
<a class="brand" href="{root}">Bitcoin invalid blocks</a>
<span class="links">
{nav("", "Blocks")}
{nav("notes/", "Notes")}
{nav("reported/", "Reported")}
<a href="{BLOB_URL}/docs/schema.md">Schema</a>
<a href="{REPO_URL}">GitHub</a>
</span>
<button id="theme" type="button" aria-label="Switch between light and dark"></button>
</nav>
<main>
{body}
</main>
<footer>
Generated from <a href="{REPO_URL}">bitcoin-data/invalid-blocks</a>; data is dedicated to the public domain under <a href="{BLOB_URL}/LICENSE-DATA">CC0 1.0</a>.
CI checks the named failure of every record; the <a href="{BLOB_URL}/docs/schema.md#evidence-enforced-by-ci">schema</a> says what each check covers and what it does not.
</footer>
</div>
</body>
</html>
"""


def index_page(
    records: list[dict[str, Any]],
    reported: list[dict[str, Any]],
    on_file: dict[str, tuple[str, dict[str, Any] | None]],
) -> tuple[str, str]:
    path = "index.html"
    counts = Counter(kind for kind, _ in on_file.values())
    counts[""] = len(records)
    cards = "\n".join(
        f'<button class="card" type="button" data-filter="{kind}"><span class="k">{label}</span><span class="v">{counts[kind]}</span></button>'
        for kind, label in {"": "invalid blocks", **EVIDENCE_CARDS}.items()
    )
    chips = "".join(
        f'<button class="chip" type="button" data-filter="{esc(rule)}">{esc(rule)}<b>{count}</b></button>'
        for rule, count in Counter(r["rule"] for r in records).most_common()
    )
    attributes = {"height": ' class="num" aria-sort="ascending"', "observations": ' class="num"'}
    headers = "".join(
        f'<th{attributes.get(name, "")}><button type="button">{name}</button></th>'
        for name in ("height", "date", "hash", "rule", "pool", "evidence", "observations")
    )
    rows = []
    for record in records:
        kind, _ = on_file[record["hash"]]
        pool = record.get("context", {}).get("pool", "")
        date = utc(record["nTime"], "%Y-%m-%d")
        search = " ".join([str(record["height"]), record["hash"], record["rule"], record["core_reject_reason"], pool, date]).lower()
        rows.append(f"""<tr data-tags="{esc(record['rule'])} {kind}" data-search="{esc(search)}">
<td class="num mono">{record['height']}</td>
<td class="mono">{date}</td>
<td class="mono" data-key="{record['hash']}"><a href="{block_href(root_of(path), record['hash'])}" title="{record['hash']}">{record['hash'][:12]}…{record['hash'][-8:]}</a></td>
<td>{badge("rule", label=record["rule"])}</td>
<td data-key="{esc(pool)}">{esc(pool) or '<span class="dim">-</span>'}</td>
<td>{badge(kind)}</td>
<td class="num">{len(record.get('observations', []))}</td>
</tr>""")

    body = f"""<h1>Bitcoin invalid blocks</h1>
<p class="sub">Headers and blocks with valid proof of work that fail a named consensus rule.</p>
<div class="prose">
<p>A <a href="https://bitcoin-data.github.io/stale-blocks/">stale block</a> follows the rules but ends up outside the active chain; these blocks break them.
They were observed on the Bitcoin network or recovered from other sources, including chains that merge-mine with Bitcoin and archived block explorers.
The data is in <a href="{BLOB_URL}/data/invalid-blocks.jsonl"><code>data/invalid-blocks.jsonl</code></a>, and contributions are welcome in the <a href="{REPO_URL}">repository</a>.</p>
</div>
<div class="cards">
{cards}
<a class="card" href="reported/"><span class="k">reported, not established</span><span class="v">{len(reported)}</span></a>
</div>
<h2>Blocks</h2>
<div id="filters" hidden>
<div class="filter"><input id="filter" type="search" placeholder="Filter by height, hash, rule, pool or date" aria-label="Filter blocks">
<span><span id="shown">{len(records)}</span> of {len(records)}</span></div>
<div class="chips">{chips}</div>
</div>
<div class="table">
<table id="blocks">
<thead><tr>{headers}</tr></thead>
<tbody>
{"\n".join(rows)}
</tbody>
</table>
</div>"""
    description = f"{len(records)} Bitcoin blocks with valid proof of work that fail a named consensus rule, with headers, full blocks, evidence and observations."
    return path, page(path, "Bitcoin invalid blocks", description, body)


def context_rows(context: dict[str, Any]) -> list[tuple[str, str]]:
    """List a record's context for display: the pool fields share a row, and parent_kind shows with the previous block."""
    rows = []
    if "pool" in context:
        source = f' (<a href="{esc(context["pool_provenance"])}">source</a>)' if "pool_provenance" in context else ""
        rows.append(("pool", f'{esc(context["pool"])} <span class="dim">by {esc(context["pool_basis"])}{source}</span>'))
    for key, value in context.items():
        if key in ("pool", "pool_basis", "pool_provenance", "parent_kind"):
            continue
        shown = f'<span class="mono wrap-all">{esc(str(value))}</span>'
        if key == "parent_mtp":
            shown += f' <span class="dim">{utc(value)}</span>'
        rows.append((key, shown))
        if key == "coinbase_scriptsig_hex":
            text = "".join(chr(b) if 32 <= b < 127 else "·" for b in bytes.fromhex(value))
            rows.append(("scriptSig as text", f'<span class="mono wrap-all">{esc(text)}</span>'))
    return rows


def observations_panel(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return ""
    rows = []
    for obs in observations:
        child = esc(obs.get("child_chain", ""))
        if "child_height" in obs:
            child += f' <span class="dim">{obs["child_height"]}</span>'
        seen = utc(obs["first_seen"]) if "first_seen" in obs else ""
        url = esc(obs["provenance"])
        rows.append(
            f'<tr><td class="mono">{esc(obs["channel"])}</td><td>{esc(obs["source"])}</td><td>{child}</td>'
            f'<td class="mono">{seen}</td><td><a class="clip" href="{url}" title="{url}">{url}</a></td></tr>'
        )
    return f"""<section class="panel"><h2>Observations ({len(observations)})</h2>
<p class="lead">Where the block was seen or recovered. Observations record acquisition; they do not establish the failure.</p>
<div class="table"><table><thead><tr><th>channel</th><th>source</th><th>child chain</th><th>first seen</th><th>provenance</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></section>"""


def block_page(
    position: int,
    records: list[dict[str, Any]],
    rules: dict[str, tuple[str, str]],
    incident: dict[str, Any] | None,
    evidence: tuple[str, dict[str, Any] | None],
) -> tuple[str, str]:
    record = records[position]
    path = f"block/{record['hash']}/index.html"
    root = BLOCK_ROOT
    context = record.get("context", {})
    header = CBlockHeader.deserialize(bytes.fromhex(record["header"]))
    name = stem(record)
    kind, proof = evidence
    check, ci_checks = rules[record["rule"]]

    note_html = ""
    if incident:
        note_html = f"""<p>Incident note: <a href="{root}notes/#{incident['id']}">{incident['html']}</a></p>
<p class="excerpt">{incident['excerpt']}</p>"""
    rule_is_reject = record["rule"] == record["core_reject_reason"]
    rule = badge("rule", f'{root}#{esc(record["rule"])}', record["rule"])
    if rule_is_reject:
        rule_rows = [("rule and Core reject", rule)]
    else:
        rule_rows = [("rule", rule), ("Core reject", f"<code>{esc(record['core_reject_reason'])}</code>")]
    verdict = dl(rule_rows + [("Core check", check), ("CI checks", ci_checks)])

    files = []
    if kind == "full-block":
        size = (BLOCKS_DIR / f"{name}.bin").stat().st_size
        files.append(("full block", f'<a class="wrap-all" href="{RAW_URL}/blocks/{name}.bin">{name}.bin</a> <span class="dim">{size:,} bytes</span>'))
    elif proof:
        proved = "coinbase" if kind == "coinbase-proof" else "failing transaction"
        placement = "its merkle branch" if "merkle_branch" in proof else "the block's ordered txids"
        files.append(("proof file", f'<a class="wrap-all" href="{BLOB_URL}/proofs/{name}.json">{name}.json</a> <span class="dim">{proved} and {placement}</span>'))
    files.append(("record", f'<a href="{BLOB_URL}/data/invalid-blocks.jsonl#L{position + 1}">data/invalid-blocks.jsonl, line {position + 1}</a>'))

    parent = next((r for r in records if r["hash"] == record["prev_hash"]), None)
    previous = f'<span class="mono">{record["prev_hash"]}</span>'
    if parent:
        previous = f'<a class="mono" href="{block_href(root, parent["hash"])}">{parent["hash"]}</a> <span class="dim">(invalid, in this dataset)</span>'
    elif context.get("parent_kind") == "canonical":
        previous += f' <span class="dim">(canonical, <a href="https://mempool.space/block/{record["prev_hash"]}">mempool.space</a>)</span>'
    elif "parent_kind" in context:
        previous += f' <span class="dim">({esc(context["parent_kind"])})</span>'
    header_rows = [
        ("version", f'0x{header.nVersion & 0xffffffff:08x} <span class="dim">({header.nVersion})</span>'),
        ("previous block", previous),
        *(("child in dataset", f'<a class="mono" href="{block_href(root, r["hash"])}">{r["hash"]}</a> <span class="dim">height {r["height"]}</span>')
          for r in records if r["prev_hash"] == record["hash"]),
        ("merkle root", f'<span class="mono">{b2lx(header.hashMerkleRoot)}</span>'),
        ("time", f'{utc(header.nTime)} <span class="dim">({header.nTime})</span>'),
        ("bits", f'0x{header.nBits:08x} <span class="dim">difficulty {header.difficulty:,.0f}</span>'),
        ("nonce", str(header.nNonce)),
        ("header hex", f'<span class="mono dim wrap-all">{record["header"]}</span>'),
    ]
    shown_context = context_rows(context)
    context_html = f'<section class="panel"><h2>Context</h2>{dl(shown_context)}</section>' if shown_context else ""

    before = records[position - 1] if position > 0 else None
    after = records[position + 1] if position + 1 < len(records) else None
    pager = f"""<div class="pager">
<span>{f'<a href="{block_href(root, before["hash"])}">← {before["height"]}</a>' if before else ""}</span>
<span>{f'<a href="{block_href(root, after["hash"])}">{after["height"]} →</a>' if after else ""}</span>
</div>"""

    body = f"""<h1>Invalid block {record['height']}</h1>
<p class="hash">{record['hash']}</p>
<div class="columns">
<div>
<section class="panel verdict"><h2>Why it is invalid</h2>{verdict}{note_html}</section>
<section class="panel"><h2>Header</h2>{dl(header_rows)}</section>
</div>
<div>
<section class="panel"><h2>Evidence on file {badge(kind, f"{root}#{kind}")}</h2>{dl(files)}</section>
{context_html}
</div>
</div>
{observations_panel(record.get("observations", []))}
{pager}"""
    title = f"Invalid Bitcoin block {record['height']} {record['hash']}"
    by_pool = f", mined by {context['pool']}" if "pool" in context else ""
    reject = "" if rule_is_reject else f" ({record['core_reject_reason']})"
    description = (
        f"Bitcoin block {record['hash']} at height {record['height']}{by_pool}, header time {utc(record['nTime'], '%Y-%m-%d')}, "
        f"fails {record['rule']}{reject}. Header, evidence and observations."
    )
    return path, page(path, title, description, body)


def notes_page(body: str, contents: str, records: list[dict[str, Any]]) -> tuple[str, str]:
    path = "notes/index.html"
    root = root_of(path)
    body = link_heading_heights(rewrite_links(body, root), root, records)
    html_body = f"""<h1>Notes</h1>
<p class="sub">Rendered from <a href="{BLOB_URL}/docs/notes.md"><code>docs/notes.md</code></a>.</p>
<section class="panel"><h2>Contents</h2>{contents}</section>
<article class="prose">
{body}
</article>"""
    description = "Notes on replaying full blocks, pool attributions, coinbase proofs, reported blocks and each invalid-block incident."
    return path, page(path, "Notes · Bitcoin invalid blocks", description, html_body)


def reported_page(reported: list[dict[str, Any]]) -> tuple[str, str]:
    path = "reported/index.html"
    rows = []
    for record in reported:
        recovered = badge("header-only", label="header") if "header" in record else '<span class="dim">hash only</span>'
        sources = " ".join(f'<a href="{esc(url)}">{esc(urlparse(url).hostname.removeprefix("www."))}</a>' for url in record["sources"])
        rows.append(f"""<tr id="{record['hash']}"><td class="num mono">{record['height']}</td>
<td class="mono wrap-all">{record['hash']}</td><td>{recovered}</td><td>{esc(record['reported_failure'])}</td><td>{sources}</td></tr>""")
    body = f"""<h1>Reported blocks</h1>
<p class="sub">Blocks a contemporary source reported as invalid, not admitted because the header or block needed to establish the failure is missing.</p>
<div class="prose"><p>Issues labelled <a href="{REPO_URL}/issues?q=label%3Areported"><code>reported</code></a> record what has been searched for each case and are the place to bring the missing data.
The <a href="{root_of(path)}notes/#reported-blocks">notes</a> describe each group.</p></div>
<div class="table"><table>
<thead><tr><th class="num">height</th><th>hash</th><th>recovered</th><th>reported failure</th><th>sources</th></tr></thead>
<tbody>
{"\n".join(rows)}
</tbody></table></div>"""
    description = "Bitcoin blocks reported as invalid whose failure is not yet established, with the reports and what has been recovered."
    return path, page(path, "Reported invalid blocks · Bitcoin invalid blocks", description, body)


def sitemap(paths: list[str]) -> str:
    entries = "\n".join(f"<url><loc>{site_url(path)}</loc></url>" for path in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n'


def main() -> None:
    records = load_jsonl(DATA_PATH)
    reported = load_jsonl(REPORTED_PATH)
    rules = rule_text(SCHEMA_PATH.read_text())
    notes, contents, incidents = render_notes(NOTES_PATH.read_text())
    links = note_links(records, incidents)
    on_file = {r["hash"]: evidence_on_file(r) for r in records}
    pages = [
        index_page(records, reported, on_file),
        notes_page(notes, contents, records),
        reported_page(reported),
        *(block_page(position, records, rules, links.get(r["hash"]), on_file[r["hash"]]) for position, r in enumerate(records)),
    ]

    files = pages + [
        ("sitemap.xml", sitemap([path for path, _ in pages])),
        ("style.css", CSS_PATH.read_text()),
        ("website.js", JS_PATH.read_text()),
    ]

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    for path, content in files:
        (OUT_DIR / path).parent.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / path).write_text(content)
    print(f"Generated {OUT_DIR} ({len(records)} block pages)")


if __name__ == "__main__":
    main()
