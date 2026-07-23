import unittest

from crawl_councilors import CrawlError, extract_multiplayer_bonuses, extract_war_bonuses


def chamber_table(name, stat):
    return f"""=== {name} Seats ===
{{| class="wikitable"
! Benefits !! Common !! Uncommon !! Rare !! Epic !! Legendary
|-
| {stat} || +1% || +2% || +3% || +4% || +5%
|}}
"""


class CouncilorBonusExtractionTests(unittest.TestCase):
    def test_extracts_war_and_multiplayer_tables_independently(self):
        source = (
            chamber_table("Primary Chamber", "Gold Loot")
            + chamber_table("War Chamber", "Infantry Damage")
        )

        war = extract_war_bonuses(source, "Example")
        multiplayer = extract_multiplayer_bonuses(source, "Example")

        self.assertEqual(war["Legendary"], [{"stat": "Infantry Damage", "value": "+5%"}])
        self.assertEqual(multiplayer["Legendary"], [{"stat": "Gold Loot", "value": "+5%"}])

    def test_reports_missing_primary_chamber_table(self):
        with self.assertRaisesRegex(CrawlError, "Primary Chamber"):
            extract_multiplayer_bonuses(chamber_table("War Chamber", "Infantry Damage"), "Example")


if __name__ == "__main__":
    unittest.main()
