import unittest
from datetime import date

import frappe
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from yrp.stock.dimensions import get_stock_dimensions
from yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance import (
	execute,
	get_columns,
	get_inward_date_breakdown,
	get_items,
)


class TestStockBalanceInwardDates(IntegrationTestCase):
	def test_groups_remaining_fifo_slots_by_date_in_oldest_first_order(self):
		result = get_inward_date_breakdown(
			[
				[5, date(2026, 8, 11)],
				[10, date(2026, 8, 7)],
				[2.5, date(2026, 8, 11)],
			]
		)
		self.assertEqual(
			result,
			[
				{"date": "07-08-2026", "qty": 10.0},
				{"date": "11-08-2026", "qty": 7.5},
			],
		)

	def test_ignores_empty_slots(self):
		self.assertEqual(get_inward_date_breakdown([[0, date(2026, 8, 11)], [3, None]]), [])


class TestStockBalanceFilters(IntegrationTestCase):
	def make_warehouse(self, name, supplier=None):
		company = frappe.get_all("Company", pluck="name", limit=1)[0]
		doc = {"doctype": "Warehouse", "warehouse_name": name, "company": company, "supplier": supplier}
		return frappe.get_doc(doc).insert(ignore_permissions=True).name

	def add_entry(self, item, warehouse, qty):
		frappe.get_doc(
			{
				"doctype": "YRP Stock Ledger Entry",
				"item": item,
				"warehouse": warehouse,
				"posting_date": today(),
				"posting_time": "01:00:00",
				"qty": qty,
				"qty_after_transaction": qty,
				"voucher_type": "YRP Stock Entry",
				"voucher_no": frappe.generate_hash(length=10),
				"is_cancelled": 0,
				"docstatus": 1,
			}
		).db_insert()

	def test_supplier_filter_reads_only_that_suppliers_warehouses(self):
		supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "ZZ Balance Maker"}).insert().name
		own = self.make_warehouse("ZZ Balance Own", supplier)
		other = self.make_warehouse("ZZ Balance Other")
		item = frappe.get_all("Item", filters={"variant_of": ["is", "set"]}, pluck="name", limit=1)[0]
		self.add_entry(item, own, 7)
		self.add_entry(item, other, 5)
		filters = {"from_date": today(), "to_date": today(), "item": [item], "supplier": [supplier]}
		_columns, rows = execute(filters)
		self.assertEqual([(row["warehouse"], row["bal_qty"]) for row in rows], [(own, 7)])

	def test_no_item_filter_reads_every_item_without_an_item_list(self):
		self.assertEqual(get_items(frappe._dict()), [])
		self.assertEqual(get_items(frappe._dict(item="A")), ["A"])


class TestStockBalanceItemPermissions(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.items = _physical_items()
		if len(self.items) < 2:
			self.skipTest("needs two physical stock Items")
		company = frappe.db.get_value("Company", {}, "name")
		if not company:
			self.skipTest("needs one Company")
		self.warehouse = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"Stock Balance Permission {frappe.generate_hash(length=8)}",
				"company": company,
			}
		).insert(ignore_permissions=True).name
		self.dimensions = _dimension_values()
		self.user = _make_stock_user()
		self.ledger_entries = [_insert_ledger(item, self.warehouse, self.dimensions) for item in self.items]
		self.user_permission = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.user,
				"allow": "Item",
				"for_value": self.items[0],
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True).name

	def tearDown(self):
		frappe.set_user("Administrator")
		if getattr(self, "user_permission", None) and frappe.db.exists("User Permission", self.user_permission):
			frappe.delete_doc("User Permission", self.user_permission, ignore_permissions=True, force=True)
		for name in getattr(self, "ledger_entries", []):
			if frappe.db.exists("YRP Stock Ledger Entry", name):
				frappe.delete_doc("YRP Stock Ledger Entry", name, ignore_permissions=True, force=True)
		if getattr(self, "warehouse", None) and frappe.db.exists("Warehouse", self.warehouse):
			frappe.delete_doc("Warehouse", self.warehouse, ignore_permissions=True, force=True)
		if getattr(self, "user", None) and frappe.db.exists("User", self.user):
			frappe.delete_doc("User", self.user, ignore_permissions=True, force=True)

	def test_item_name_is_data_and_report_filters_by_item_permission(self):
		item_name = next(column for column in get_columns({}, get_stock_dimensions()) if column["fieldname"] == "item_name")
		self.assertEqual(item_name["fieldtype"], "Data")

		frappe.set_user(self.user)
		payload = run(
			"YRP Stock Balance",
			filters={
				"from_date": frappe.utils.today(),
				"to_date": frappe.utils.today(),
				"warehouse": self.warehouse,
			},
			ignore_prepared_report=True,
		)
		visible_items = {row.get("item") for row in payload["result"]}
		self.assertIn(self.items[0], visible_items)
		self.assertNotIn(self.items[1], visible_items)


def _make_stock_user():
	email = f"stock-balance-{frappe.generate_hash(length=8)}@example.test"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Stock Balance Permission Test",
			"enabled": 1,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	user.add_roles("Stock User")
	return email


def _physical_items():
	return [
		row[0]
		for row in frappe.db.sql(
			"""select name from `tabItem`
			where is_stock_item = 1 and (coalesce(variant_of, '') != '' or coalesce(has_variants, 0) = 0)
			order by name limit 2"""
		)
	]


def _dimension_values():
	values = {}
	for dimension in get_stock_dimensions():
		value = frappe.db.get_value(dimension["dimension_doctype"], {}, "name")
		if dimension.get("mandatory") and not value:
			raise unittest.SkipTest(f"needs one {dimension['dimension_doctype']}")
		values[dimension["fieldname"]] = value
	return values


def _insert_ledger(item, warehouse, dimensions):
	doc = frappe.get_doc(
		{
			"doctype": "YRP Stock Ledger Entry",
			"item": item,
			"warehouse": warehouse,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"voucher_type": "Item",
			"voucher_no": item,
			"qty": 7,
			"qty_after_transaction": 7,
			"rate": 10,
			"valuation_rate": 10,
			"stock_value": 70,
			"stock_value_difference": 70,
			**dimensions,
		}
	).insert(ignore_permissions=True, ignore_links=True)
	return doc.name
