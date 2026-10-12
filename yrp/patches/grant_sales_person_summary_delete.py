import frappe


def execute():
	"""Sites with Custom DocPerm on YRP Retail Order Summary miss the new DocType JSON grant: a Sales
	Person may delete a draft summary."""
	frappe.db.set_value(
		"Custom DocPerm",
		{"parent": "YRP Retail Order Summary", "role": "YRP Sales Person", "permlevel": 0, "if_owner": 0},
		"delete",
		1,
		update_modified=False,
	)
	frappe.clear_cache(doctype="YRP Retail Order Summary")
