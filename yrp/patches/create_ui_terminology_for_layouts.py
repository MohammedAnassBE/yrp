# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe


def execute():
	if not frappe.db.exists("DocType", "YRP UI Terminology"):
		return

	for layout in frappe.get_all("UI Layout", pluck="name"):
		if frappe.db.exists("YRP UI Terminology", {"ui_layout": layout}):
			continue
		frappe.get_doc(
			{
				"doctype": "YRP UI Terminology",
				"ui_layout": layout,
			}
		).insert(ignore_permissions=True)
