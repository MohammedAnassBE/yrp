import json

import frappe
from frappe import _
from frappe.model.document import Document


class UserListview(Document):
	def validate(self):
		# A user may only manage their own listview preferences.
		if frappe.session.user != "Administrator" and self.user != frappe.session.user:
			frappe.throw(_("You can only configure your own listview preferences."))


@frappe.whitelist()
def get_user_listview(doctype_name):
	"""Current user's saved columns for `doctype_name` (ordered by idx), or None."""
	user = frappe.session.user
	name = frappe.db.get_value(
		"User Listview", {"user": user, "doctype_name": doctype_name}, "name"
	)
	if not name:
		return None
	return frappe.get_all(
		"User Listview Field",
		filters={"parent": name},
		fields=["fieldname", "fieldtype", "label", "enabled"],
		order_by="idx asc",
	)


@frappe.whitelist()
def save_user_listview(doctype_name, columns):
	"""Create/replace the current user's columns for `doctype_name`.

	`columns` is an ordered JSON list of {fieldname, enabled}; list order is the
	column display order (persisted as the child-row idx). Only fields that still
	exist on the target DocType's meta are kept.
	"""
	if isinstance(columns, str):
		columns = json.loads(columns)

	user = frappe.session.user
	meta = frappe.get_meta(doctype_name)
	labels = {df.fieldname: (df.label or df.fieldname) for df in meta.fields}
	types = {df.fieldname: df.fieldtype for df in meta.fields}

	name = frappe.db.get_value(
		"User Listview", {"user": user, "doctype_name": doctype_name}, "name"
	)
	if name:
		doc = frappe.get_doc("User Listview", name)
	else:
		doc = frappe.new_doc("User Listview")
		doc.user = user
		doc.doctype_name = doctype_name

	doc.set("fields", [])
	for col in columns:
		fieldname = (col or {}).get("fieldname")
		if not fieldname or fieldname not in labels:
			continue
		doc.append(
			"fields",
			{
				"fieldname": fieldname,
				"fieldtype": types.get(fieldname, ""),
				"label": labels.get(fieldname, fieldname),
				"enabled": 1 if (col or {}).get("enabled") else 0,
			},
		)
	doc.save(ignore_permissions=True)
	return "ok"


@frappe.whitelist()
def reset_user_listview(doctype_name):
	"""Delete the current user's saved columns for `doctype_name` (revert to default)."""
	user = frappe.session.user
	name = frappe.db.get_value(
		"User Listview", {"user": user, "doctype_name": doctype_name}, "name"
	)
	if name:
		frappe.delete_doc("User Listview", name, ignore_permissions=True, force=True)
	return "ok"
