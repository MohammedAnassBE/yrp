"""Portable static configuration, imported with Frappe's native fixture path."""
import json
import unittest
from pathlib import Path

import frappe
from frappe.modules.import_file import import_file_by_path


class TestSalesFixtures(unittest.TestCase):
	def setUp(self):
		self.point = "sales_fixtures_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		# Fixture imports populate metadata caches with transactional policy.
		# Drop those caches after restoring the original database permissions.
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")

	def test_native_role_and_permission_fixture_import_is_repeatable_without_user_changes(self):
		roles_before = frappe.get_all("Has Role", fields=["name", "parent", "role"], order_by="name")
		users_before = frappe.get_all("User", fields=["name", "modified", "enabled", "user_type"], order_by="name")
		for _ in range(2):
			for filename in ("00_role.json", "custom_docperm.json"):
				import_file_by_path(frappe.get_app_path("yrp", "fixtures", filename),
					data_import=True, force=True, reset_permissions=True)
		for role in ("YRP Sales Person", "YRP Sales Partner"):
			self.assertEqual(frappe.db.get_value("Role", role, "desk_access"), 1)
		self.assertEqual(roles_before, frappe.get_all("Has Role", fields=["name", "parent", "role"], order_by="name"))
		self.assertEqual(users_before, frappe.get_all("User", fields=["name", "modified", "enabled", "user_type"], order_by="name"))

	def test_fresh_permission_sets_keep_all_native_roles_and_exclude_site_policy(self):
		path = frappe.get_app_path("yrp", "fixtures", "custom_docperm.json")
		rows = json.loads(Path(path).read_text())
		parents = {row['parent'] for row in rows}
		rights = [df.fieldname for df in frappe.get_meta("Custom DocPerm").fields if df.fieldtype == "Check"]
		def key(row):
			return row.get('parent'), row.get('role'), row.get('permlevel', 0), row.get('if_owner', 0)
		baseline = {}
		for parent in parents:
			slug = frappe.scrub(parent)
			path = Path(frappe.get_module_path(frappe.get_meta(parent).module, 'doctype', slug, slug + '.json'))
			for row in json.loads(path.read_text()).get('permissions', []):
				if not row['role'].startswith('YRP '):
					row['parent'] = parent
					baseline[key(row)] = row
		exported = {key(row): row for row in rows if not row['role'].startswith("YRP ")}
		self.assertEqual(set(baseline), set(exported))
		for identity, expected in baseline.items():
			self.assertEqual({field: expected.get(field) or 0 for field in rights},
				{field: exported[identity].get(field) or 0 for field in rights}, identity)
		# Simulate a fresh site's absent Custom DocPerm rows, not its business data.
		frappe.db.delete("Custom DocPerm", {"parent": ["in", list(parents)]})
		import_file_by_path(frappe.get_app_path("yrp", "fixtures", "custom_docperm.json"),
			data_import=True, force=True, reset_permissions=True)
		for parent in parents:
			frappe.clear_cache(doctype=parent)
			actual = {key(row) for row in frappe.get_meta(parent).permissions}
			self.assertTrue({identity for identity in baseline if identity[0] == parent} <= actual)

	def test_static_configuration_is_exported_and_not_recreated_after_migrate(self):
		entries = frappe.get_hooks("fixtures", app_name="yrp")
		self.assertTrue({"Role", "Custom DocPerm", "Custom Field"} <= {row['dt'] for row in entries})
		for method in frappe.get_hooks("after_migrate", app_name="yrp"):
			self.assertNotIn(method.rsplit('.', 1)[-1], {
				"setup_partner_role", "setup_sales_roles", "setup_contact_support", "setup_retail", "setup_sales_flow", "setup_item_sales", "setup_partner_workspace",
			})
		rows = json.loads(Path(frappe.get_app_path("yrp", "fixtures", "custom_field.json")).read_text())
		self.assertTrue({"YRP Partner", "YRP Retail"} <= {row.get('module') for row in rows})
		self.assertTrue(all(row['doctype'] == 'Custom Field' for row in rows))

	def test_yrp_operational_create_grants_include_editing(self):
		rows = json.loads(Path(frappe.get_app_path("yrp", "fixtures", "custom_docperm.json")).read_text())
		for row in rows:
			if row['role'].startswith('YRP ') and row.get('create'):
				with self.subTest(doctype=row['parent'], role=row['role']):
					self.assertTrue(row.get('write'), "Operational records must remain editable after creation.")
