"""Permissions yrp grants on other apps' doctypes, kept as standard DocPerm rows.

yrp's own doctypes keep their permissions in their DocType JSON. Site changes
made in the Role Permission Manager stay in Custom DocPerm.
"""
import frappe

# (doctype, role, permlevel, rights) with if_owner 0.
PERMISSIONS = [
	("Account", "Sales Manager", 0, {"select": 1}),
	("Account", "System Manager", 0, {"read": 1}),
	("Account", "YRP Customer", 0, {"select": 1, "read": 1}),
	("Account", "YRP Sales Partner", 0, {"select": 1, "read": 1}),
	("Account", "YRP Sales Person", 0, {"select": 1, "read": 1}),
	("Address", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Address", "YRP Partner", 0, {"read": 1, "export": 1}),
	("Address", "YRP Partner Manager", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Address", "YRP Retail User", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Address", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Address", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Blanket Order", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Blanket Order", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Blanket Order", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Company", "YRP Customer", 0, {"select": 1, "read": 1}),
	("Company", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Company", "YRP Sales Person", 0, {"select": 1, "read": 1}),
	("Contact", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Contact", "YRP Partner", 0, {"read": 1, "export": 1}),
	("Contact", "YRP Partner Manager", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Contact", "YRP Retail User", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Contact", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Contact", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Contact", "All", 1, {"read": 1, "report": 1}),
	("Cost Center", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Currency", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Customer", "System Manager", 0, {"read": 1}),
	("Customer", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Customer", "YRP Partner", 0, {"read": 1, "export": 1}),
	("Customer", "YRP Partner Manager", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Customer", "YRP Retail User", 0, {"read": 1, "export": 1}),
	("Customer", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Customer", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Customer", "Sales Manager", 1, {"read": 1, "write": 1, "export": 1}),
	("Customer", "System Manager", 1, {"read": 1, "write": 1, "export": 1}),
	("Customer", "Sales Master Manager", 2, {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "share": 1, "print": 1, "email": 1}),
	("Dunning", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Dunning", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Dunning", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("GL Entry", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("GL Entry", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("GL Entry", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Installation Note", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Installation Note", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Installation Note", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Item", "Sales Manager", 0, {"read": 1}),
	("Item", "System Manager", 0, {"read": 1}),
	("Item", "YRP Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Item", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Item", "YRP Sales Person", 0, {"select": 1, "read": 1, "export": 1}),
	("Loyalty Point Entry", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Loyalty Point Entry", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Loyalty Point Entry", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Schedule", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Schedule", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Schedule", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Visit", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Visit", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Maintenance Visit", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Payment Terms Template", "All", 0, {"select": 1, "report": 1, "export": 1, "share": 1, "print": 1, "email": 1}),
	("Payment Terms Template", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Price List", "Sales Manager", 0, {"read": 1}),
	("Price List", "System Manager", 0, {"read": 1}),
	("Price List", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Quotation", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Quotation", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Quotation", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Sales Invoice", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Sales Invoice", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Sales Invoice", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Sales Invoice", "Accounts Manager", 2, {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "share": 1, "print": 1, "email": 1}),
	("Sales Invoice", "Accounts User", 2, {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "share": 1, "print": 1, "email": 1}),
	("Sales Invoice", "System Manager", 2, {"read": 1}),
	("Sales Partner", "YRP Partner", 0, {"read": 1, "export": 1}),
	("Sales Partner", "YRP Partner Manager", 0, {"read": 1, "export": 1}),
	("Sales Partner", "YRP Retail User", 0, {"read": 1, "export": 1}),
	("Sales Partner", "YRP Sales Partner", 0, {"read": 1, "export": 1}),
	("Sales Partner", "Sales Manager", 1, {"read": 1, "write": 1, "export": 1}),
	("Sales Partner", "System Manager", 1, {"read": 1, "write": 1, "export": 1}),
	("Sales Person", "YRP Partner", 0, {"read": 1, "export": 1}),
	("Sales Person", "YRP Partner Manager", 0, {"read": 1, "write": 1, "create": 1, "export": 1}),
	("Sales Person", "YRP Sales Person", 0, {"read": 1, "export": 1}),
	("Sales Taxes and Charges Template", "Sales Manager", 0, {"read": 1}),
	("Sales Taxes and Charges Template", "System Manager", 0, {"read": 1}),
	("Sales Taxes and Charges Template", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Shipment", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Shipment", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Shipment", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Shipping Rule", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Tax Category", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Terms and Conditions", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("UOM", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("UOM", "YRP Sales Person", 0, {"select": 1, "read": 1, "export": 1}),
	("Warehouse", "Sales Manager", 0, {"read": 1}),
	("Warehouse", "System Manager", 0, {"read": 1}),
	("Warehouse", "YRP Sales Partner", 0, {"select": 1, "read": 1, "export": 1}),
	("Warranty Claim", "YRP Customer", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Warranty Claim", "YRP Sales Partner", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
	("Warranty Claim", "YRP Sales Person", 0, {"select": 1, "read": 1, "report": 1, "export": 1, "print": 1}),
]


def ensure_permissions() -> None:
	"""Add missing yrp grants. Runs after migrate because a doctype sync rewrites its DocPerm rows."""
	changed = {
		doctype
		for doctype, role, permlevel, rights in PERMISSIONS
		if ensure_permission(doctype, role, permlevel, rights)
	}
	for doctype in changed:
		frappe.clear_cache(doctype=doctype)

	for doctype in set(frappe.get_all("YRP Partner Type", pluck="reference_doctype")):
		ensure_partner_source_permission(doctype)


def ensure_partner_source_permission(doctype: str) -> None:
	"""Let YRP Partner read a Partner Type source; record rules narrow the rows."""
	if ensure_permission(doctype, "YRP Partner", 0, {"read": 1}):
		frappe.clear_cache(doctype=doctype)


def ensure_permission(doctype: str, role: str, permlevel: int, rights: dict[str, int]) -> bool:
	"""Insert or extend one DocPerm row and return whether it changed.

	The parent DocType is never saved, so developer mode does not rewrite another app's JSON.
	"""
	filters = {
		"parent": doctype,
		"parenttype": "DocType",
		"parentfield": "permissions",
		"role": role,
		"permlevel": permlevel,
		"if_owner": 0,
	}
	name = frappe.db.get_value("DocPerm", filters)
	if not name:
		idx = frappe.db.count("DocPerm", {"parent": doctype}) + 1
		frappe.get_doc({"doctype": "DocPerm", "idx": idx, **filters, **rights}).db_insert()
		return True

	current = frappe.db.get_value("DocPerm", name, list(rights), as_dict=True)
	missing = {right: 1 for right in rights if not current[right]}
	if missing:
		frappe.db.set_value("DocPerm", name, missing, update_modified=False)
	return bool(missing)
