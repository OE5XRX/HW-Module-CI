# HW-Module-CI

Shared CI infrastructure for OE5XRX hardware module repositories.

This repo holds the reusable GitHub workflows, KiBot configs, Python scripts, and Jekyll assets that every hardware module repo (`HW-Module-FMTransceiver`, `HW-Module-BusBoard`, `HW-Module-CM4Carrier`, `HW-Module-DeviceTester`, `HW-Module-PowerBoard`, plus `HW-DebugBoard`) consumes via thin wrapper workflows.

## Repository convention

Every consumer repo MUST have:

1. **Exactly one `.kicad_pro` file at the repo root.** The basename can be anything — `FMTransceiver.kicad_pro`, `PowerBoard.kicad_pro`, etc. The corresponding `<name>.kicad_sch` and `<name>.kicad_pcb` must live alongside.
2. **A `doc/index.md` file** with Jekyll front-matter (`title`, `nav_order`, `parent`) and the page body. KiBot output paths in the body MUST use the Jekyll variable `{{ site.data.project.name }}`, e.g.:

   ```markdown
   - [Schaltplan]({{ site.data.project.name }}-schematic.pdf)
   - [BOM]({{ site.data.project.name }}-bom.html)
   ```

That's it. No `scripts/`, no `Gemfile`, no `kibot.yaml`, no `doc/_config.yml` in the consumer repo — everything CI-related lives here.

## Files overwritten at CI time (do not customize locally)

The `setup` composite action copies the following files from this repo into each consumer's working tree at the start of every CI run, **overwriting anything the consumer ships under the same names without warning**:

- `doc/_config.yml`
- `doc/Gemfile`
- `doc/favicon.ico`
- `doc/Icon.png`

Additionally, `doc/_data/project.yml` is **generated at runtime** by CI (containing the auto-detected project name). Consumers should keep this path in their `.gitignore` and never commit it.

Consequence: do not maintain board-specific variants of these files in module repos. If you need to customize any of them (e.g., to add a Jekyll plugin), commit the change here in `HW-Module-CI` so every consumer picks it up via `@main`.

## Consumer-side workflow files

Each consumer repo carries three workflows. They are nearly identical across all repos — only the `on:` triggers differ if you need to customise.

### `.github/workflows/kibot-check.yaml`

```yaml
name: KiBot Check
on:
  workflow_dispatch:
  pull_request:
    branches: [main]
  push:
    branches: [main]
jobs:
  check:
    uses: OE5XRX/HW-Module-CI/.github/workflows/kibot-check.yaml@main
    secrets: inherit
```

### `.github/workflows/create-debug-docs.yaml`

```yaml
name: Create Debug Docs
on:
  workflow_dispatch:
  push:
    branches: [main]
permissions:
  contents: write
jobs:
  build:
    uses: OE5XRX/HW-Module-CI/.github/workflows/create-debug-docs.yaml@main
    secrets: inherit
```

### `.github/workflows/create-release-docs.yaml`

```yaml
name: Create Release Docs
on:
  workflow_dispatch:
  release:
    types: [published]
jobs:
  build:
    uses: OE5XRX/HW-Module-CI/.github/workflows/create-release-docs.yaml@main
    secrets: inherit
```

## Pinning policy

Consumer workflows pin to `@main` (floating). Breaking changes pushed here affect all consumers immediately — coordinate before pushing destructive changes to `main`.

## Required org-level secrets (OE5XRX)

The release workflow expects these secrets to be available org-wide:

- `INVENTREE_API_TOKEN`
- `INVENTREE_API_HOST`
- `MOUSER_API_KEY`
- `DEPLOY_GH_TOKEN`

All four should have visibility `all` so any repo in the OE5XRX org can use them via `secrets: inherit`.

## Layout of this repo

```
.github/
  actions/setup/action.yml         # Dual-checkout + project detection + Jekyll staging
  workflows/
    kibot-check.yaml               # ERC + DRC preflight
    create-debug-docs.yaml         # On push to main → publish to gh-pages of caller
    create-release-docs.yaml       # On release → deploy to OE5XRX.github.io + InvenTree sync
kibot/
  production.kibot.yaml            # Full board production export
  test.kibot.yaml                  # ERC/DRC preflight only
doc/
  _config.yml                      # Jekyll config (just-the-docs theme)
  Gemfile                          # Ruby deps for Jekyll build
  favicon.ico
  Icon.png
scripts/
  bom_export.py                    # Push BOM to InvenTree on release
  make_stencil_image.py            # Post-process KiBot SVG → stencil PNG
  inventree_sync/                  # Package: KiCad→InvenTree part syncing
  requirements.txt
```

## Local development

To validate workflow YAML before pushing:

```bash
# Python YAML syntax
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/kibot-check.yaml'))"

# Optional: install actionlint and run
actionlint .github/workflows/*.yaml
```

To validate Python scripts:

```bash
python3 -m py_compile scripts/bom_export.py scripts/make_stencil_image.py
python3 -m py_compile scripts/inventree_sync/*.py
```

There is no local "smoke test" — the workflows can only execute meaningfully when invoked from a consumer repo with a real KiCad project at its root.
