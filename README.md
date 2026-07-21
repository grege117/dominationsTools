# DomiNations Tools

A small, dependency-free collection of browser tools for DomiNations players. Each tool is designed to run as a static page, so it can be opened directly from disk or served from any static web host.

## Current tool: Legendary War Artifact Helper

`legendary-artifact-finder.html` helps players search Legendary War Artifact bonuses and build a personal artifact setup.

It includes:

- Case-insensitive bonus tags with highlighted matches.
- An Owned Artifacts column stored in the browser's local storage.
- Up to four fully Active artifacts, plus one selected bonus from each other owned artifact.
- An alphabetized Active bonuses list.
- Copy/Load sharing strings for transferring a setup between users or browsers.
- Built-in Help instructions.

The page is self-contained and embeds its artifact data, so it does not require a web server. `legendary-war-artifacts.json` is the matching reusable data file.

## Run locally

Open `legendary-artifact-finder.html` directly in a modern browser, or start the included standard-library Python server:

```bash
python3 serve_artifact_finder.py
```

Then open http://127.0.0.1:8000/legendary-artifact-finder.html.

## Data

Artifact data is primarily sourced from the [DomiNations Legendary War Artifacts wiki page](https://dominations.fandom.com/wiki/Legendary_War_Artifacts#List_of_Legendary_War_Artifacts), with individual artifact pages used when the index does not provide bonus rows. Newer artifacts may need to be added manually until that index is updated.

When updating data, keep `legendary-war-artifacts.json` and the JSON embedded in `legendary-artifact-finder.html` identical.

## Adding tools

Future tools should remain standalone HTML pages where practical, use no external runtime dependencies, and have a clear link or entry added here. Shared data files and small standard-library helper scripts can live at the repository root until the collection needs a more structured layout.
