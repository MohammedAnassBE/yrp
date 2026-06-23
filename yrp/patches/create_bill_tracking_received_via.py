import frappe


DEFAULT_RECEIVED_VIA = ("HO", "Post", "Email", "Warehouse", "Others")


def execute():
	if not frappe.db.exists("DocType", "Bill Tracking Received Via"):
		return

	for value in DEFAULT_RECEIVED_VIA:
		if frappe.db.exists("Bill Tracking Received Via", value):
			continue
		frappe.get_doc({
			"doctype": "Bill Tracking Received Via",
			"received_via": value,
		}).insert(ignore_permissions=True)
