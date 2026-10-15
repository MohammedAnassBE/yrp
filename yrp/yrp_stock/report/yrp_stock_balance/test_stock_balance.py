from datetime import date

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance import (
	execute,
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
