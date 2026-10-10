import json

import frappe
from frappe.tests import IntegrationTestCase

from yrp.permissions import PERMISSIONS, ensure_permission, ensure_permissions


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

		ensure_permissions()
		before = frappe.get_all("DocPerm", fields=["name", "modified", "read", "select", "export"], order_by="name")
		ensure_permissions()
		self.assertEqual(before, frappe.get_all("DocPerm", fields=["name", "modified", "read", "select", "export"], order_by="name"))

	def test_fresh_site_gets_yrp_grants_and_keeps_standard_permissions(self):
		doctypes = ["Customer", "GL Entry"]
		frappe.db.delete("Custom DocPerm", {"parent": ["in", doctypes]})
		ensure_permissions()

		for doctype in doctypes:
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
