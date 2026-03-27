# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Idempotent patch: reads YRP Stock Settings and creates Custom Fields
	on stock/operational DocTypes for each configured stock dimension.
	"""
	if not frappe.db.exists("DocType", "YRP Stock Settings"):
		return

	from yrp.stock.dimensions import create_dimension_fields
	create_dimension_fields()
