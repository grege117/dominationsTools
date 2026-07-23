#!/usr/bin/env python3
"""Network-free tests for the Multiplayer Legendary Artifact crawler."""

from __future__ import annotations

import unittest
from pathlib import Path

import crawl_multiplayer_artifacts as crawler


def catalog() -> dict[str, object]:
    return {
        "source": crawler.SOURCE_URL,
        "artifacts": [
            {
                "name": "Admiral Yi’s Helmet",
                "status": "available",
                "bonuses": ["One +1%"] * 5,
            }
        ],
    }


class MultiplayerDiscoveryTests(unittest.TestCase):
    def test_dedicated_list_is_discovered(self) -> None:
        wikitext = """== List of Legendary Artifacts ==
* [[Admiral Yi’s Helmet]]
* [[Sutton Hoo Helmet]]
== Quick View ==
* [[Not an artifact]]
"""
        self.assertEqual(
            [item["name"] for item in crawler.discover_artifacts(wikitext)],
            ["Admiral Yi’s Helmet", "Sutton Hoo Helmet"],
        )

    def test_catalog_requires_multiplayer_source(self) -> None:
        crawler.validate_catalog(catalog())
        invalid = catalog()
        invalid["source"] = "https://example.invalid"
        with self.assertRaises(crawler.CrawlError):
            crawler.validate_catalog(invalid)


class MultiplayerEmbeddingTests(unittest.TestCase):
    def test_embedded_multiplayer_catalog_round_trips(self) -> None:
        serialized = crawler.serialize_catalog(catalog())
        html = crawler.EMBED_START + "{}" + crawler.EMBED_END
        updated = crawler.embed_catalog(html, serialized, Path("helper.html"))
        start = updated.index(crawler.EMBED_START) + len(crawler.EMBED_START)
        end = updated.index(crawler.EMBED_END, start)
        self.assertEqual(updated[start:end].replace("<\\/", "</"), serialized.rstrip())


if __name__ == "__main__":
    unittest.main()
