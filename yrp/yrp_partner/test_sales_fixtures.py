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
			import_file_by_path(frappe.get_app_path("yrp", "fixtures", "00_role.json"),
				data_import=True, force=True, reset_permissions=True)
		for role in ("YRP Sales Person", "YRP Sales Partner"):
			self.assertEqual(frappe.db.get_value("Role", role, "desk_access"), 1)
		self.assertEqual(roles_before, frappe.get_all("Has Role", fields=["name", "parent", "role"], order_by="name"))
		self.assertEqual(users_before, frappe.get_all("User", fields=["name", "modified", "enabled", "user_type"], order_by="name"))

	def test_static_configuration_is_exported_and_not_recreated_after_migrate(self):
		entries = frappe.get_hooks("fixtures", app_name="yrp")
		self.assertTrue({"Role", "Custom Field"} <= {row['dt'] for row in entries})
		self.assertNotIn("Custom DocPerm", {row['dt'] for row in entries})
		for method in frappe.get_hooks("after_migrate", app_name="yrp"):
			self.assertNotIn(method.rsplit('.', 1)[-1], {
				"setup_partner_role", "setup_sales_roles", "setup_contact_support", "setup_retail", "setup_sales_flow", "setup_item_sales", "setup_partner_workspace",
			})
		rows = json.loads(Path(frappe.get_app_path("yrp", "fixtures", "custom_field.json")).read_text())
		self.assertTrue({"YRP Partner", "YRP Retail"} <= {row.get('module') for row in rows})
		self.assertTrue(all(row['doctype'] == 'Custom Field' for row in rows))
