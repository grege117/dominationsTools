#!/usr/bin/env python3
"""Build the Multiplayer Legendary Artifact catalog from the DomiNations wiki.

The Multiplayer list is the wiki's ``Legendary Artifacts`` page. This crawler
uses the shared Statistic-table parser from crawl_artifacts.py, writes a
reusable JSON catalog, and synchronizes the page's embedded fallback catalog.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from crawl_artifacts import (
    CrawlError,
    atomic_write,
    discover_artifacts as _discover_war_artifacts,
    embed_catalog as _embed_catalog,
    extract_bonuses,
    fetch_page_wikitexts,
    fetch_wikitext,
    find_duplicates,
    serialize_catalog,
    validate_catalog as _validate_catalog,
)


SOURCE_URL = "https://dominations.fandom.com/wiki/Legendary_Artifacts"
DEFAULT_OUTPUT = Path(__file__).with_name("legendary-multiplayer-artifacts.json")
DEFAULT_HTML = Path(__file__).with_name("legendary-artifact-finder.html")
EMBED_START = '  <script id="multiplayer-artifact-data" type="application/json">\n'
EMBED_END = "\n  </script>"


def discover_artifacts(wikitext: str) -> list[dict[str, str]]:
    """Discover the linked artifacts in the Multiplayer artifact list."""

    return _discover_war_artifacts(
        wikitext, list_heading="List of Legendary Artifacts"
    )


def validate_catalog(catalog: dict[str, Any]) -> None:
    _validate_catalog(catalog, expected_source=SOURCE_URL)


def embed_catalog(html_text: str, serialized: str, path: Path) -> str:
    return _embed_catalog(
        html_text,
        serialized,
        path,
        embed_start=EMBED_START,
        embed_end=EMBED_END,
        catalog_label="Multiplayer artifact",
    )


def build_catalog(*, timeout: float, retries: int, batch_size: int) -> dict[str, Any]:
    main_wikitext = fetch_wikitext(
        "Legendary Artifacts", timeout=timeout, retries=retries
    )
    discovered = discover_artifacts(main_wikitext)
    duplicate_names = find_duplicates(item["name"] for item in discovered)
    if duplicate_names:
        raise CrawlError(f"Duplicate Multiplayer artifact names: {duplicate_names}")
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
        artifacts.append({"name": item["name"], "status": status, "bonuses": bonuses})

    if available_count * 2 < len(discovered):
        raise CrawlError(
            f"Multiplayer artifact coverage is suspiciously low: {available_count}/"
            f"{len(discovered)}; refusing to overwrite the catalog"
        )
    catalog = {"source": SOURCE_URL, "artifacts": artifacts}
    validate_catalog(catalog)
    return catalog


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--skip-html", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=20)
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
            next_html = embed_catalog(args.html.read_text(encoding="utf-8"), serialized, args.html)
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
        f"Wrote {len(catalog['artifacts'])} Multiplayer Legendary Artifacts to "
        f"{args.output} ({len(unavailable)} unavailable)"
    )
    if unavailable:
        print("No bonus data: " + ", ".join(unavailable))
    if not args.skip_html:
        print(f"Updated embedded Multiplayer artifact data in {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
