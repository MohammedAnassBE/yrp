import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests import IntegrationTestCase

from yrp.api import web_reports as api
from yrp.web_reports.fields import EXCLUDED_FIELDTYPES, report_field_policy
from yrp.web_reports.filters import authorize_filters
from yrp.web_reports.layouts import (
	LAYOUT_DOCTYPE,
	get_report_layout,
	reset_report_layout,
	save_report_layout,
)
from yrp.web_reports.link_columns import get_link_field_options, get_linked_values
from yrp.web_reports.registry import clear_web_report_cache, get_web_reports
from yrp.web_reports.test_support import ReportAccessTestCase, report_access_entries


class TestLinkColumns(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = [_make_user("layout-a"), _make_user("layout-b")]
		cls.ai_bot_user = _make_user("layout-ai-bot", roles=("AI Bot",))
		cls.items = frappe.get_all("Item", pluck="name", order_by="name", limit=2)
		cls.restricted_fields = _create_restricted_fields()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for fieldname in cls.restricted_fields:
			name = f"Item-{fieldname}"
			if frappe.db.exists("Custom Field", name):
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		for user in [*cls.users, cls.ai_bot_user]:
			for name in frappe.get_all(LAYOUT_DOCTYPE, filters={"user": user}, pluck="name"):
				frappe.delete_doc(LAYOUT_DOCTYPE, name, ignore_permissions=True, force=True)
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, ignore_permissions=True, force=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		clear_web_report_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		for user in [*self.users, self.ai_bot_user]:
			for name in frappe.get_all(LAYOUT_DOCTYPE, filters={"user": user}, pluck="name"):
				frappe.delete_doc(LAYOUT_DOCTYPE, name, ignore_permissions=True, force=True)

	def test_field_policy_excludes_sensitive_and_non_value_fields(self):
		fields = report_field_policy("User")
		self.assertFalse({field["fieldtype"] for field in fields} & EXCLUDED_FIELDTYPES)
		self.assertNotIn("new_password", {field["fieldname"] for field in fields})

	def test_forged_source_and_target_are_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown linked source"):
			get_link_field_options("stock-balance", "item_name")
		with self.assertRaisesRegex(frappe.ValidationError, "not available"):
			get_linked_values(
				"stock-balance",
				[{"source_column": "item", "fields": ["not_a_field"], "names": []}],
			)

	def test_restricted_fields_are_excluded_from_options_hydration_and_layouts(self):
		frappe.set_user(self.users[0])
		options = {field["fieldname"] for field in get_link_field_options("stock-balance", "item")}
		self.assertFalse(set(self.restricted_fields) & options)

		for fieldname in self.restricted_fields:
			with self.subTest(fieldname=fieldname, operation="hydrate"):
				with self.assertRaisesRegex(frappe.ValidationError, "not available"):
					get_linked_values(
						"stock-balance",
						[{"source_column": "item", "fields": [fieldname], "names": self.items[:1]}],
					)
			with self.subTest(fieldname=fieldname, operation="save"):
				with self.assertRaisesRegex(frappe.ValidationError, "not available"):
					save_report_layout("stock-balance", [_linked_column("item", fieldname)])

	def test_more_than_five_thousand_names_is_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "at most 5000 names"):
			get_linked_values(
				"stock-balance",
				[
					{
						"source_column": "item",
						"fields": ["item_name"],
						"names": [f"ITEM-{index}" for index in range(5_001)],
					}
				],
			)

	def test_shared_target_sources_have_separate_namespaces(self):
		if not self.items:
			self.skipTest("needs one Item")
		item = self.items[0]
		result = get_linked_values(
			"purchase-order-itemwise",
			[
				{"source_column": "item", "fields": ["item_name"], "names": [item]},
				{"source_column": "item_variant", "fields": ["item_name"], "names": [item]},
			],
		)
		self.assertEqual(set(result), {"item", "item_variant"})
		self.assertIn(item, result["item"])
		self.assertIn(item, result["item_variant"])

	def test_user_permissions_omit_unreadable_names_and_filters(self):
		if len(self.items) < 2:
			self.skipTest("needs two Items")
		permission = _add_item_permission(self.users[0], self.items[0])
		self.addCleanup(_delete_permission, permission)
		frappe.set_user(self.users[0])
		result = get_linked_values(
			"stock-balance",
			[
				{
					"source_column": "item",
					"fields": ["item_name"],
					"names": self.items,
				}
			],
		)
		self.assertEqual(set(result["item"]), {self.items[0]})
		with self.assertRaises(frappe.PermissionError):
			authorize_filters(get_web_reports()["stock-balance"].filters, {"item": self.items[1]})

	def test_user_without_target_read_permission_is_rejected(self):
		frappe.set_user("Administrator")
		frappe.get_doc("User", self.users[1]).remove_roles("Stock User")
		frappe.clear_cache(user=self.users[1])
		frappe.set_user(self.users[1])
		with self.assertRaises(frappe.PermissionError):
			get_link_field_options("stock-balance", "item")
		frappe.set_user("Administrator")
		frappe.get_doc("User", self.users[1]).add_roles("Stock User")

	def test_layout_is_per_user_and_reset_is_scoped(self):
		frappe.set_user(self.users[0])
		save_report_layout("stock-balance", [_base_column("item", width=140)])
		frappe.set_user(self.users[1])
		self.assertIsNone(get_report_layout("stock-balance")["layout"])
		save_report_layout("stock-balance", [_base_column("warehouse")])
		reset_report_layout("stock-balance")
		self.assertIsNone(get_report_layout("stock-balance")["layout"])
		frappe.set_user(self.users[0])
		self.assertEqual(get_report_layout("stock-balance")["layout"][0].column_key, "base:item")

	def test_layout_validation_enforces_keys_limits_width_and_alignment(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown base"):
			save_report_layout("stock-balance", [_base_column("forged")])
		with self.assertRaisesRegex(frappe.ValidationError, "width"):
			save_report_layout("stock-balance", [_base_column("item", width=59)])
		with self.assertRaisesRegex(frappe.ValidationError, "alignment"):
			save_report_layout("stock-balance", [_base_column("item", align="justify")])
		duplicate = [_base_column("item"), _base_column("item")]
		with self.assertRaisesRegex(frappe.ValidationError, "Duplicate"):
			save_report_layout("stock-balance", duplicate)
		fields = [field["fieldname"] for field in report_field_policy("Item")][:21]
		if len(fields) == 21:
			columns = [_linked_column("item", fieldname) for fieldname in fields]
			with self.assertRaisesRegex(frappe.ValidationError, "at most 20 linked"):
				save_report_layout("stock-balance", columns)

	def test_non_system_manager_has_no_direct_doctype_access(self):
		frappe.set_user(self.users[0])
		save_report_layout("stock-balance", [_base_column("item")])
		name = frappe.db.get_value(LAYOUT_DOCTYPE, {"user": self.users[0]}, "name")
		self.assertFalse(frappe.has_permission(LAYOUT_DOCTYPE, "read"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(LAYOUT_DOCTYPE, name).check_permission("read")

	def test_ai_bot_can_use_layout_facade_but_not_the_layout_doctype(self):
		frappe.set_user(self.ai_bot_user)
		save_report_layout("stock-balance", [_base_column("item")])
		name = frappe.db.get_value(LAYOUT_DOCTYPE, {"user": self.ai_bot_user}, "name")
		layout = frappe.get_doc(LAYOUT_DOCTYPE, name)

		self.assertEqual(get_report_layout("stock-balance")["layout"][0].column_key, "base:item")
		self.assertEqual(frappe.get_list(LAYOUT_DOCTYPE, pluck="name"), [])
		self.assertFalse(layout.has_permission("read", user=self.ai_bot_user))


class TestLinkReportAccess(ReportAccessTestCase):
	def test_report_access_is_required_before_link_validation(self):
		with report_access_entries() as fixture:
			frappe.set_user(fixture.denied_user)
			self.assertTrue(frappe.has_permission("Item", "read"))
			for kind, entry in fixture.entries.items():
				for source in ("item", "forged"):
					with self.subTest(kind=kind, endpoint="options", source=source):
						with self.assertRaises(frappe.PermissionError):
							api.get_link_field_options(entry.key, source)
				valid_requests = [
					{"source_column": "item", "fields": ["item_name"], "names": [fixture.item.name]}
				]
				for requests in ([], "invalid json", valid_requests):
					with self.subTest(kind=kind, endpoint="values", requests=requests):
						with self.assertRaises(frappe.PermissionError):
							api.get_linked_values(entry.key, requests)

	def test_authorized_link_options_and_values_are_unchanged(self):
		with report_access_entries() as fixture:
			frappe.set_user(self.permitted_user)
			requests = [{"source_column": "item", "fields": ["item_name"], "names": [fixture.item.name]}]
			for kind, entry in fixture.entries.items():
				with self.subTest(kind=kind):
					self.assertEqual(api.get_link_field_options(entry.key, "item"), report_field_policy("Item"))
					self.assertEqual(
						api.get_linked_values(entry.key, requests),
						{"item": {fixture.item.name: {"item_name": fixture.item.item_name}}},
					)


def _make_user(prefix, roles=("Stock User",)):
	email = f"{prefix}-{frappe.generate_hash(length=8)}@example.test"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Web Report Test",
			"enabled": 1,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	if roles:
		user.add_roles(*roles)
	return email


def _add_item_permission(user, item):
	frappe.set_user("Administrator")
	return frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Item",
			"for_value": item,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True).name


def _create_restricted_fields():
	prefix = f"web_report_{frappe.generate_hash(length=8).lower()}"
	specifications = {
		f"{prefix}_password": {"fieldtype": "Password"},
		f"{prefix}_masked": {"fieldtype": "Data", "mask": 1},
		f"{prefix}_permlevel": {"fieldtype": "Data", "permlevel": 9},
		f"{prefix}_hidden": {"fieldtype": "Data", "hidden": 1},
		f"{prefix}_virtual": {"fieldtype": "Data", "is_virtual": 1},
	}
	for fieldname, values in specifications.items():
		create_custom_field(
			"Item",
			{
				"fieldname": fieldname,
				"label": fieldname.replace("_", " ").title(),
				"insert_after": "item_name",
				**values,
			},
		)
	frappe.clear_cache(doctype="Item")
	return tuple(specifications)


def _delete_permission(name):
	frappe.set_user("Administrator")
	if frappe.db.exists("User Permission", name):
		frappe.delete_doc("User Permission", name, ignore_permissions=True, force=True)


def _base_column(fieldname, *, width=100, align="auto"):
	return {
		"column_key": f"base:{fieldname}",
		"kind": "base",
		"visible": 1,
		"align": align,
		"width": width,
	}


def _linked_column(source, target):
	return {
		"column_key": f"linked:{source}:{target}",
		"kind": "linked",
		"source_column": source,
		"target_field": target,
		"visible": 1,
		"align": "auto",
		"width": 100,
	}
