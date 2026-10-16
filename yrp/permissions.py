"""Standard grants, with YRP role grants mirrored into existing custom permission sets."""
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
	rights = {"read": 1}
	for grant_doctype, role, permlevel, grant_rights in PERMISSIONS:
		if (grant_doctype, role, permlevel) == (doctype, "YRP Partner", 0):
			rights.update(grant_rights)
	if ensure_permission(doctype, "YRP Partner", 0, rights):
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
	custom_changed = False
	if role.startswith("YRP ") and frappe.db.exists("Custom DocPerm", {"parent": doctype}):
		custom_changed = _ensure_custom_permission(filters, rights)
	name = frappe.db.get_value("DocPerm", filters)
	if not name:
		idx = frappe.db.count("DocPerm", {"parent": doctype}) + 1
		frappe.get_doc({"doctype": "DocPerm", "idx": idx, **filters, **rights}).db_insert()
		return True

	current = frappe.db.get_value("DocPerm", name, list(rights), as_dict=True)
	missing = {right: 1 for right in rights if not current[right]}
	if missing:
		frappe.db.set_value("DocPerm", name, missing, update_modified=False)
	return bool(missing) or custom_changed


def _ensure_custom_permission(filters: dict, rights: dict[str, int]) -> bool:
	"""Reconcile only the matching YRP rows, including clearing unrequested rights."""
	# Custom DocPerm is a regular DocType, unlike the standard DocPerm child table.
	filters = {key: filters[key] for key in ("parent", "role", "permlevel", "if_owner")}
	values = {
		field.fieldname: rights.get(field.fieldname, 0)
		for field in frappe.get_meta("Custom DocPerm").fields
		if field.fieldtype == "Check" and field.fieldname != "if_owner"
	}
	rows = frappe.get_all("Custom DocPerm", filters, ["name", *values])
	if not rows:
		idx = frappe.db.count("Custom DocPerm", {"parent": filters["parent"]}) + 1
		frappe.get_doc({"doctype": "Custom DocPerm", "idx": idx, **filters, **values}).db_insert()
		return True

	changed = False
	for row in rows:
		updates = {right: value for right, value in values.items() if row[right] != value}
		if updates:
			frappe.db.set_value("Custom DocPerm", row.name, updates, update_modified=False)
			changed = True
	return changed
