#!/usr/bin/env python3
"""Crawl DomiNations War data and report inconsistent stat labels.

The report examines benefit names from Legendary War Artifact Statistic tables
and Councilor War Chamber tables.  It flags HP/DMG abbreviations, noncanonical
capitalization, and conservative likely misspellings of Hitpoints or Damage.
Only Python's standard library and the shared crawler helpers are used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from crawl_councilors import (
    CrawlError,
    api_request,
    clean_wikitext,
    discover_councilors,
    remove_cell_attributes,
    source_url,
)


ARTIFACTS_PAGE = "Legendary War Artifacts"
ARTIFACTS_SOURCE_URL = (
    "https://dominations.fandom.com/wiki/"
    "Legendary_War_Artifacts#List_of_Legendary_War_Artifacts"
)
COUNCILORS_PAGE = "Councilors"
COUNCILORS_SOURCE_URL = (
    "https://dominations.fandom.com/wiki/Councilors#Councilors_List"
)
RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")
DEFAULT_OUTPUT = Path(__file__).with_name("stat-label-inconsistencies.html")

ISSUE_ABBREVIATION = "Abbreviation"
ISSUE_CAPITALIZATION = "Capitalization"
ISSUE_MISSPELLING = "Misspelling"
ISSUE_ORDER = {
    ISSUE_ABBREVIATION: 0,
    ISSUE_CAPITALIZATION: 1,
    ISSUE_MISSPELLING: 2,
}


class AuditError(RuntimeError):
    """Raised when a primary source cannot be crawled safely."""


def fetch_wikitext(title: str, *, timeout: float, retries: int) -> str:
    """Fetch one page as wikitext, converting crawler errors for this command."""

    try:
        payload = api_request(
            {"action": "parse", "page": title, "prop": "wikitext"},
            timeout=timeout,
            retries=retries,
        )
    except CrawlError as exc:
        raise AuditError(str(exc)) from exc
    try:
        return payload["parse"]["wikitext"]
    except (KeyError, TypeError) as exc:
        raise AuditError(f"No wikitext returned for {title!r}") from exc


def find_duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates, key=str.casefold)


def discover_artifacts(wikitext: str) -> list[dict[str, str]]:
    """Discover all linked artifacts, including links under nested headings."""

    section_match = re.search(
        r"^==\s*List of Legendary War Artifacts\s*==\s*$"
        r"([\s\S]*?)(?=^==[^=]|\Z)",
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not section_match:
        raise AuditError(
            "Legendary War Artifacts page has no recognizable artifact list"
        )

    artifacts: list[dict[str, str]] = []
    for raw_line in section_match.group(1).splitlines():
        line = raw_line.strip()
        if not line.startswith("*"):
            continue
        link = re.search(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]", line)
        if not link:
            # Parent labels such as "Gemini 8" and "Da Vinci's" are grouping
            # headings, not artifacts themselves.
            continue
        target = link.group(1).strip()
        if not target:
            continue
        artifacts.append(
            {
                "target": target,
                "name": target,
                "sourceUrl": source_url(target),
            }
        )

    if not artifacts:
        raise AuditError("No artifacts were discovered in the artifact list")
    duplicates = find_duplicates(item["target"] for item in artifacts)
    if duplicates:
        raise AuditError(f"Duplicate artifact links discovered: {duplicates}")
    return artifacts


def fetch_page_wikitexts_tolerant(
    titles: list[str], *, timeout: float, retries: int, batch_size: int
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Fetch page revisions in batches, returning page-level warnings."""

    texts: dict[str, str] = {}
    warnings: list[dict[str, str]] = []

    for offset in range(0, len(titles), batch_size):
        title_batch = titles[offset : offset + batch_size]
        try:
            payload = api_request(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "redirects": "1",
                    "titles": "|".join(title_batch),
                },
                timeout=timeout,
                retries=retries,
            )
        except CrawlError as exc:
            # A failed batch is not proof that every page in it is bad. Retry
            # each page separately so good pages remain covered by the report.
            for title in title_batch:
                try:
                    texts[title] = fetch_wikitext(
                        title, timeout=timeout, retries=retries
                    )
                except AuditError as page_exc:
                    warnings.append(
                        {
                            "item": title,
                            "url": source_url(title),
                            "message": f"Page could not be read: {page_exc}",
                        }
                    )
            continue

        query = payload.get("query", {})
        aliases: dict[str, str] = {title: title for title in title_batch}
        for entry in query.get("normalized", []):
            before, after = entry.get("from"), entry.get("to")
            if before and after:
                for requested, current in tuple(aliases.items()):
                    if current == before:
                        aliases[requested] = after
        for entry in query.get("redirects", []):
            before, after = entry.get("from"), entry.get("to")
            if before and after:
                for requested, current in tuple(aliases.items()):
                    if current == before:
                        aliases[requested] = after

        pages = {page.get("title"): page for page in query.get("pages", [])}
        for requested, resolved in aliases.items():
            page = pages.get(resolved)
            if not page or page.get("missing"):
                warnings.append(
                    {
                        "item": requested,
                        "url": source_url(requested),
                        "message": "Wiki page is missing.",
                    }
                )
                continue
            try:
                texts[requested] = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                warnings.append(
                    {
                        "item": requested,
                        "url": source_url(requested),
                        "message": "Wiki page returned no readable revision content.",
                    }
                )

    return texts, warnings


