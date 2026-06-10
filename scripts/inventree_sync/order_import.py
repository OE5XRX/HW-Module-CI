"""
order_import.py – Import historical supplier orders into InvenTree.

Parses Mouser-XLS and LCSC-CSV order files, resolves each line to an
InvenTree Part (reusing the `inventree_sync` BOM-export primitives) and
upserts a PurchaseOrder per supplier with received StockItems.

File is source-of-truth on re-runs:
  - PO not yet present       → CREATE + receive
  - PO PENDING/PLACED        → reconcile line-items against file → receive
  - PO COMPLETE, in-sync     → no-op
  - PO COMPLETE, drift       → loud-fail, no writes
"""
