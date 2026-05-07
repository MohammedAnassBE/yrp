"""Rename Item Production Detail.primary_attribute → primary_item_attribute.

Mirrors production_api's IPD field naming. Idempotent — safe to re-run.
"""

import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if not frappe.db.has_column("Item Production Detail", "primary_attribute"):
		# Already renamed (or never existed) — nothing to do.
		return
	rename_field("Item Production Detail", "primary_attribute", "primary_item_attribute")
