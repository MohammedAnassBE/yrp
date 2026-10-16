import json

import frappe
from frappe.tests import IntegrationTestCase

from yrp.permissions import (
	PERMISSIONS,
	ensure_partner_source_permission,
	ensure_permission,
	ensure_permissions,
)


def get_docperm(doctype, role, permlevel=0):
	return frappe.get_all("DocPerm", {"parent": doctype, "role": role, "permlevel": permlevel, "if_owner": 0}, ["*"])


def get_standard_permissions(doctype):
	module = frappe.get_meta(doctype).module
	path = frappe.get_module_path(module, "doctype", frappe.scrub(doctype), frappe.scrub(doctype) + ".json")
	with open(path) as file:
		return json.load(file)["permissions"]


class TestPermissions(IntegrationTestCase):
	def setUp(self):
		savepoint = "yrp_permissions_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(savepoint)
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.rollback, save_point=savepoint)
		self.role = frappe.get_doc({"doctype": "Role", "role_name": "_Test YRP Grant " + frappe.generate_hash(length=6)}).insert().name

	def test_missing_row_is_inserted_as_standard_docperm(self):
		self.assertTrue(ensure_permission("Dunning", self.role, 0, {"read": 1, "select": 1}))

		(row,) = get_docperm("Dunning", self.role)
		self.assertEqual((row.parenttype, row.parentfield, row.read, row.select, row.write), ("DocType", "permissions", 1, 1, 0))
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "Dunning", "role": self.role}))

	def test_missing_right_is_added_without_removing_others(self):
		ensure_permission("Dunning", self.role, 0, {"read": 1, "print": 1})

		self.assertTrue(ensure_permission("Dunning", self.role, 0, {"read": 1, "export": 1}))

		(row,) = get_docperm("Dunning", self.role)
		self.assertEqual((row.read, row.print, row.export), (1, 1, 1))

	def test_repeated_run_changes_nothing(self):
		self.assertTrue(ensure_permission("Dunning", self.role, 0, {"read": 1}))
		self.assertFalse(ensure_permission("Dunning", self.role, 0, {"read": 1}))
		self.assertEqual(len(get_docperm("Dunning", self.role)), 1)

		self.add_custom_permission("Dunning", self.role, read=1)
		ensure_permissions()
		before = frappe.get_all("DocPerm", fields=["name", "modified", "read", "select", "export"], order_by="name")
		custom_before = frappe.get_all("Custom DocPerm", fields=["*"], order_by="name")
		ensure_permissions()
		self.assertEqual(before, frappe.get_all("DocPerm", fields=["name", "modified", "read", "select", "export"], order_by="name"))
		self.assertEqual(custom_before, frappe.get_all("Custom DocPerm", fields=["*"], order_by="name"))

	def add_custom_permission(self, doctype, role, **rights):
		return frappe.get_doc({
			"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType",
			"parentfield": "permissions", "role": role, "permlevel": 0, "if_owner": 0, **rights,
		}).db_insert()

	def test_custom_permissions_get_exact_yrp_grants_and_keep_other_roles(self):
		self.add_custom_permission("Dunning", self.role, read=1, write=1)
		self.add_custom_permission("Dunning", "YRP Customer", write=1, create=1, mask=1)
		before = frappe.get_all("Custom DocPerm", {"role": ["not like", "YRP %"]}, ["*"], order_by="name")
		# Prime the effective permissions cache before reconciliation.
		frappe.clear_cache(doctype="Dunning")
		frappe.get_meta("Dunning")

		ensure_permissions()

		self.assertEqual(before, frappe.get_all("Custom DocPerm", {"role": ["not like", "YRP %"]}, ["*"], order_by="name"))
		effective = frappe.get_meta("Dunning").permissions
		checks = [field.fieldname for field in frappe.get_meta("Custom DocPerm").fields
			if field.fieldtype == "Check" and field.fieldname != "if_owner"]
		for doctype, role, permlevel, rights in PERMISSIONS:
			if doctype != "Dunning" or not role.startswith("YRP "):
				continue
			rows = [row for row in effective if (row.role, row.permlevel, row.if_owner) == (role, permlevel, 0)]
			self.assertTrue(rows, role)
			for row in rows:
				self.assertEqual({right: row.get(right) for right in checks}, {right: rights.get(right, 0) for right in checks})

	def test_partner_source_keeps_full_declared_grant(self):
		self.add_custom_permission("Customer", self.role, read=1)
		ensure_permissions()

		ensure_partner_source_permission("Customer")

		rows = frappe.get_all("Custom DocPerm", {"parent": "Customer", "role": "YRP Partner", "permlevel": 0, "if_owner": 0}, ["read", "export"])
		self.assertTrue(rows)
		self.assertTrue(all(row.read == row.export == 1 for row in rows))

	def test_fresh_site_gets_yrp_grants_and_keeps_standard_permissions(self):
		doctypes = ["Customer", "GL Entry"]
		frappe.db.delete("Custom DocPerm", {"parent": ["in", doctypes]})
		ensure_permissions()

		for doctype in doctypes:
			self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": doctype}))
			frappe.clear_cache(doctype=doctype)
			effective = {(row.role, row.permlevel, row.if_owner): row for row in frappe.get_meta(doctype).permissions}
			for standard in get_standard_permissions(doctype):
				row = effective[(standard["role"], standard.get("permlevel", 0), standard.get("if_owner", 0))]
				for right in ("read", "write", "create", "delete", "submit"):
					self.assertGreaterEqual(row.get(right) or 0, standard.get(right, 0), (doctype, standard["role"], right))
			for grant_doctype, role, permlevel, rights in PERMISSIONS:
				if grant_doctype == doctype:
					row = effective[(role, permlevel, 0)]
					self.assertEqual({right: row.get(right) for right in rights}, rights, (doctype, role))

	def test_yrp_create_grants_include_write(self):
		grants = [(doctype, role, rights) for doctype, role, _, rights in PERMISSIONS]
		for doctype in ("YRP Retail Order", "YRP Retailer", "YRP Visit", "YRP Customer Stock"):
			grants += [(doctype, row["role"], row) for row in get_standard_permissions(doctype)]
		for doctype, role, rights in grants:
			if role.startswith("YRP ") and rights.get("create"):
				self.assertTrue(rights.get("write"), (doctype, role))
