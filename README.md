# DomiNations Tools

A small, dependency-free collection of browser tools for DomiNations players. Each tool is designed to run as a static page, so it can be opened directly from disk or served from any static web host.

## Current tools

### Legendary War Artifact Helper

`legendary-artifact-finder.html` helps players search Legendary War Artifact bonuses and build a personal artifact setup.

It includes:

- Case-insensitive bonus tags with highlighted matches.
- An Owned Artifacts column stored in the browser's local storage.
- Up to four fully Active artifacts, plus one selected bonus from each other owned artifact.
- An alphabetized Active bonuses list.
- Copy/Load sharing strings for transferring a setup between users or browsers.
- Built-in Help instructions.

The page is self-contained and embeds its artifact data, so it does not require a web server. `legendary-war-artifacts.json` is the matching reusable data file.

### War Council Helper

`councilor-helper.html` helps players search War Chamber councilor bonuses and assemble a seven-seat War Council.

It includes:

- Case-insensitive search across councilor names, types, and War bonuses.
- Type and displayed-rarity controls.
- A seven-councilor setup with an independent rarity for each seat.
- An alphabetized list of all active War bonuses.
- Browser-local persistence and Copy/Load sharing strings.
- Built-in Help instructions.

The page is also self-contained. Its embedded data matches the reusable `councilors.json` file.

## Run locally

Open `legendary-artifact-finder.html` directly in a modern browser, or start the included standard-library Python server:

```bash
python3 serve_artifact_finder.py
```

Then open http://127.0.0.1:8000/legendary-artifact-finder.html.

The same server also makes the Council helper available at http://127.0.0.1:8000/councilor-helper.html. Both HTML files can also be opened directly from disk.

To start the Council helper directly and open it in your default browser, run:

```bash
python3 serve_councilor_helper.py
```

Use `--port` to select another port or `--no-browser` when running without a desktop browser.

With GitHub Pages enabled for this repository, the hosted tools are available at:

- https://grege117.github.io/dominationsTools/legendary-artifact-finder.html
- https://grege117.github.io/dominationsTools/councilor-helper.html

## Data

Artifact names are sourced from the [DomiNations Legendary War Artifacts wiki page](https://dominations.fandom.com/wiki/Legendary_War_Artifacts#List_of_Legendary_War_Artifacts). Bonuses are read from the Statistic table on each linked artifact page. Linked artifacts whose page or bonus data is unavailable remain in the catalog with an `unavailable` status.

When updating data, keep `legendary-war-artifacts.json` and the JSON embedded in `legendary-artifact-finder.html` identical.

Regenerate and validate the Legendary War Artifact data with:

```bash
python3 crawl_artifacts.py
```

The crawler updates `legendary-war-artifacts.json` and the embedded data in `legendary-artifact-finder.html` together. Use `--skip-html` to generate only the reusable JSON file.

Councilor names and types are sourced from the [DomiNations Councilors list](https://dominations.fandom.com/wiki/Councilors#Councilors_List). War bonuses are read from the War Chamber table on each councilor's page and expanded into the complete effective bonus list for every rarity.

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
