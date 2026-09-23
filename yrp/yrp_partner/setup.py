"""Idempotent install/migrate setup for managed logins, Contact UI, and roles."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup_partner_users():
	"""Track account ownership so synchronization never disables unrelated users."""
	create_custom_fields({"User": [
		{
			"fieldname": fieldname, "label": label, "fieldtype": "Check",
			"default": "0", "hidden": 1, "read_only": 1, "module": "YRP Partner",
		}
		for fieldname, label in (
			("yrp_partner_managed", "Created by YRP Partner"),
			("yrp_partner_generated_email", "Generated Partner Email"),
			("yrp_partner_auto_disabled", "Disabled by Partner Synchronization"),
		)
	]})
	if not frappe.db.exists("Role", "YRP Partner"):
		frappe.get_doc({"doctype": "Role", "role_name": "YRP Partner", "desk_access": 0}).insert(ignore_permissions=True)


def setup_contact_support():
	"""Add native Contact panels without modifying ERPNext source or HR permissions."""
	create_custom_fields({doctype: [
		{"fieldname": "yrp_contacts_section", "label": "Contacts", "fieldtype": "Section Break", "module": "YRP Partner"},
		{"fieldname": "contact_html", "label": "Contacts", "fieldtype": "HTML", "insert_after": "yrp_contacts_section", "module": "YRP Partner"},
	] for doctype in ("Sales Person", "Employee") if not frappe.get_meta(doctype).has_field("contact_html")})
	for role in ("YRP Partner Manager", "YRP Retail User"):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
	from frappe.permissions import add_permission, update_permission_property

	for role in ("YRP Partner Manager", "YRP Retail User"):
		if not frappe.db.exists("Custom DocPerm", {"parent": "Contact", "role": role}):
			add_permission("Contact", role)
			for ptype in ("create", "write"):
				update_permission_property("Contact", role, 0, ptype, 1)

		for doctype in ("Customer", "Sales Partner"):
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
				add_permission(doctype, role)

	create_custom_fields({doctype: [
		{"fieldname": "yrp_addresses_section", "label": "Addresses", "fieldtype": "Section Break", "module": "YRP Partner"},
		{"fieldname": "address_html", "label": "Addresses", "fieldtype": "HTML", "insert_after": "yrp_addresses_section", "module": "YRP Partner"},
	] for doctype in ("Sales Person", "Employee") if not frappe.get_meta(doctype).has_field("address_html")})
	for role, doctypes in {
		"YRP Partner Manager": ("Sales Person", "Customer", "Address"),
		"YRP Retail User": ("Address",),
	}.items():
		for doctype in doctypes:
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
				add_permission(doctype, role)
			for ptype in ("create", "write"):
				update_permission_property(doctype, role, 0, ptype, 1)
	for doctype in ("Sales Partner", "Sales Person", "Customer", "YRP Retailer", "Contact", "Address", "YRP Partner"):
		if frappe.db.exists("DocType", doctype):
			ensure_partner_read_permission(doctype)


def ensure_partner_read_permission(doctype):
	"""Grant only base read; query/document hooks narrow records and deny writes."""
	from frappe.permissions import add_permission

	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": "YRP Partner"}):
		add_permission(doctype, "YRP Partner")
