#!/usr/bin/env python3
"""Build councilors.json from the DomiNations Councilors wiki pages.

The crawler uses only Python's standard library and the public MediaWiki API.
It discovers the current Councilor list and types from the Councilors page,
reads each linked page's Primary and War Chamber Seats tables, writes the
reusable JSON, and synchronizes the standalone page's embedded copy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


API_URL = "https://dominations.fandom.com/api.php"
SOURCE_URL = "https://dominations.fandom.com/wiki/Councilors#Councilors_List"
RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")
USER_AGENT = (
    "dominationsTools-councilor-crawler/1.0 "
    "(https://github.com/grege117/dominationsTools)"
)
DEFAULT_OUTPUT = Path(__file__).with_name("councilors.json")
DEFAULT_HTML = Path(__file__).with_name("councilor-helper.html")
EMBED_START = '  <script id="councilor-data" type="application/json">\n'
EMBED_END = "\n  </script>"


class CrawlError(RuntimeError):
    """Raised when wiki data cannot be safely converted into the catalog."""


def api_request(
    params: dict[str, str], *, timeout: float, retries: int
) -> dict[str, Any]:
    """Send a MediaWiki API request, retrying transient failures."""

    request_params = {
        "format": "json",
        "formatversion": "2",
        **params,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(request_params)}"
    last_error: BaseException | None = None

    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
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
        {
            "action": "parse",
            "page": title,
            "prop": "wikitext",
        },
        timeout=timeout,
        retries=retries,
    )
    try:
        return payload["parse"]["wikitext"]
    except (KeyError, TypeError) as exc:
        raise CrawlError(f"No wikitext returned for {title!r}") from exc


def discover_councilors(wikitext: str) -> list[dict[str, str]]:
    """Extract link target, display name, and type from Councilors List."""

    section_match = re.search(
        r"^==\s*Councilors List\s*==\s*$([\s\S]*?)(?=^==[^=]|\Z)",
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not section_match:
        raise CrawlError("Councilors page has no recognizable Councilors List section")

    type_name: str | None = None
    councilors: list[dict[str, str]] = []
    for raw_line in section_match.group(1).splitlines():
        line = raw_line.strip()
        type_match = re.fullmatch(r"={3,}\s*(.*?)\s*={3,}", line)
        if type_match:
            type_name = clean_wikitext(type_match.group(1))
            continue

        link_match = re.match(r"^\*\s*\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]", line)
        if not link_match:
            continue
        if not type_name:
            raise CrawlError(f"Councilor link appears before a type heading: {line}")

        target = link_match.group(1).strip()
        display = clean_wikitext(link_match.group(2) or target)
        if not target or not display:
            raise CrawlError(f"Invalid Councilor link: {line}")
        councilors.append({"target": target, "name": display, "type": type_name})

    if not councilors:
        raise CrawlError("No Councilors discovered in Councilors List")

    duplicate_names = find_duplicates(item["name"] for item in councilors)
    duplicate_targets = find_duplicates(item["target"] for item in councilors)
    if duplicate_names or duplicate_targets:
        raise CrawlError(
            "Duplicate Councilors discovered: "
            f"names={duplicate_names or 'none'}, targets={duplicate_targets or 'none'}"
        )
    return councilors


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_page_wikitexts(
    titles: list[str], *, timeout: float, retries: int, batch_size: int
) -> dict[str, str]:
    """Fetch Councilor revisions in MediaWiki batches and map requested titles."""

    result: dict[str, str] = {}
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
        aliases: dict[str, str] = {title: title for title in title_batch}
        for entry in query.get("normalized", []):
            source = entry.get("from")
            destination = entry.get("to")
            if source and destination:
                for requested, current in list(aliases.items()):
                    if current == source:
                        aliases[requested] = destination
        for entry in query.get("redirects", []):
            source = entry.get("from")
            destination = entry.get("to")
            if source and destination:
                for requested, current in list(aliases.items()):
                    if current == source:
                        aliases[requested] = destination

        pages = {page.get("title"): page for page in query.get("pages", [])}
        for requested, resolved in aliases.items():
            page = pages.get(resolved)
            if not page or page.get("missing"):
                raise CrawlError(
                    f"Councilor page {requested!r} is missing (resolved as {resolved!r})"
                )
            try:
                result[requested] = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise CrawlError(f"No page content returned for {requested!r}") from exc

    missing = [title for title in titles if title not in result]
    if missing:
        raise CrawlError(f"Page content was not returned for: {', '.join(missing)}")
    return result


def clean_wikitext(value: str) -> str:
    """Convert the small subset of wiki markup used in table cells to text."""

    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)

    # Resolve nested links from the inside out. The final pipe-separated segment
    # is the text MediaWiki displays.
    link_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    while link_pattern.search(value):
        value = link_pattern.sub(lambda match: match.group(1).split("|")[-1], value)

    # Templates are not expected in benefit tables. Keep their final argument if
    # one appears, which is the most common display-text convention.
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


def split_table_cells(line: str) -> list[tuple[str, str]]:
    """Split a wikitext table line into (marker, cell text) tuples."""

    marker = line[0]
    if marker not in "!|":
        return []
    separator = "!!" if marker == "!" else "||"
    cells = line[1:].split(separator)
    return [(marker, cell.strip()) for cell in cells]


def remove_cell_attributes(value: str) -> str:
    """Drop optional `style=... |` prefixes from a wikitext table cell."""

    if "|" not in value:
        return value
    prefix, remainder = value.split("|", 1)
    if re.search(r"(?:^|\s)(?:style|class|rowspan|colspan|align|scope)\s*=", prefix, re.I):
        return remainder.strip()
    return value


def parse_wikitable(table: str, page_name: str) -> list[list[str]]:
    """Parse the simple MediaWiki table syntax used by Councilor benefit tables."""

    rows: list[list[str]] = []
    current: list[str] = []

    def finish_row() -> None:
        nonlocal current
        if current:
            rows.append(current)
            current = []

    for raw_line in table.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{| ") or line == "{|" or line.startswith("{|class"):
            continue
        if line.startswith("|-"):
            finish_row()
            continue
        if line.startswith("|}"):
            finish_row()
            break
        if line[0] not in "!|":
            if current:
                current[-1] = f"{current[-1]} {line}".strip()
            continue
        for _, value in split_table_cells(line):
            current.append(clean_wikitext(remove_cell_attributes(value)))

    finish_row()
    if not rows:
        raise CrawlError(f"{page_name}: War Chamber table is empty")
    return rows


def extract_council_bonuses(
    wikitext: str, page_name: str, chamber_name: str
) -> dict[str, list[dict[str, str]]]:
    """Extract one Council chamber's rarity table from a Councilor page."""

    heading = re.search(
        rf"^===\s*{re.escape(chamber_name)} Seats\s*===\s*$",
        wikitext,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not heading:
        raise CrawlError(f"{page_name}: no recognizable {chamber_name} Seats heading")

    remainder = wikitext[heading.end() :]
    table_start = remainder.find("{|")
    next_heading = re.search(r"^={2,3}[^=].*?={2,3}\s*$", remainder, re.MULTILINE)
    if table_start < 0 or (next_heading and next_heading.start() < table_start):
        raise CrawlError(f"{page_name}: no table follows {chamber_name} Seats heading")
    table_end = remainder.find("|}", table_start)
    if table_end < 0:
        raise CrawlError(f"{page_name}: {chamber_name} table has no closing marker")

    rows = parse_wikitable(remainder[table_start : table_end + 2], page_name)
    header = rows[0]
    expected_header = ["Benefits", *RARITIES]
    if [cell.casefold() for cell in header] != [
        cell.casefold() for cell in expected_header
    ]:
        raise CrawlError(
            f"{page_name}: unexpected {chamber_name} table header {header!r}; "
            f"expected {expected_header!r}"
        )

    bonuses: dict[str, list[dict[str, str]]] = {rarity: [] for rarity in RARITIES}
    for row_number, row in enumerate(rows[1:], start=1):
        if len(row) != len(expected_header):
            raise CrawlError(
                f"{page_name}: {chamber_name} row {row_number} has {len(row)} cells, "
                f"expected {len(expected_header)}: {row!r}"
            )
        stat = row[0]
        if not stat:
            raise CrawlError(f"{page_name}: {chamber_name} row {row_number} has no benefit")

        seen_value = False
        for rarity, value in zip(RARITIES, row[1:]):
            if value:
                seen_value = True
                bonuses[rarity].append({"stat": stat, "value": value})
            elif seen_value:
                raise CrawlError(
                    f"{page_name}: benefit {stat!r} has a blank {rarity} value "
                    "after it has already unlocked"
                )
        if not seen_value:
            raise CrawlError(f"{page_name}: benefit {stat!r} has no rarity values")

    for rarity, rarity_bonuses in bonuses.items():
        if not rarity_bonuses:
            raise CrawlError(
                f"{page_name}: no effective {rarity} {chamber_name} bonuses found"
            )
    return bonuses


def extract_war_bonuses(wikitext: str, page_name: str) -> dict[str, list[dict[str, str]]]:
    return extract_council_bonuses(wikitext, page_name, "War Chamber")


def extract_multiplayer_bonuses(
    wikitext: str, page_name: str
) -> dict[str, list[dict[str, str]]]:
    return extract_council_bonuses(wikitext, page_name, "Primary Chamber")


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def source_url(target: str) -> str:
    title = target.replace(" ", "_")
    return "https://dominations.fandom.com/wiki/" + urllib.parse.quote(
        title, safe="()_-"
    )


def find_duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def validate_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("source") != SOURCE_URL:
        raise CrawlError("Catalog source URL is incorrect")
    if catalog.get("rarities") != list(RARITIES):
        raise CrawlError("Catalog rarities are missing or out of order")
    councilors = catalog.get("councilors")
    if not isinstance(councilors, list) or not councilors:
        raise CrawlError("Catalog has no Councilors")

    ids = [item.get("id") for item in councilors]
    duplicate_ids = find_duplicates(value for value in ids if isinstance(value, str))
    if duplicate_ids:
        raise CrawlError(f"Duplicate Councilor IDs: {duplicate_ids}")

    for item in councilors:
        for field in ("id", "name", "type", "sourceUrl"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise CrawlError(f"Councilor has an invalid {field}: {item!r}")
        for field, label in (
            ("warBonuses", "War"),
            ("multiplayerBonuses", "Multiplayer"),
        ):
            bonuses_by_rarity = item.get(field)
            if (
                not isinstance(bonuses_by_rarity, dict)
                or list(bonuses_by_rarity) != list(RARITIES)
            ):
                raise CrawlError(f"{item['name']}: invalid {label} rarity keys")
            for rarity in RARITIES:
                bonuses = bonuses_by_rarity[rarity]
                if not isinstance(bonuses, list) or not bonuses:
                    raise CrawlError(f"{item['name']}: no {rarity} {label} bonuses")
                for bonus in bonuses:
                    if set(bonus) != {"stat", "value"}:
                        raise CrawlError(
                            f"{item['name']}: malformed {rarity} bonus {bonus!r}"
                        )
                    if not all(
                        isinstance(bonus[key], str) and bonus[key].strip()
                        for key in ("stat", "value")
                    ):
                        raise CrawlError(
                            f"{item['name']}: empty {rarity} bonus value {bonus!r}"
                        )


def serialize_catalog(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def embed_catalog(html_text: str, serialized: str, path: Path) -> str:
    """Replace the page's one embedded catalog without touching other markup."""

    if html_text.count(EMBED_START) != 1:
        raise CrawlError(
            f"{path}: expected exactly one Councilor data script opening marker"
        )
    start = html_text.index(EMBED_START) + len(EMBED_START)
    end = html_text.find(EMBED_END, start)
    if end < 0:
        raise CrawlError(f"{path}: Councilor data script has no closing marker")

    # This remains equivalent JSON but prevents an unexpected source string from
    # terminating the HTML script element.
    embedded = serialized.rstrip("\n").replace("</", "<\\/")
    return html_text[:start] + embedded + html_text[end:]


def build_catalog(*, timeout: float, retries: int, batch_size: int) -> dict[str, Any]:
    main_wikitext = fetch_wikitext("Councilors", timeout=timeout, retries=retries)
    discovered = discover_councilors(main_wikitext)
    page_texts = fetch_page_wikitexts(
        [item["target"] for item in discovered],
        timeout=timeout,
        retries=retries,
        batch_size=batch_size,
    )

    councilors: list[dict[str, Any]] = []
    for item in discovered:
        councilors.append(
            {
                "id": slugify(item["name"]),
                "name": item["name"],
                "type": item["type"],
                "sourceUrl": source_url(item["target"]),
                "warBonuses": extract_war_bonuses(
                    page_texts[item["target"]], item["name"]
                ),
                "multiplayerBonuses": extract_multiplayer_bonuses(
                    page_texts[item["target"]], item["name"]
                ),
            }
        )

    catalog = {
        "source": SOURCE_URL,
        "generatedAt": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "rarities": list(RARITIES),
        "councilors": councilors,
    }
    validate_catalog(catalog)
    return catalog


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"JSON destination (default: {DEFAULT_OUTPUT.name})",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=DEFAULT_HTML,
        help=(
            "standalone page whose embedded data is updated "
            f"(default: {DEFAULT_HTML.name})"
        ),
    )
    parser.add_argument(
        "--skip-html",
        action="store_true",
        help="write only the JSON file and do not update embedded page data",
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
        help="Councilor pages per MediaWiki API request (default: 20)",
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
            timeout=args.timeout,
            retries=args.retries,
            batch_size=args.batch_size,
        )
        serialized = serialize_catalog(catalog)
        next_html: str | None = None
        if not args.skip_html:
            next_html = embed_catalog(
                args.html.read_text(encoding="utf-8"), serialized, args.html
            )

        args.output.write_text(serialized, encoding="utf-8")
        if next_html is not None:
            args.html.write_text(next_html, encoding="utf-8")
    except (CrawlError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    type_count = len({item["type"] for item in catalog["councilors"]})
    print(
        f"Wrote {len(catalog['councilors'])} Councilors across "
        f"{type_count} types to {args.output}"
    )
    if not args.skip_html:
        print(f"Updated embedded Councilor data in {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
