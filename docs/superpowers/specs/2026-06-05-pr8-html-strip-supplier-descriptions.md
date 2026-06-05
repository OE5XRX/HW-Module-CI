# PR-8 — HTML-Tags + Entities in Supplier-Descriptions strippen

**Status:** Spec ready.
**Scope:** Hot-Fix vor dem Marathon-Sync. LCSC und Mouser liefern Beschreibungen
mit HTML-Tags (`<b>`, `<sup>`) **und** HTML-Entities (`&reg;`, `&trade;`,
`&amp;`). InvenTree's Server validiert die Description-Feld mit einer
"keine HTML"-Regel — und lehnt Strings mit `&reg;` ab.
**Predecessor:** PR-7 CI bom_export args (main @ fac64a9).
**Erstellt:** 2026-06-05 nach erstem CI-Sync der PowerBoard v1.1.

---

## Motivation

Im ersten Real-Sync der PowerBoard v1.1 schlug genau **ein** Part-Create
mit HTTP 400 fehl:

```
ERROR: Part creation failed for 'LMR51430 500kHz':
  body: {"description":["Remove HTML tags from this value"]}
  data.description: 'Schaltspannungsregler SIMPLE SWITCHER&reg; 4.5-V to 36-V 3-A'
```

LCSC liefert `productDescEn` mit `&reg;` (HTML entity), `LCSCFetcher._parse`
reicht es unverändert durch. InvenTree sagt Nein.

`MouserFetcher.fetch` strippt zwar schon `<[^>]+>`-Tags (PR-3) — aber
**ebenfalls keine Entities**. Dasselbe Problem würde dort silently durch-
gehen falls eine Description nur Entities ohne Tags hat.

---

## Goals

- Jeder PartData mit Description aus LCSC oder Mouser wird zu plain Text
  ohne HTML-Tags und ohne HTML-Entities (decoded zu Unicode wo möglich).
- Idempotent: `clean(clean(x)) == clean(x)`.
- Eine Helper-Funktion, beide Fetcher nutzen sie.

## Non-Goals

- Keine Sanitization von anderen PartData-Feldern (datasheet_url, image_url,
  parameters, etc.) — bisher kein Bug-Befund.
- Kein Markdown→Plaintext, kein Whitespace-Collapse — KISS, nur HTML cleanup.

---

## Architektur

### Helper

In `scripts/inventree_sync/fetchers.py` auf Modul-Ebene (beide Klassen nutzen ihn):

```python
import html
from typing import Optional

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_description(text: Optional[str]) -> Optional[str]:
    """Strip HTML tags and decode HTML entities from a supplier description.

    InvenTree's Part.description field rejects strings containing HTML
    markup with HTTP 400 ("Remove HTML tags from this value"). Both LCSC
    and Mouser occasionally return descriptions with <b>/<sup>-style
    tags AND with HTML entities like &reg;, &trade;, &amp;.

    Order matters: unescape FIRST so encoded tag-syntax (&lt;b&gt;) gets
    converted to real tags, then strip — otherwise we'd leave decoded
    tags in the output.

    Examples:
        >>> _clean_description("SIMPLE SWITCHER&reg; buck regulator")
        'SIMPLE SWITCHER® buck regulator'
        >>> _clean_description("<b>10kΩ</b> &plusmn;1%")
        '10kΩ ±1%'
        >>> _clean_description("")
        ''
    """
    if not text:
        return text
    return _HTML_TAG_RE.sub("", html.unescape(text)).strip()
```

### LCSCFetcher integration

In `LCSCFetcher._parse`:

```python
return PartData(
    ...
    description=_clean_description(product.get("productDescEn", "")),
    ...
)
```

### MouserFetcher integration

Existierende Zeile:

```python
description = re.sub(r"<[^>]+>", "", p.get("Description", ""))
```

ersetzen durch:

```python
description = _clean_description(p.get("Description", ""))
```

---

## Tests

### Pytest — `scripts/tests/test_clean_description.py` (neu)

~10 Cases:

```python
from inventree_sync.fetchers import _clean_description

def test_empty_string():           assert _clean_description("") == ""
def test_plain_text_unchanged():   assert _clean_description("100nF cap") == "100nF cap"
def test_entity_reg_decoded():     assert _clean_description("SimpleSwitcher&reg;") == "SimpleSwitcher®"
def test_entity_trade_decoded():   assert _clean_description("Foo&trade;") == "Foo™"
def test_entity_amp_decoded():     assert _clean_description("R&amp;D part") == "R&D part"
def test_entity_pm_decoded():      assert _clean_description("&plusmn;1%") == "±1%"
def test_strip_b_tag():            assert _clean_description("<b>bold</b> text") == "bold text"
def test_strip_sup_tag():          assert _clean_description("10<sup>3</sup>") == "103"
def test_entity_then_tag():        assert _clean_description("&lt;b&gt;real-bold&lt;/b&gt;") == "real-bold"
def test_combined_tags_entities(): assert _clean_description("<b>SWITCHER&reg;</b>") == "SWITCHER®"
def test_idempotent():
    for x in ("", "plain", "SimpleSwitcher&reg;", "<b>&trade;</b>"):
        assert _clean_description(_clean_description(x)) == _clean_description(x)
def test_strips_trailing_whitespace():
    # Stripping a tag can leave dangling whitespace
    assert _clean_description("<p>foo</p>  ") == "foo"
```

---

## Backwards compatibility

| Change | Risk | Mitigation |
|---|---|---|
| Existierende Parts auf InvenTree haben unveränderte Descriptions | Keiner — Refresh-Sync würde sie aktualisieren | n/a |
| Bestehende Mouser-Tests test_mouser_attributes.py | Keiner — Helper macht eine echte Erweiterung des PR-3 Strippings | Identisches Verhalten für reine-Tag-Inputs |
| Cross-fetcher-Konsistenz | Keiner — beide nutzen jetzt denselben Helper | n/a |

---

## Files touched

```
scripts/inventree_sync/fetchers.py        +20 LOC   (Helper + 2 call-sites)
scripts/tests/test_clean_description.py   +50 LOC   (NEW)
```

**Total: ~70 LOC.**

---

## Implementation order

1. Spec + Plan committen.
2. Pytest-File schreiben (alle Cases rot).
3. `_clean_description()` + Aufrufe einbauen.
4. Full pytest grün.
5. Commit, Push, PR, Copilot, Merge.
6. Re-Trigger PowerBoard v1.1 Workflow → Bestandsaufnahme.