def split_table_cells(line: str) -> list[str]:
    marker = line[0]
    separator = "!!" if marker == "!" else "||"
    return [
        clean_wikitext(remove_cell_attributes(cell.strip()))
        for cell in line[1:].split(separator)
    ]


def parse_wikitable(table: str) -> list[list[str]]:
    """Parse the uncomplicated row/cell syntax used by the source tables."""

    rows: list[list[str]] = []
    current: list[str] = []

    def finish_row() -> None:
        nonlocal current
        if current:
            rows.append(current)
            current = []

    for raw_line in table.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{|" ):
            continue
        if line.startswith("|-"):
            finish_row()
            continue
        if line.startswith("|}"):
            finish_row()
            break
        if line[0] in "!|":
            current.extend(split_table_cells(line))
        elif current:
            # A wrapped cell is uncommon but legal wikitext.
            current[-1] = clean_wikitext(f"{current[-1]} {line}")
    finish_row()
    return rows


def table_after_heading(
    wikitext: str, heading_pattern: str, *, page_name: str, table_name: str
) -> list[list[str]]:
    heading = re.search(
        heading_pattern,
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not heading:
        raise ValueError(f"no recognizable {table_name} heading")

    remainder = wikitext[heading.end() :]
    table_start = remainder.find("{|")
    next_heading = re.search(r"^={2,3}[^=].*?={2,3}\s*$", remainder, re.MULTILINE)
    if table_start < 0 or (next_heading and next_heading.start() < table_start):
        raise ValueError(f"no table follows the {table_name} heading")
    table_end = remainder.find("|}", table_start)
    if table_end < 0:
        raise ValueError(f"{table_name} table has no closing marker")

    rows = parse_wikitable(remainder[table_start : table_end + 2])
    if not rows:
        raise ValueError(f"{table_name} table is empty")
    return rows


def extract_artifact_labels(wikitext: str, page_name: str) -> list[tuple[str, str]]:
    rows = table_after_heading(
        wikitext,
        r"^==+\s*Statistics?\s*==+\s*$",
        page_name=page_name,
        table_name="Statistic",
    )
    header = [cell.casefold() for cell in rows[0]]
    if not header or "benefit name" not in header[0]:
        raise ValueError(f"unexpected Statistic table header: {rows[0]!r}")

    labels: list[tuple[str, str]] = []
    blank_lines: list[int] = []
    for line_number, row in enumerate(rows[1:], start=1):
        if not row or not row[0]:
            blank_lines.append(line_number)
            continue
        # A value may be omitted when it is supplied by a rowspan in an earlier
        # row. The benefit label is still complete and is all this audit needs.
        labels.append((row[0], f"Bonus line {line_number}"))
    if not labels:
        raise ValueError("Statistic table contains no usable bonus data")
    if blank_lines:
        lines = ", ".join(str(line) for line in blank_lines)
        raise ValueError(f"Statistic table has blank benefit row(s): {lines}")
    return labels


def extract_councilor_labels(wikitext: str, page_name: str) -> list[tuple[str, str]]:
    rows = table_after_heading(
        wikitext,
        r"^===\s*War Chamber Seats\s*===\s*$",
        page_name=page_name,
        table_name="War Chamber Seats",
    )
    expected = ["Benefits", *RARITIES]
    if [cell.casefold() for cell in rows[0]] != [
        cell.casefold() for cell in expected
    ]:
        raise ValueError(
            f"unexpected War Chamber table header: {rows[0]!r}; expected {expected!r}"
        )

    labels: list[tuple[str, str]] = []
    for row_number, row in enumerate(rows[1:], start=1):
        if len(row) != len(expected) or not row[0]:
            raise ValueError(f"malformed War Chamber row {row_number}: {row!r}")
        active_rarities = [
            rarity for rarity, value in zip(RARITIES, row[1:]) if value.strip()
        ]
        if not active_rarities:
            raise ValueError(f"War Chamber row {row_number} has no rarity values")
        labels.append((row[0], ", ".join(active_rarities)))
    if not labels:
        raise ValueError("War Chamber table contains no benefit rows")
    return labels


def damerau_levenshtein(left: str, right: str) -> int:
    """Return the optimal-string-alignment edit distance for two short words."""

    rows = len(left) + 1
    columns = len(right) + 1
    matrix = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        matrix[row][0] = row
    for column in range(columns):
        matrix[0][column] = column

    for row in range(1, rows):
        for column in range(1, columns):
            substitution = 0 if left[row - 1] == right[column - 1] else 1
            matrix[row][column] = min(
                matrix[row - 1][column] + 1,
                matrix[row][column - 1] + 1,
                matrix[row - 1][column - 1] + substitution,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                matrix[row][column] = min(
                    matrix[row][column], matrix[row - 2][column - 2] + 1
                )
    return matrix[-1][-1]


def likely_misspelling(word: str) -> str | None:
    folded = word.casefold()
    if folded.startswith("hi") and folded != "hitpoints":
        if 7 <= len(folded) <= 11 and damerau_levenshtein(folded, "hitpoints") <= 2:
            return "Hitpoints"
    if folded.startswith("d") and folded != "damage":
        # Restrict Damage candidates to a transposition, omission, or single
        # substitution of the same-length noun. This deliberately avoids words
        # such as "damaged" and "damages".
        if 5 <= len(folded) <= 6 and damerau_levenshtein(folded, "damage") <= 1:
            return "Damage"
    return None


def audit_label(label: str) -> dict[str, Any] | None:
    """Return normalization details, or None when a label is canonical."""

    replacements: list[tuple[int, int, str]] = []
    issue_types: set[str] = set()
    matched_terms: set[str] = set()

    def add(match: re.Match[str], replacement: str, issue_type: str) -> None:
        replacements.append((match.start(), match.end(), replacement))
        issue_types.add(issue_type)
        matched_terms.add(match.group(0))

    occupied: list[tuple[int, int]] = []
    noncanonical_forms = (
        (r"\bHP\b", "Hitpoints", ISSUE_ABBREVIATION),
        (r"\bDMG\b", "Damage", ISSUE_ABBREVIATION),
        (
            r"(?<![A-Za-z0-9_])H\s*\.\s*P\.?(?![A-Za-z0-9_])",
            "Hitpoints",
            ISSUE_ABBREVIATION,
        ),
        (
            r"(?<![A-Za-z0-9_])D\s*\.\s*M\s*\.\s*G\.?(?![A-Za-z0-9_])",
            "Damage",
            ISSUE_ABBREVIATION,
        ),
        (r"\bHit[\s-]+Points\b", "Hitpoints", ISSUE_MISSPELLING),
    )
    for pattern, replacement, issue_type in noncanonical_forms:
        for match in re.finditer(pattern, label, flags=re.IGNORECASE):
            add(match, replacement, issue_type)
            occupied.append((match.start(), match.end()))

    for pattern, canonical in (
        (r"\bHitpoints\b", "Hitpoints"),
        (r"\bDamage\b", "Damage"),
    ):
        for match in re.finditer(pattern, label, flags=re.IGNORECASE):
            if match.group(0) != canonical:
                add(match, canonical, ISSUE_CAPITALIZATION)
                occupied.append((match.start(), match.end()))

    for match in re.finditer(r"\b[A-Za-z]+\b", label):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        replacement = likely_misspelling(match.group(0))
        if replacement:
            add(match, replacement, ISSUE_MISSPELLING)

    if not replacements:
        return None

    proposed = label
    for start, end, replacement in sorted(replacements, reverse=True):
        proposed = proposed[:start] + replacement + proposed[end:]
    return {
        "proposedLabel": proposed,
        "issueTypes": sorted(issue_types, key=ISSUE_ORDER.__getitem__),
        "matchedTerms": sorted(
            matched_terms, key=lambda value: (value.casefold(), value)
        ),
    }


def audit_items(
    source_type: str,
    items: list[dict[str, str]],
    page_texts: dict[str, str],
    extractor: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """Extract and audit labels, warning on pages with unexpected structures."""

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    analyzed = 0

    for item in items:
        wikitext = page_texts.get(item["target"])
        if wikitext is None:
            continue
        try:
            labels = extractor(wikitext, item["name"])
        except ValueError as exc:
            warnings.append(
                {
                    "item": item["name"],
                    "url": item["sourceUrl"],
                    "message": str(exc),
                }
            )
            continue
        analyzed += 1

        for label, context in labels:
            result = audit_label(label)
            if not result:
                continue
            key = (item["name"], label, result["proposedLabel"])
            finding = grouped.setdefault(
                key,
                {
                    "source": source_type,
                    "item": item["name"],
                    "originalLabel": label,
                    "proposedLabel": result["proposedLabel"],
                    "issueTypes": set(),
                    "matchedTerms": set(),
                    "contexts": set(),
                    "url": item["sourceUrl"],
                },
            )
            finding["issueTypes"].update(result["issueTypes"])
            finding["matchedTerms"].update(result["matchedTerms"])
            finding["contexts"].add(context)

    findings: list[dict[str, Any]] = []
    for finding in grouped.values():
        finding["issueTypes"] = sorted(
            finding["issueTypes"], key=ISSUE_ORDER.__getitem__
        )
        finding["matchedTerms"] = sorted(
            finding["matchedTerms"], key=lambda value: (value.casefold(), value)
        )
        if source_type == "Artifact":
            finding["contexts"] = sorted(
                finding["contexts"],
                key=lambda value: (
                    int(re.search(r"\d+", value).group()), value.casefold()
                ),
            )
        else:
            rarity_index = {rarity: index for index, rarity in enumerate(RARITIES)}
            active_rarities = {
                part.strip()
                for value in finding["contexts"]
                for part in value.split(",")
                if part.strip()
            }
            finding["contexts"] = [
                ", ".join(
                    sorted(
                        active_rarities,
                        key=lambda value: (
                            rarity_index.get(value, len(RARITIES)), value.casefold()
                        ),
                    )
                )
            ]
        findings.append(finding)
    return findings, warnings, analyzed


def finding_sort_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if finding["source"] == "Artifact" else 1,
        finding["item"].casefold(),
        finding["originalLabel"].casefold(),
        finding["proposedLabel"].casefold(),
    )


def warning_sort_key(warning: dict[str, str]) -> tuple[str, str, str]:
    return (
        warning.get("source", "").casefold(),
        warning["item"].casefold(),
        warning["message"].casefold(),
    )


def require_minimum_coverage(
    source: str, *, analyzed: int, discovered: int
) -> None:
    """Reject a crawl that is too incomplete to replace a useful report."""

    if discovered <= 0 or analyzed * 2 < discovered:
        raise AuditError(
            f"{source} detail-page coverage is suspiciously low: "
            f"{analyzed}/{discovered}; refusing to overwrite the existing report"
        )


def build_report_data(
    *, timeout: float, retries: int, batch_size: int
) -> dict[str, Any]:
    """Crawl both primary lists and all linked pages before generating output."""

    try:
        artifact_main = fetch_wikitext(
            ARTIFACTS_PAGE, timeout=timeout, retries=retries
        )
        councilor_main = fetch_wikitext(
            COUNCILORS_PAGE, timeout=timeout, retries=retries
        )
        artifacts = discover_artifacts(artifact_main)
        discovered_councilors = discover_councilors(councilor_main)
    except (AuditError, CrawlError) as exc:
        raise AuditError(f"Primary list crawl failed: {exc}") from exc

    councilors = [
        {
            **item,
            "sourceUrl": source_url(item["target"]),
        }
        for item in discovered_councilors
    ]

    artifact_texts, artifact_fetch_warnings = fetch_page_wikitexts_tolerant(
        [item["target"] for item in artifacts],
        timeout=timeout,
        retries=retries,
        batch_size=batch_size,
    )
    councilor_texts, councilor_fetch_warnings = fetch_page_wikitexts_tolerant(
        [item["target"] for item in councilors],
        timeout=timeout,
        retries=retries,
        batch_size=batch_size,
    )

    artifact_findings, artifact_parse_warnings, artifacts_analyzed = audit_items(
        "Artifact", artifacts, artifact_texts, extract_artifact_labels
    )
    councilor_findings, councilor_parse_warnings, councilors_analyzed = audit_items(
        "Councilor", councilors, councilor_texts, extract_councilor_labels
    )

    # Individual missing or malformed pages remain visible as warnings, but a
    # widespread detail-page failure should not replace a previously useful
    # report with a nearly empty one.
    require_minimum_coverage(
        "Artifact", analyzed=artifacts_analyzed, discovered=len(artifacts)
    )
    require_minimum_coverage(
        "Councilor", analyzed=councilors_analyzed, discovered=len(councilors)
    )

    warnings: list[dict[str, str]] = []
    for source, source_warnings in (
        ("Artifact", artifact_fetch_warnings + artifact_parse_warnings),
        ("Councilor", councilor_fetch_warnings + councilor_parse_warnings),
    ):
        for warning in source_warnings:
            warnings.append({"source": source, **warning})

    findings = sorted(artifact_findings + councilor_findings, key=finding_sort_key)
    warnings.sort(key=warning_sort_key)
    affected_items = {(item["source"], item["item"]) for item in findings}
    category_counts = Counter(
        issue_type
        for finding in findings
        for issue_type in finding["issueTypes"]
    )

    return {
        "generatedAt": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "sources": {
            "artifacts": ARTIFACTS_SOURCE_URL,
            "councilors": COUNCILORS_SOURCE_URL,
        },
        "summary": {
            "artifactsDiscovered": len(artifacts),
            "artifactsAnalyzed": artifacts_analyzed,
            "councilorsDiscovered": len(councilors),
            "councilorsAnalyzed": councilors_analyzed,
            "findings": len(findings),
            "affectedItems": len(affected_items),
            "warnings": len(warnings),
            "categoryCounts": {
                issue_type: category_counts.get(issue_type, 0)
                for issue_type in ISSUE_ORDER
            },
        },
        "findings": findings,
        "warnings": warnings,
    }


def json_for_html(data: Any) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_html(data: dict[str, Any]) -> str:
    generated = html.escape(data["generatedAt"])
    report_json = json_for_html(data)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DomiNations Stat Label Inconsistency Report</title>
  <style>
    :root {{ color-scheme: light; --ink:#17222d; --muted:#5b6875; --line:#d5dde5; --panel:#fff; --navy:#183a59; --blue:#2374a8; --cream:#f4f1e8; --warn:#8a3e17; --warn-bg:#fff4e8; --good:#1f6b45; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--cream); color:var(--ink); font:16px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    a {{ color:var(--blue); }}
    header {{ background:var(--navy); color:#fff; padding:2rem max(1rem,calc((100vw - 1280px)/2)); }}
    header h1 {{ margin:0 0 .4rem; font-size:clamp(1.55rem,4vw,2.4rem); }}
    header p {{ margin:.25rem 0; max-width:75ch; color:#e2edf5; }}
    header a {{ color:#fff; }}
    main {{ width:min(1280px,calc(100% - 2rem)); margin:1.25rem auto 3rem; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; box-shadow:0 2px 8px #17222d12; padding:1rem; margin-bottom:1rem; }}
    .summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); gap:.75rem; }}
    .metric {{ border-left:4px solid var(--blue); padding:.45rem .7rem; background:#f7fafc; }}
    .metric strong {{ display:block; font-size:1.5rem; }}
    .metric span {{ color:var(--muted); font-size:.88rem; }}
    .controls {{ display:grid; grid-template-columns:minmax(220px,2fr) repeat(2,minmax(150px,1fr)); gap:.75rem; align-items:end; }}
    label {{ display:grid; gap:.3rem; color:var(--muted); font-size:.86rem; font-weight:650; }}
    input,select {{ width:100%; border:1px solid #aebbc7; border-radius:6px; background:#fff; color:var(--ink); font:inherit; padding:.62rem .7rem; }}
    input:focus,select:focus {{ outline:3px solid #2374a833; border-color:var(--blue); }}
    .result-count {{ color:var(--muted); margin:.8rem 0 0; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; min-width:940px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:.72rem .65rem; text-align:left; vertical-align:top; }}
    th {{ background:#edf3f7; color:#334554; font-size:.82rem; letter-spacing:.02em; text-transform:uppercase; }}
    tbody tr:hover {{ background:#f8fbfd; }}
    .source,.issue {{ display:inline-block; border-radius:999px; padding:.15rem .5rem; font-size:.78rem; font-weight:700; white-space:nowrap; }}
    .source {{ background:#e6f0f7; color:#174f75; }}
    .issue {{ background:#f0e8f6; color:#5d3778; margin:0 .2rem .2rem 0; }}
    .proposed {{ color:var(--good); font-weight:700; }}
    .empty {{ padding:2rem; text-align:center; color:var(--muted); }}
    .warnings {{ border-color:#e1b48f; background:var(--warn-bg); }}
    .warnings h2 {{ color:var(--warn); margin-top:0; }}
    .warnings ul {{ margin-bottom:0; }}
    .warnings li + li {{ margin-top:.45rem; }}
    .small {{ color:var(--muted); font-size:.88rem; }}
    @media (max-width:760px) {{
      .controls {{ grid-template-columns:1fr; }}
      main {{ width:min(100% - 1rem,1280px); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>DomiNations Stat Label Inconsistency Report</h1>
    <p>Live audit of Legendary War Artifact Statistic tables and Councilor War Chamber bonuses. Canonical labels use the full words <strong>Hitpoints</strong> and <strong>Damage</strong>.</p>
    <p class="small">Generated <time datetime="{generated}">{generated}</time> · Sources: <a href="{html.escape(ARTIFACTS_SOURCE_URL)}">Artifacts</a> and <a href="{html.escape(COUNCILORS_SOURCE_URL)}">Councilors</a></p>
  </header>
  <main>
    <section class="panel summary" id="summary" aria-label="Crawl summary"></section>
    <section class="panel">
      <div class="controls">
        <label>Search
          <input id="search" type="search" placeholder="Item, label, issue, or context" autocomplete="off">
        </label>
        <label>Source
          <select id="source-filter"><option value="">All sources</option><option>Artifact</option><option>Councilor</option></select>
        </label>
        <label>Issue category
          <select id="issue-filter"><option value="">All categories</option><option>Abbreviation</option><option>Capitalization</option><option>Misspelling</option></select>
        </label>
      </div>
      <p class="result-count" id="result-count" aria-live="polite"></p>
    </section>
    <section class="panel" aria-label="Inconsistent labels">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Source</th><th>Item</th><th>Original label</th><th>Issue</th><th>Proposed label</th><th>Context</th><th>Wiki</th></tr></thead>
          <tbody id="findings"></tbody>
        </table>
      </div>
      <p class="empty" id="empty" hidden>No findings match the current filters.</p>
    </section>
    <section class="panel warnings" id="warning-panel" hidden>
      <h2>Crawl warnings</h2>
      <p>These pages were not included in the findings because they were missing or their table structure could not be recognized.</p>
      <ul id="warnings"></ul>
    </section>
  </main>
  <script id="report-data" type="application/json">{report_json}</script>
  <script>
    "use strict";
    const report = JSON.parse(document.getElementById("report-data").textContent);
    const summary = report.summary;
    const metricData = [
      [summary.findings, "Inconsistent labels"],
      [summary.affectedItems, "Affected items"],
      [`${{summary.artifactsAnalyzed}} / ${{summary.artifactsDiscovered}}`, "Artifact pages analyzed"],
      [`${{summary.councilorsAnalyzed}} / ${{summary.councilorsDiscovered}}`, "Councilor pages analyzed"],
      [summary.warnings, "Crawl warnings"]
    ];
    const summaryElement = document.getElementById("summary");
    for (const [value, label] of metricData) {{
      const metric = document.createElement("div");
      metric.className = "metric";
      const strong = document.createElement("strong");
      strong.textContent = value;
      const span = document.createElement("span");
      span.textContent = label;
      metric.append(strong, span);
      summaryElement.append(metric);
    }}

    const search = document.getElementById("search");
    const sourceFilter = document.getElementById("source-filter");
    const issueFilter = document.getElementById("issue-filter");
    const tbody = document.getElementById("findings");
    const count = document.getElementById("result-count");
    const empty = document.getElementById("empty");

    function cell(text, className="") {{
      const td = document.createElement("td");
      td.textContent = text;
      if (className) td.className = className;
      return td;
    }}

    function render() {{
      const query = search.value.trim().toLowerCase();
      const source = sourceFilter.value;
      const issue = issueFilter.value;
      const matches = report.findings.filter(finding => {{
        if (source && finding.source !== source) return false;
        if (issue && !finding.issueTypes.includes(issue)) return false;
        if (!query) return true;
        return [finding.source, finding.item, finding.originalLabel,
          finding.proposedLabel, finding.issueTypes.join(" "),
          finding.matchedTerms.join(" "), finding.contexts.join(" ")]
          .join(" ").toLowerCase().includes(query);
      }});

      tbody.replaceChildren();
      for (const finding of matches) {{
        const row = document.createElement("tr");
        const sourceCell = cell("");
        const sourceBadge = document.createElement("span");
        sourceBadge.className = "source";
        sourceBadge.textContent = finding.source;
        sourceCell.append(sourceBadge);

        const issueCell = cell("");
        for (const issueType of finding.issueTypes) {{
          const badge = document.createElement("span");
          badge.className = "issue";
          badge.textContent = issueType;
          issueCell.append(badge);
        }}
        const linkCell = cell("");
        const link = document.createElement("a");
        link.href = finding.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open page";
        linkCell.append(link);
        row.append(sourceCell, cell(finding.item), cell(finding.originalLabel),
          issueCell, cell(finding.proposedLabel, "proposed"),
          cell(finding.contexts.join("; ")), linkCell);
        tbody.append(row);
      }}
      count.textContent = `Showing ${{matches.length}} of ${{report.findings.length}} findings`;
      empty.hidden = matches.length !== 0;
    }}

    for (const control of [search, sourceFilter, issueFilter]) {{
      control.addEventListener(control === search ? "input" : "change", render);
    }}

    if (report.warnings.length) {{
      const panel = document.getElementById("warning-panel");
      const list = document.getElementById("warnings");
      for (const warning of report.warnings) {{
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = warning.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = `${{warning.source}}: ${{warning.item}}`;
        item.append(link, ` — ${{warning.message}}`);
        list.append(item);
      }}
      panel.hidden = false;
    }}
    render();
  </script>
</body>
</html>
"""


def atomic_write(path: Path, content: str) -> None:
    """Write the complete report atomically after all crawl work succeeds."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"HTML destination (default: {DEFAULT_OUTPUT.name})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="HTTP request timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries for transient HTTP failures (default: 3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Individual pages per MediaWiki request (default: 20)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.timeout <= 0 or args.retries < 0 or not 1 <= args.batch_size <= 50:
        print(
            "error: timeout must be positive, retries non-negative, and batch-size 1-50",
            file=sys.stderr,
        )
        return 2
    try:
        data = build_report_data(
            timeout=args.timeout,
            retries=args.retries,
            batch_size=args.batch_size,
        )
        atomic_write(args.output, render_html(data))
    except (AuditError, CrawlError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = data["summary"]
    print(
        f"Wrote {summary['findings']} findings across "
        f"{summary['affectedItems']} items to {args.output}"
    )
    print(
        f"Analyzed {summary['artifactsAnalyzed']}/"
        f"{summary['artifactsDiscovered']} artifacts and "
        f"{summary['councilorsAnalyzed']}/"
        f"{summary['councilorsDiscovered']} councilors; "
        f"{summary['warnings']} warning(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
