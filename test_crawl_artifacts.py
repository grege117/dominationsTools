#!/usr/bin/env python3
"""Fast, network-free tests for the Legendary War Artifact crawler."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

import crawl_artifacts as crawler


def available_artifact(name: str = "Test Artifact") -> dict[str, object]:
    return {
        "name": name,
        "status": "available",
        "bonuses": [
            "Bomber Damage +7%",
            "Bomber Hitpoints +6%",
            "Quick Victory Time +3%",
            "Air Defense Damage +8%",
            "Invading Fighter Damage -6%",
        ],
    }


def valid_catalog() -> dict[str, object]:
    return {
        "source": crawler.SOURCE_URL,
        "artifacts": [
            available_artifact(),
            {"name": "Unavailable Artifact", "status": "unavailable", "bonuses": []},
        ],
    }


class ArtifactDiscoveryTests(unittest.TestCase):
    def test_nested_artifact_links_are_discovered_but_group_labels_are_not(self) -> None:
        wikitext = """Intro
== List of Legendary War Artifacts ==
* [[Battersea Shield]]
* '''Gemini 8'''
** [[Gemini 8 Gloves|Gloves]]
** [[Gemini 8 Helmet|Helmet]]
* '''Da Vinci's'''
** [[Da Vinci's Flying Machine|Flying Machine]]
** [[Da Vinci's Machine Gun|Machine Gun]]
== See also ==
* [[Not a War Artifact]]
"""

        artifacts = crawler.discover_artifacts(wikitext)

        self.assertEqual(
            [(item["target"], item["name"]) for item in artifacts],
            [
                ("Battersea Shield", "Battersea Shield"),
                ("Gemini 8 Gloves", "Gemini 8 Gloves"),
                ("Gemini 8 Helmet", "Gemini 8 Helmet"),
                ("Da Vinci's Flying Machine", "Da Vinci's Flying Machine"),
                ("Da Vinci's Machine Gun", "Da Vinci's Machine Gun"),
            ],
        )

    def test_missing_list_and_duplicate_targets_are_rejected(self) -> None:
        with self.assertRaises(crawler.CrawlError):
            crawler.discover_artifacts("== A different section ==\n* [[Artifact]]")

        duplicate = """== List of Legendary War Artifacts ==
* [[Battersea Shield]]
* [[Battersea Shield|A duplicate link]]
"""
        with self.assertRaises(crawler.CrawlError):
            crawler.discover_artifacts(duplicate)


class ArtifactStatisticsTests(unittest.TestCase):
    def test_singular_heading_and_inline_cells_are_parsed(self) -> None:
        wikitext = """== Statistic ==
{| class="article-table"
! Benefit Name !! Base Stat
|-
| [[Bombers|Bomber]] Damage || +8%
|-
| Invading [[Factory]] [[Troops|Troop]] Hitpoints || -6%
|-
| Quick Victory Time || +9%
|-
| Air Defense Damage || +7%
|-
| Fighter Hitpoints || +6%
|}
"""

        self.assertEqual(
            crawler.extract_bonuses(wikitext, "Inline Artifact"),
            [
                "Bomber Damage +8%",
                "Invading Factory Troop Hitpoints -6%",
                "Quick Victory Time +9%",
                "Air Defense Damage +7%",
                "Fighter Hitpoints +6%",
            ],
        )

    def test_extra_leading_table_delimiter_is_not_part_of_the_bonus_name(self) -> None:
        wikitext = """== Statistic ==
{| class="article-table"
! Benefit Name !! Base Stat
|-
||Invading [[Generals|General's]] Damage|| -6%
|-
| Enemy Defender Spawn Time || +6%
|-
| Defender Damage || +6%
|-
| Defender Hitpoints || +11%
|-
| Invading [[Generals|General's]] Hitpoints || -11%
|}
"""

        self.assertEqual(
            crawler.extract_bonuses(wikitext, "Leading delimiter artifact")[0],
            "Invading General's Damage -6%",
        )

    def test_plural_heading_and_separate_cell_lines_are_parsed(self) -> None:
        wikitext = """== Statistics ==
{| class="article-table"
!Benefit Name
!Base Stat
|-
|[[Heavy Infantry]] Damage
| +7%
|-
|[[Heavy Infantry]] Attack Speed
| +7%
|-
|[[APC]] Hitpoints
| +6%
|-
|[[APC]] Deploy Time
| -6%
|-
|Quick Victory Time
| +3%
|}
"""

        self.assertEqual(
            crawler.extract_bonuses(wikitext, "Alfred-like Artifact"),
            [
                "Heavy Infantry Damage +7%",
                "Heavy Infantry Attack Speed +7%",
                "APC Hitpoints +6%",
                "APC Deploy Time -6%",
                "Quick Victory Time +3%",
            ],
        )

    def test_bare_wiki_fallback_uses_the_display_label(self) -> None:
        wikitext = """== Statistic ==
{| class="article-table"
! Benefit Name !! Base Stat
|-
| Airstip Troop Hitpoints | Airstrip Troop Hitpoints || +7%
|-
| Bomber Damage || +8%
|-
| Quick Victory Time || +9%
|-
| Air Defense Damage || +7%
|-
| Fighter Hitpoints || +6%
|}
"""

        self.assertEqual(
            crawler.extract_bonuses(wikitext, "Fallback Artifact")[0],
            "Airstrip Troop Hitpoints +7%",
        )

    def test_rowspan_value_is_propagated_and_duplicate_slots_are_preserved(self) -> None:
        wikitext = """== Statistics ==
{| class="article-table"
! Benefit Name !! Base Stat
|-
| [[Bombers|Bomber]] Hitpoints || rowspan="2" | +6%
|-
| [[Bombers|Bomber]] Hitpoints
|-
| Recon Bonus Duration || +3%
|-
| Bazooka Attack Speed || +3%
|-
| Quick Victory Time || +6%
|}
"""

        self.assertEqual(
            crawler.extract_bonuses(wikitext, "Rowspan Artifact"),
            [
                "Bomber Hitpoints +6%",
                "Bomber Hitpoints +6%",
                "Recon Bonus Duration +3%",
                "Bazooka Attack Speed +3%",
                "Quick Victory Time +6%",
            ],
        )

    def test_completely_blank_statistic_table_is_unavailable(self) -> None:
        wikitext = """== Statistics ==
{| class="article-table"
!Benefit Name
!Base Stat
|-
| ||
|-
| ||
|-
| ||
|-
| ||
|-
| ||
|}
"""

        self.assertEqual(crawler.extract_bonuses(wikitext, "Blank Artifact"), [])

    def test_incomplete_or_structurally_invalid_table_is_rejected(self) -> None:
        partial_blank = """== Statistics ==
{| class="article-table"
! Benefit Name !! Base Stat
|-
| Bomber Damage || +8%
|-
| ||
|-
| Quick Victory Time || +9%
|-
| Air Defense Damage || +7%
|-
| Fighter Hitpoints || +6%
|}
"""
        bad_header = partial_blank.replace("Base Stat", "Level 10")

        for wikitext in (partial_blank, bad_header, "No statistics here"):
            with self.subTest(wikitext=wikitext[:30]):
                with self.assertRaises(crawler.CrawlError):
                    crawler.extract_bonuses(wikitext, "Broken Artifact")


class CatalogValidationTests(unittest.TestCase):
    def test_valid_available_and_unavailable_artifacts_are_accepted(self) -> None:
        crawler.validate_catalog(valid_catalog())

    def test_duplicate_names_and_invalid_status_bonus_combinations_are_rejected(self) -> None:
        duplicate = valid_catalog()
        duplicate["artifacts"].append(available_artifact())

        bad_status = valid_catalog()
        bad_status["artifacts"][0]["status"] = "unknown"

        empty_available = valid_catalog()
        empty_available["artifacts"][0]["bonuses"] = []

        populated_unavailable = valid_catalog()
        populated_unavailable["artifacts"][1]["bonuses"] = ["Bomber Damage +1%"]

        for catalog in (
            duplicate,
            bad_status,
            empty_available,
            populated_unavailable,
        ):
            with self.subTest(catalog=catalog):
                with self.assertRaises(crawler.CrawlError):
                    crawler.validate_catalog(catalog)


class CatalogEmbeddingTests(unittest.TestCase):
    def test_serialized_and_embedded_catalogs_are_equal_json(self) -> None:
        catalog = valid_catalog()
        catalog["artifacts"][0]["name"] = "Ramesses Ⅱ's </script> Relic"
        serialized = crawler.serialize_catalog(catalog)
        original_html = (
            "<!doctype html>\n<p>before</p>\n"
            + crawler.EMBED_START
            + '{"old": true}'
            + crawler.EMBED_END
            + "\n<p>after</p>\n"
        )

        updated = crawler.embed_catalog(
            original_html, serialized, Path("artifact-helper.html")
        )

        start = updated.index(crawler.EMBED_START) + len(crawler.EMBED_START)
        end = updated.index(crawler.EMBED_END, start)
        embedded_text = updated[start:end]
        self.assertEqual(json.loads(serialized), catalog)
        self.assertEqual(json.loads(embedded_text), catalog)
        self.assertNotIn("</script>", embedded_text.casefold())
        self.assertTrue(updated.startswith("<!doctype html>\n<p>before</p>\n"))
        self.assertTrue(updated.endswith("\n<p>after</p>\n"))

    def test_missing_duplicate_or_unclosed_embed_markers_are_rejected(self) -> None:
        serialized = crawler.serialize_catalog(valid_catalog())
        complete = crawler.EMBED_START + "{}" + crawler.EMBED_END
        cases = {
            "missing opening marker": "<html></html>",
            "duplicate opening marker": complete + complete,
            "missing closing marker": crawler.EMBED_START + "{}",
        }
        for label, html_text in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(crawler.CrawlError):
                    crawler.embed_catalog(
                        html_text, serialized, Path("artifact-helper.html")
                    )


class CliValidationTests(unittest.TestCase):
    def test_invalid_numeric_arguments_return_two_without_crawling(self) -> None:
        invalid_argv = (
            ["--timeout", "0", "--skip-html"],
            ["--retries=-1", "--skip-html"],
            ["--batch-size", "0", "--skip-html"],
            ["--batch-size", "51", "--skip-html"],
        )

        with mock.patch.object(crawler, "build_catalog") as build_catalog:
            for argv in invalid_argv:
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(crawler.main(argv), 2)
            build_catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
