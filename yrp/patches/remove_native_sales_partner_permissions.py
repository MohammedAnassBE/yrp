import frappe

PARTNER_ROLES = ["YRP Customer", "YRP Sales Partner", "YRP Sales Person"]


def execute():
	"""Partners moved to YRP sales documents; drop their retired native Sales Order, Delivery Note and Packing Slip grants."""
	names = frappe.db.get_all(
		"Custom DocPerm",
		filters={
			"parent": ["in", ["Sales Order", "Delivery Note", "Packing Slip"]],
			"role": ["in", PARTNER_ROLES],
		},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
