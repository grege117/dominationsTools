# DomiNations Tools

A small, dependency-free collection of browser tools for DomiNations players. Each tool is designed to run as a static page, so it can be opened directly from disk or served from any static web host.

## Current tools

### Legendary Artifact Helper

`legendary-artifact-finder.html` helps players search War or Multiplayer Legendary Artifact bonuses and build a personal setup for either collection.

It includes:

- Case-insensitive bonus tags with highlighted matches.
- Separate Owned Artifacts columns and saved setups for War and Multiplayer collections.
- Up to four fully Active artifacts, plus one selected bonus from each other owned artifact.
- An alphabetized Active bonuses list.
- Copy/Load sharing strings that automatically switch to the matching collection.
- Read-only Copy Link URLs that show a shared setup without changing the visitor's local configuration.
- Built-in Help instructions.

The page embeds both catalogs for direct-file use. When served, it refreshes the selected catalog from `legendary-war-artifacts.json` or `legendary-multiplayer-artifacts.json` and falls back to its embedded copy if that request fails.

### Council Helper

`councilor-helper.html` helps players search both War Chamber and Primary Chamber (Multiplayer) councilor bonuses and assemble a seven-seat council for either mode.

It includes:

- A War/Multiplayer toggle with independent seven-councilor selections for each mode.
- Case-insensitive search across councilor names, types, and the selected mode's bonuses.
- Type and displayed-rarity controls.
- A seven-councilor setup with an independent rarity for each seat.
- An alphabetized list of all active bonuses for the selected mode.
- Browser-local persistence and Copy/Load sharing strings; shared setups include their mode.
- Read-only Copy Link URLs that preserve the visitor's local Council setup and show rarity-colored selected Councilors.
- Built-in Help instructions.

The page is also self-contained. Its embedded data matches the reusable `councilors.json` file.

## Run locally

Open either HTML page directly in a modern browser, or from the repository root start Python's built-in HTTP server:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory .
```

Then open either helper:

- http://127.0.0.1:8000/legendary-artifact-finder.html
- http://127.0.0.1:8000/councilor-helper.html

Serving the directory allows the Artifact Helper to refresh its external JSON catalogs; both pages also work directly from disk using their embedded data.

With GitHub Pages enabled for this repository, the hosted tools are available at:

- https://grege117.github.io/dominationsTools/legendary-artifact-finder.html
- https://grege117.github.io/dominationsTools/councilor-helper.html

## Data

The JSON files are reusable catalogs for the browser pages. Each crawler uses only the Python standard library and the public DomiNations Fandom MediaWiki API. A successful run validates the parsed tables, writes the external JSON, and replaces the matching embedded JSON block in the HTML page so direct-file use continues to work.

### Legendary Artifact catalogs

War artifact names are sourced from the [DomiNations Legendary War Artifacts wiki page](https://dominations.fandom.com/wiki/Legendary_War_Artifacts#List_of_Legendary_War_Artifacts). Multiplayer artifact names are sourced from the [DomiNations Legendary Artifacts wiki page](https://dominations.fandom.com/wiki/Legendary_Artifacts). Bonuses are read from the Statistic table on each linked artifact page. Linked artifacts whose page or bonus data is unavailable remain in the catalog with an `unavailable` status.

When updating data, keep each external artifact JSON file and its matching embedded JSON block in `legendary-artifact-finder.html` identical.

`legendary-war-artifacts.json` and `legendary-multiplayer-artifacts.json` use this format:

```json
{
  "source": "wiki list URL",
  "artifacts": [
    {
      "name": "Artifact name",
      "status": "available",
      "bonuses": ["Bonus text", "..."]
    }
  ]
}
```

`status` is either `available` or `unavailable`. Unavailable artifacts have an empty `bonuses` list because the crawler could not find usable bonus data on the source page.

Regenerate and validate the Legendary War Artifact data with:

```bash
python3 crawl_artifacts.py
```

The crawler updates `legendary-war-artifacts.json` and the embedded data in `legendary-artifact-finder.html` together. Use `--skip-html` to generate only the reusable JSON file.

Regenerate Multiplayer Legendary Artifact data with:

```bash
python3 crawl_multiplayer_artifacts.py
```

The Multiplayer crawler updates `legendary-multiplayer-artifacts.json` and its embedded page data together.

### Councilor catalog

Councilor names and types are sourced from the [DomiNations Councilors list](https://dominations.fandom.com/wiki/Councilors#Councilors_List). War and Multiplayer bonuses are read from the War Chamber and Primary Chamber tables on each councilor's page and expanded into the complete effective bonus list for every rarity.

`councilors.json` uses this format:

```json
{
  "source": "wiki list URL",
  "generatedAt": "ISO-8601 UTC timestamp",
  "rarities": ["Common", "Uncommon", "Rare", "Epic", "Legendary"],
  "councilors": [
    {
      "id": "stable-lowercase-id",
      "name": "Councilor name",
      "type": "Leader",
      "sourceUrl": "wiki page URL",
      "warBonuses": {
        "Common": [{"stat": "Bonus name", "value": "+1%"}]
      },
      "multiplayerBonuses": {
        "Common": [{"stat": "Bonus name", "value": "+1%"}]
      }
    }
  ]
}
```

Every Councilor includes all five rarity keys in both bonus maps. The entries are the effective bonuses at that rarity, so a higher rarity repeats benefits unlocked at lower rarities where the wiki table provides them.

Regenerate and validate the Councilor data with:

```bash
python3 crawl_councilors.py
```

The crawler updates `councilors.json` and the embedded data in `councilor-helper.html` together.

## Data consistency audit

`stat-label-inconsistencies.html` reports live wiki labels that abbreviate, misspell, or inconsistently capitalize the words `Hitpoints` and `Damage`. Each finding includes a proposed label and a link to the affected artifact or councilor page. Missing or structurally incomplete wiki pages are listed separately as crawl warnings.

Regenerate the standalone report with:

```bash
python3 generate_label_inconsistency_report.py
```

Run its network-free regression tests with:

```bash
python3 -m unittest -v test_label_inconsistency_report.py
```

The audit reads the wiki directly and does not modify either helper or its JSON data. When GitHub Pages is enabled, the report is available at https://grege117.github.io/dominationsTools/stat-label-inconsistencies.html.

## Adding tools

Future tools should remain standalone HTML pages where practical, use no external runtime dependencies, and have a clear link or entry added here. Shared data files and small standard-library helper scripts can live at the repository root until the collection needs a more structured layout.
