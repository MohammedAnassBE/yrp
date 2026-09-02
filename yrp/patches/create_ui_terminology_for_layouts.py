# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe


def execute():
	if not frappe.db.exists("DocType", 'YRP YRP UI Terminology'):
		return

	for layout in frappe.get_all('YRP UI Layout', pluck="name"):
		if frappe.db.exists('YRP YRP UI Terminology', {"ui_layout": layout}):
			continue
		frappe.get_doc(
			{
				"doctype": 'YRP YRP UI Terminology',
				"ui_layout": layout,
			}
		).insert(ignore_permissions=True)
