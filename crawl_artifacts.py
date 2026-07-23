#!/usr/bin/env python3
"""Build Legendary War Artifact JSON from the DomiNations wiki.

The crawler uses only Python's standard library and the public MediaWiki API.
It discovers the current artifact list, reads each linked page's Statistic
table, writes legendary-war-artifacts.json, and synchronizes the standalone
helper page's embedded copy.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


API_URL = "https://dominations.fandom.com/api.php"
SOURCE_URL = (
    "https://dominations.fandom.com/wiki/"
    "Legendary_War_Artifacts#List_of_Legendary_War_Artifacts"
)
USER_AGENT = (
    "dominationsTools-artifact-crawler/1.0 "
    "(https://github.com/grege117/dominationsTools)"
)
DEFAULT_OUTPUT = Path(__file__).with_name("legendary-war-artifacts.json")
DEFAULT_HTML = Path(__file__).with_name("legendary-artifact-finder.html")
EMBED_START = '  <script id="artifact-data" type="application/json">\n'
EMBED_END = "\n  </script>"
EXPECTED_BONUS_COUNT = 5


class CrawlError(RuntimeError):
    """Raised when wiki data cannot be converted safely into the catalog."""


def api_request(
    params: dict[str, str], *, timeout: float, retries: int
) -> dict[str, Any]:
    """Send a MediaWiki API request, retrying request and API failures."""

    request_params = {"format": "json", "formatversion": "2", **params}
    url = f"{API_URL}?{urllib.parse.urlencode(request_params)}"
    last_error: BaseException | None = None

    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if "error" in payload:
                error = payload["error"]
                raise CrawlError(
                    f"MediaWiki API error {error.get('code', 'unknown')}: "
                    f"{error.get('info', error)}"
                )
            return payload
        except (
            CrawlError,
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2**attempt, 8))

    raise CrawlError(f"MediaWiki API request failed: {last_error}")


def fetch_wikitext(title: str, *, timeout: float, retries: int) -> str:
    payload = api_request(
        {"action": "parse", "page": title, "prop": "wikitext"},
        timeout=timeout,
        retries=retries,
    )
    try:
        return payload["parse"]["wikitext"]
    except (KeyError, TypeError) as exc:
        raise CrawlError(f"No wikitext returned for {title!r}") from exc


def clean_wikitext(value: str) -> str:
    """Convert the small subset of wiki markup used by table cells to text."""

    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    link_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    while link_pattern.search(value):
        value = link_pattern.sub(lambda match: match.group(1).split("|")[-1], value)
    value = re.sub(
        r"\{\{([^{}]+)\}\}",
        lambda match: match.group(1).split("|")[-1],
        value,
    )
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("'''", "").replace("''", "")
    value = html.unescape(value)
    return " ".join(value.replace("\xa0", " ").split())


def find_duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        folded = value.casefold()
        if folded in seen:
            duplicates.add(value)
        seen.add(folded)
    return sorted(duplicates, key=str.casefold)


def discover_artifacts(wikitext: str) -> list[dict[str, str]]:
    """Discover linked leaf artifacts, including nested group children."""

    section = re.search(
        r"^==\s*List of Legendary War Artifacts\s*==\s*$"
        r"([\s\S]*?)(?=^==[^=]|\Z)",
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not section:
        raise CrawlError(
            "Legendary War Artifacts page has no recognizable artifact list"
        )

    artifacts: list[dict[str, str]] = []
    for raw_line in section.group(1).splitlines():
        line = raw_line.strip()
        if not line.startswith("*"):
            continue
        link = re.search(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]", line)
        if not link:
            continue
        target = link.group(1).strip()
        if not target:
            raise CrawlError(f"Invalid artifact link: {line}")
        # Link display text is abbreviated under groups such as Gemini 8 and
        # Da Vinci's, so the full page target is the catalog name.
        artifacts.append({"target": target, "name": target})

    if not artifacts:
        raise CrawlError("No artifacts were discovered in the artifact list")
    duplicates = find_duplicates(item["target"] for item in artifacts)
    if duplicates:
        raise CrawlError(f"Duplicate artifact links discovered: {duplicates}")
    return artifacts


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_page_wikitexts(
    titles: list[str], *, timeout: float, retries: int, batch_size: int
) -> dict[str, str | None]:
    """Fetch page revisions, using None only for confirmed missing pages."""

    result: dict[str, str | None] = {}
    for title_batch in chunks(titles, batch_size):
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
        query = payload.get("query", {})
        aliases = {title: title for title in title_batch}
        for mapping_name in ("normalized", "redirects"):
            for entry in query.get(mapping_name, []):
                source, destination = entry.get("from"), entry.get("to")
                if source and destination:
                    for requested, current in tuple(aliases.items()):
                        if current == source:
                            aliases[requested] = destination

        pages = {page.get("title"): page for page in query.get("pages", [])}
        for requested, resolved in aliases.items():
            page = pages.get(resolved)
            if not page or page.get("missing"):
                result[requested] = None
                continue
            try:
                result[requested] = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise CrawlError(f"No page content returned for {requested!r}") from exc

    absent = [title for title in titles if title not in result]
    if absent:
        raise CrawlError(f"Page results were omitted for: {', '.join(absent)}")
    return result


@dataclass(frozen=True)
class TableCell:
    text: str
    rowspan: int = 1


def parse_cell(raw_value: str, page_name: str) -> TableCell:
    value = raw_value.strip()
    rowspan = 1
    if "|" in value:
        prefix, remainder = value.split("|", 1)
        if re.search(
            r"(?:^|\s)(?:style|class|rowspan|colspan|align|scope)\s*=",
            prefix,
            flags=re.IGNORECASE,
        ):
            match = re.search(r"rowspan\s*=\s*[\"']?(\d+)", prefix, re.IGNORECASE)
            if match:
                rowspan = int(match.group(1))
                if rowspan < 1:
                    raise CrawlError(f"{page_name}: invalid rowspan {rowspan}")
            value = remainder.strip()
        elif re.search(r"\s\|\s", value):
            # A few source cells contain MediaWiki-style fallback text without
            # the surrounding link brackets. The final segment is the intended
            # display label (and avoids retaining both spellings).
            value = value.rsplit("|", 1)[-1].strip()
    return TableCell(clean_wikitext(value), rowspan)


def split_table_cells(line: str, page_name: str) -> list[TableCell]:
    separator = "!!" if line[0] == "!" else "||"
    return [parse_cell(value, page_name) for value in line[1:].split(separator)]


def parse_wikitable(table: str, page_name: str) -> list[list[str]]:
    """Parse a two-column table and expand values supplied by rowspans."""

    physical_rows: list[list[TableCell]] = []
    current: list[TableCell] = []

    def finish_row() -> None:
        nonlocal current
        if current:
            physical_rows.append(current)
            current = []

    for raw_line in table.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{|"):
            continue
        if line.startswith("|-"):
            finish_row()
            continue
        if line.startswith("|}"):
            finish_row()
            break
        if line[0] in "!|":
            current.extend(split_table_cells(line, page_name))
        elif current:
            previous = current[-1]
            current[-1] = TableCell(
                clean_wikitext(f"{previous.text} {line}"), previous.rowspan
            )
    finish_row()
    if not physical_rows:
        raise CrawlError(f"{page_name}: Statistic table is empty")

    header = [cell.text for cell in physical_rows[0]]
    if len(header) != 2:
        raise CrawlError(f"{page_name}: malformed Statistic table header {header!r}")

    logical_rows = [header]
    active_spans: dict[int, tuple[int, str]] = {}
    for row_number, cells in enumerate(physical_rows[1:], start=1):
        row: list[str | None] = [None, None]
        next_spans: dict[int, tuple[int, str]] = {}
        for column, (remaining, value) in active_spans.items():
            row[column] = value
            if remaining > 1:
                next_spans[column] = (remaining - 1, value)

        cursor = 0
        for cell in cells:
            while cursor < len(row) and row[cursor] is not None:
                cursor += 1
            if cursor >= len(row):
                raise CrawlError(
                    f"{page_name}: Statistic row {row_number} has too many cells"
                )
            row[cursor] = cell.text
            if cell.rowspan > 1:
                next_spans[cursor] = (cell.rowspan - 1, cell.text)
            cursor += 1
        logical_rows.append([value if value is not None else "" for value in row])
        active_spans = next_spans
    return logical_rows


def extract_bonuses(wikitext: str, page_name: str) -> list[str]:
    """Extract five combined benefit/value strings; blank tables are unavailable."""

    heading = re.search(
        r"^==+\s*Statistics?\s*==+\s*$",
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not heading:
        raise CrawlError(f"{page_name}: no recognizable Statistic heading")
    remainder = wikitext[heading.end() :]
    table_start = remainder.find("{|")
    next_heading = re.search(r"^={2,3}[^=].*?={2,3}\s*$", remainder, re.MULTILINE)
    if table_start < 0 or (next_heading and next_heading.start() < table_start):
        raise CrawlError(f"{page_name}: no table follows the Statistic heading")
    table_end = remainder.find("|}", table_start)
    if table_end < 0:
        raise CrawlError(f"{page_name}: Statistic table has no closing marker")

    rows = parse_wikitable(remainder[table_start : table_end + 2], page_name)
    expected_header = ["Benefit Name", "Base Stat"]
    if [cell.casefold() for cell in rows[0]] != [
        cell.casefold() for cell in expected_header
    ]:
        raise CrawlError(
            f"{page_name}: unexpected Statistic table header {rows[0]!r}; "
            f"expected {expected_header!r}"
        )
    data_rows = rows[1:]
    if len(data_rows) != EXPECTED_BONUS_COUNT:
        raise CrawlError(
            f"{page_name}: Statistic table has {len(data_rows)} bonus rows; "
            f"expected {EXPECTED_BONUS_COUNT}"
        )
    if all(not benefit and not value for benefit, value in data_rows):
        return []
    for row_number, (benefit, value) in enumerate(data_rows, start=1):
        if not benefit or not value:
            raise CrawlError(
                f"{page_name}: Statistic row {row_number} is partially blank"
            )
    return [f"{benefit} {value}" for benefit, value in data_rows]


def validate_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("source") != SOURCE_URL:
        raise CrawlError("Catalog source URL is incorrect")
    artifacts = catalog.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise CrawlError("Catalog has no artifacts")
    duplicate_names = find_duplicates(
        item.get("name", "") for item in artifacts if isinstance(item, dict)
    )
    if duplicate_names:
        raise CrawlError(f"Duplicate artifact names: {duplicate_names}")

    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "name",
            "status",
            "bonuses",
        }:
            raise CrawlError(f"Malformed artifact entry: {artifact!r}")
        name = artifact["name"]
        status = artifact["status"]
        bonuses = artifact["bonuses"]
        if not isinstance(name, str) or not name.strip():
            raise CrawlError(f"Artifact has an invalid name: {artifact!r}")
        if status not in {"available", "unavailable"}:
            raise CrawlError(f"{name}: invalid status {status!r}")
        if not isinstance(bonuses, list) or not all(
            isinstance(bonus, str) and bonus.strip() for bonus in bonuses
        ):
            raise CrawlError(f"{name}: invalid bonuses")
        if status == "available" and len(bonuses) != EXPECTED_BONUS_COUNT:
            raise CrawlError(
                f"{name}: available artifacts require {EXPECTED_BONUS_COUNT} bonuses"
            )
        if status == "unavailable" and bonuses:
            raise CrawlError(f"{name}: unavailable artifact must have no bonuses")


def build_catalog(*, timeout: float, retries: int, batch_size: int) -> dict[str, Any]:
    main_wikitext = fetch_wikitext(
        "Legendary War Artifacts", timeout=timeout, retries=retries
    )
    discovered = discover_artifacts(main_wikitext)
    page_texts = fetch_page_wikitexts(
        [item["target"] for item in discovered],
        timeout=timeout,
        retries=retries,
        batch_size=batch_size,
    )

    artifacts: list[dict[str, Any]] = []
    available_count = 0
    for item in discovered:
        page_text = page_texts[item["target"]]
        bonuses = [] if page_text is None else extract_bonuses(page_text, item["name"])
        status = "available" if bonuses else "unavailable"
        available_count += status == "available"
        artifacts.append(
            {"name": item["name"], "status": status, "bonuses": bonuses}
        )

    if available_count * 2 < len(discovered):
        raise CrawlError(
            f"Artifact coverage is suspiciously low: {available_count}/"
            f"{len(discovered)}; refusing to overwrite the catalog"
        )
    catalog = {"source": SOURCE_URL, "artifacts": artifacts}
    validate_catalog(catalog)
    return catalog


def serialize_catalog(catalog: dict[str, Any]) -> str:
    # Keep one artifact per line, matching the existing catalog's compact and
    # review-friendly format while retaining stable source/bonus ordering.
    lines = [
        "{",
        f'  "source": {json.dumps(catalog["source"], ensure_ascii=False)},',
        '  "artifacts": [',
    ]
    artifacts = catalog["artifacts"]
    for index, artifact in enumerate(artifacts):
        suffix = "," if index + 1 < len(artifacts) else ""
        serialized = json.dumps(
            artifact, ensure_ascii=False, separators=(",", ":")
        )
        lines.append(f"    {serialized}{suffix}")
    lines.extend(["  ]", "}"])
    return "\n".join(lines) + "\n"


def embed_catalog(html_text: str, serialized: str, path: Path) -> str:
    """Replace the helper's one embedded catalog without changing other markup."""

    if html_text.count(EMBED_START) != 1:
        raise CrawlError(
            f"{path}: expected exactly one artifact data script opening marker"
        )
    start = html_text.index(EMBED_START) + len(EMBED_START)
    end = html_text.find(EMBED_END, start)
    if end < 0:
        raise CrawlError(f"{path}: artifact data script has no closing marker")
    embedded = serialized.rstrip("\n").replace("</", "<\\/")
    return html_text[:start] + embedded + html_text[end:]


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="JSON destination"
    )
    parser.add_argument(
        "--html", type=Path, default=DEFAULT_HTML, help="helper HTML to synchronize"
    )
    parser.add_argument(
        "--skip-html", action="store_true", help="write JSON without updating HTML"
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="retries for transient failures"
    )
    parser.add_argument(
        "--batch-size", type=int, default=20, help="artifact pages per API request"
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
        catalog = build_catalog(
            timeout=args.timeout, retries=args.retries, batch_size=args.batch_size
        )
        serialized = serialize_catalog(catalog)
        next_html: str | None = None
        if not args.skip_html:
            current_html = args.html.read_text(encoding="utf-8")
            next_html = embed_catalog(current_html, serialized, args.html)

        # No destination is touched until crawling, parsing, validation, and
        # HTML embedding have all succeeded.
        atomic_write(args.output, serialized)
        if next_html is not None:
            atomic_write(args.html, next_html)
    except (CrawlError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    unavailable = [
        artifact["name"]
        for artifact in catalog["artifacts"]
        if artifact["status"] == "unavailable"
    ]
    print(
        f"Wrote {len(catalog['artifacts'])} Legendary War Artifacts to "
        f"{args.output} ({len(unavailable)} unavailable)"
    )
    if unavailable:
        print("No bonus data: " + ", ".join(unavailable))
    if not args.skip_html:
        print(f"Updated embedded artifact data in {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
