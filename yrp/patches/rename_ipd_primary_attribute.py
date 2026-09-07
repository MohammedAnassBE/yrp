"""Rename Item Production Detail.primary_attribute → primary_item_attribute.

Idempotent — safe to re-run.
"""

import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if not frappe.db.has_column('YRP Item Production Detail', "primary_attribute"):
		# Already renamed (or never existed) — nothing to do.
		return
	rename_field('YRP Item Production Detail', "primary_attribute", "primary_item_attribute")
