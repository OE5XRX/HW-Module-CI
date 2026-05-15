# HW-Module-CI

Shared CI infrastructure for OE5XRX hardware module repositories.

This repo holds the reusable GitHub workflows, KiBot configs, Python scripts, and Jekyll assets that every hardware module repo (`HW-Module-FMTransceiver`, `HW-Module-BusBoard`, `HW-Module-CM4Carrier`, `HW-Module-DeviceTester`, `HW-Module-PowerBoard`, plus `HW-DebugBoard`) consumes via thin wrapper workflows.

The release model the Auto-Release workflow implements is documented at [oe5xrx.org/docs/remote-station/hardware/versioning/](https://oe5xrx.org/docs/remote-station/hardware/versioning/).

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

### `.github/workflows/auto-release.yaml`

```yaml
name: Auto-Release
on:
  workflow_dispatch:
permissions:
  contents: write
jobs:
  call:
    uses: OE5XRX/HW-Module-CI/.github/workflows/auto-release.yaml@main
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
- `RELEASE_TRIGGER_TOKEN` — fine-grained PAT, `contents:write` + `actions:write`, scoped to the HW-Module-* repos. Used by the Auto-Release workflow so the created tag triggers the downstream `release: published` event (the default `GITHUB_TOKEN` would not).

All four should have visibility `all` so any repo in the OE5XRX org can use them via `secrets: inherit`.

## Layout of this repo

```
.github/
  actions/setup/action.yml         # Dual-checkout + project detection + Jekyll staging
  workflows/
    kibot-check.yaml               # ERC + DRC preflight
    create-debug-docs.yaml         # On push to main → publish to gh-pages of caller
    create-release-docs.yaml       # On release → deploy to OE5XRX.github.io + InvenTree sync
    auto-release.yaml              # On workflow_dispatch → diff since last tag → gh release create
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
  compute_next_version.py          # Decide next semver tag for Auto-Release
  make_stencil_image.py            # Post-process KiBot SVG → stencil PNG
  inventree_sync/                  # Package: KiCad→InvenTree part syncing
  requirements.txt                 # Runtime deps installed in release runs
  requirements-dev.txt             # pytest, used only in HW-Module-CI self-CI
  tests/                           # pytest suite for the helper scripts
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

A `.github/workflows/ci.yaml` self-lint workflow runs on every PR to this repo: actionlint, `py_compile`, pip install dry-run on `scripts/requirements.txt`, and `yaml.safe_load` on every YAML we ship. Required check before merge to `main`.

## Known gotchas

Bugs we hit during the rollout. Documented so they don't get re-introduced.

### `github.workflow_sha` does NOT refer to the called workflow

Inside a reusable workflow (`on: workflow_call`), `github.workflow_sha` resolves to the **calling** workflow's SHA, not the SHA of this repo's workflow file. Trying to `actions/checkout` HW-Module-CI at that SHA fails with `remote error: upload-pack: not our ref <sha>` because the SHA only exists in the consumer's repo.

→ The composite action checks out HW-Module-CI at `ref: main` (floating), matching the spec's `@main` pinning decision. Tiny race window when `main` moves between dispatch and checkout, accepted.

History: `6a1d809` (discovered during FM migration smoke test).

### `actions/configure-pages@v5` requires Pages to be pre-enabled

The action fails with `Get Pages site failed. Please verify that the repository has Pages enabled` on any repo without GitHub Pages turned on. For repos that have never deployed gh-pages before, this is a chicken-and-egg problem (Pages source = `gh-pages` branch, but the branch doesn't exist yet — needs a deploy to create it; deploy is blocked by `configure-pages` failing).

→ We use `JamesIves/github-pages-deploy-action@v4` which creates the gh-pages branch if missing. `configure-pages` was a holdover from the official Pages flow; its step output was never referenced downstream. Removed.

History: `7915ada` (discovered during CM4Carrier migration smoke test).

### Tag names with `/` or `&` break the `<<VERSION>>` sed substitution

`create-release-docs.yaml` injects the release tag (with leading `v` stripped, e.g. `v1.5` → `1.5`) into KiCad title blocks via `sed -i "s/<<VERSION>>/.../g" *.kicad_*`. Release tag names can legally contain `/` (e.g. `release/1.2`), `&`, or `\` — all of which sed treats as metacharacters in the replacement side.

→ The replacement string is escaped via `printf '%s' "$RAW" | sed -e 's/[\/&\\]/\\&/g'` before being interpolated into the outer `sed -i`.

The `<<VERSION>>` placeholder convention is documented at [oe5xrx.org/docs/remote-station/hardware/versioning/](https://oe5xrx.org/docs/remote-station/hardware/versioning/). Module schematics carry the literal string `<<VERSION>>` in their title block; CI substitutes it at build time with the appropriate semver (or `BETA-<commit>` in debug-docs).

### InvenTree sync runs LAST and non-blocking

If InvenTree (the supplier-parts server) is down or misbehaves on release day, the BOM upload step fails — but the docs deploy to OE5XRX.github.io already happened in the prior step, and `continue-on-error: true` keeps the workflow from aborting. The InvenTree step shows red in the UI; re-run via `gh run rerun <id> --failed`.

### File overwrites at CI time

The `setup` composite action unconditionally copies `_ci/doc/{_config.yml,Gemfile,favicon.ico,Icon.png}` into the caller's `doc/`, overwriting whatever the consumer ships under the same names. **Do not maintain board-specific variants of these files in module repos** — customisations go here in HW-Module-CI so every consumer picks them up via `@main`.
