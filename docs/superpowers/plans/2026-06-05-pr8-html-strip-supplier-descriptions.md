# PR-8 HTML-Strip Supplier Descriptions — Implementation Plan

**Goal:** Hot-Fix für den Real-Sync — beide Supplier-Fetcher liefern Description-
Strings via gemeinsamen `_clean_description()` Helper, der HTML-Entities
decoded UND Tags stript.

**Tech:** Python stdlib only (`html.unescape` + existing `re`). Keine neuen Deps.

---

## File Structure

**Modified:**
- `scripts/inventree_sync/fetchers.py` — `_clean_description()` Helper auf
  Modul-Ebene; `LCSCFetcher._parse` und `MouserFetcher.fetch` nutzen ihn.

**Created:**
- `scripts/tests/test_clean_description.py` — ~12 pytest cases.

**Untouched:**
- Alles andere (models.py, client.py, part_manager.py, …).

---

## Task 1: Pytest red

- [ ] **Step 1.1: Pytest-File anlegen**

Siehe Spec für die ~12 Cases (test_clean_description.py).

- [ ] **Step 1.2: pytest -v → confirm red (ImportError)**

```bash
python3 -m pytest scripts/tests/test_clean_description.py -v
```

Expected: ImportError "cannot import _clean_description from inventree_sync.fetchers".

---

## Task 2: Implement helper + integration

- [ ] **Step 2.1: Helper in fetchers.py**

`import html` zur Import-Sektion, `_HTML_TAG_RE` + `_clean_description()`
Modul-level nach `_make_retry_session()`.

- [ ] **Step 2.2: LCSCFetcher._parse — Description durch Helper**

```python
description=_clean_description(product.get("productDescEn", "")),
```

- [ ] **Step 2.3: MouserFetcher.fetch — existing strip ersetzen**

Alte Zeile `description = re.sub(r"<[^>]+>", "", p.get("Description", ""))`
ersetzen durch:

```python
description = _clean_description(p.get("Description", ""))
```

- [ ] **Step 2.4: pytest grün**

```bash
python3 -m pytest scripts/tests/ -q
```

Expected: 115 + ~12 cases = ~127 grün.

- [ ] **Step 2.5: Smoke-Test gegen echtes LCSC**

```bash
python3 -c "
import os
os.environ.setdefault('MOUSER_API_KEY','dummy')
from scripts.inventree_sync.fetchers import LCSCFetcher
d = LCSCFetcher().fetch_by_sku('C5185863')  # LMR51430 – hat das &reg; Issue
print(repr(d.description))
assert '&' not in d.description, 'still has entity!'
assert '<' not in d.description and '>' not in d.description, 'still has tags!'
print('OK')
"
```

Expected: `'Schaltspannungsregler SIMPLE SWITCHER® 4.5-V to 36-V 3-A'` o.ä.,
ohne `&reg;` / Tags.

---

## Task 3: Commit, Push, PR, Copilot, Merge

- [ ] **Step 3.1: Commit**

```
feat(inventree-sync): strip HTML tags + decode entities in supplier descriptions

LCSC and Mouser occasionally return descriptions with HTML tags (<b>, <sup>)
or entities (&reg;, &trade;, &plusmn;). InvenTree's Part.description
field rejects them with HTTP 400 "Remove HTML tags from this value".

Adds _clean_description() helper (html.unescape + tag-regex strip, order
matters so encoded tags get decoded then stripped). Both fetchers route
through it. Mouser's existing PR-3 tag-strip is consolidated to the helper.

Discovered in the first CI Real-Sync of PowerBoard v1.1: LMR51430's LCSC
description "Schaltspannungsregler SIMPLE SWITCHER&reg; ..." was the only
Part that failed creation among 17 BOM entries.
```

- [ ] **Step 3.2: Push + PR**

```bash
git push -u origin feat/pr8-html-strip-supplier-descriptions
gh pr create --title "feat(inventree-sync): PR-8 strip HTML tags + decode entities in supplier descriptions" --body ...
```

- [ ] **Step 3.3: Copilot review loop**

Standard 4min initial wait + 1min poll bis 10min total. Findings adressieren,
push, neuer round, bis sauber.

- [ ] **Step 3.4: Squash-Merge + sync main**

- [ ] **Step 3.5: Re-trigger PowerBoard v1.1 workflow**

```bash
gh workflow run "Create Release Docs" --repo OE5XRX/HW-Module-PowerBoard --ref v1.1
```

Beobachten bis fertig, dann Bestandsaufnahme auf parts.oe5xrx.org.
