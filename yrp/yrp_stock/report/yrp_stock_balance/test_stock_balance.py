import unittest
from datetime import date
from itertools import product, repeat
from unittest.mock import patch

import frappe
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from yrp.stock.dimensions import get_stock_dimensions
from yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance import (
	execute,
	get_columns,
	get_inward_date_breakdown,
	get_item_warehouse_map,
	get_items,
	get_stock_ledger_entries,
	parse_list_filter,
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
				"posting_datetime": f"{today()} 01:00:00",
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
		self.assertIsNone(get_items(frappe._dict()))
		self.assertEqual(get_items(frappe._dict(item="A")), ["A"])

	def make_item(self, code, parent=None, template=False, attributes=False):
		name = f"BAL-{self.fixture_key}-{code}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"name": name,
				"item_code": name,
				"item_name": f"Name {code}",
				"item_group": frappe.db.get_value("Item Group", {}, "name"),
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"has_variants": int(template),
				"variant_of": parent,
			}
		).db_insert()
		if attributes:
			frappe.get_doc(
				{
					"doctype": "Item Variant Attribute",
					"parent": name,
					"parenttype": "Item",
					"parentfield": "attributes",
					"attribute": "Size",
					"attribute_value": "M",
				}
			).db_insert()
		return name

	def test_item_and_variant_selections_never_broaden_an_empty_match(self):
		self.fixture_key = frappe.generate_hash(length=8)
		template = self.make_item("T", template=True, attributes=True)
		variant_a = self.make_item("V1", parent=template, attributes=True)
		variant_b = self.make_item("V2", parent=template, attributes=True)
		standalone = self.make_item("S")
		empty = self.make_item("EMPTY", template=True, attributes=True)
		warehouse = self.make_warehouse(f"Balance Items {self.fixture_key}")
		for item, qty in ((variant_a, 3), (variant_b, 5), (standalone, 7)):
			self.add_entry(item, warehouse, qty)
		cases = [
			({"parent_item": template}, {variant_a, variant_b}),
			({"parent_item": [template, standalone]}, {variant_a, variant_b, standalone}),
			({"parent_item": f'["{standalone}"]'}, {standalone}),
			({"item": [variant_a, variant_b]}, {variant_a, variant_b}),
			({"parent_item": [template], "item": [variant_b]}, {variant_b}),
			({"parent_item": standalone, "item": variant_a}, set()),
			({"parent_item": empty}, set()),
			({"parent_item": "NO-SUCH-BALANCE-ITEM"}, set()),
			({"item": "NO-SUCH-BALANCE-ITEM"}, set()),
		]
		for extra, expected in cases:
			with self.subTest(extra=extra):
				_, rows = execute({"from_date": today(), "to_date": today(), "warehouse": warehouse, **extra})
				self.assertEqual({row["item"] for row in rows}, expected)
		_, rows = execute({"from_date": today(), "to_date": today(), "warehouse": warehouse})
		by_item = {row["item"]: row for row in rows}
		self.assertEqual(
			(by_item[variant_a]["parent_item"], by_item[variant_a]["parent_item_name"]), (template, "Name T")
		)
		self.assertEqual(
			(by_item[standalone]["parent_item"], by_item[standalone]["parent_item_name"]),
			(standalone, "Name S"),
		)

	def test_item_picker_excludes_variants_and_standalones_with_attributes(self):
		from yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance import get_item_filter_options

		self.fixture_key = frappe.generate_hash(length=8)
		template = self.make_item("T", template=True, attributes=True)
		standalone = self.make_item("S")
		self.make_item("ATTR", attributes=True)
		variant = self.make_item("V", parent=template, attributes=True)
		self.assertEqual(
			{r["value"] for r in get_item_filter_options(self.fixture_key)}, {template, standalone}
		)
		self.assertEqual(
			{r["value"] for r in get_item_filter_options(self.fixture_key, variant=1)}, {variant}
		)

	def test_warehouse_code_and_supplier_or_location_name_are_separate(self):
		supplier = frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": f"Balance Name {frappe.generate_hash(length=8)}"}
		).insert()
		own = self.make_warehouse(f"Balance Supplier {frappe.generate_hash(length=8)}", supplier.name)
		location = f"Balance Location {frappe.generate_hash(length=8)}"
		other = self.make_warehouse(location)
		item = _physical_items()[0]
		for warehouse in (own, other):
			self.add_entry(item, warehouse, 7)
			columns, rows = execute({"from_date": today(), "to_date": today(), "warehouse": warehouse})
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["warehouse"], warehouse)
			self.assertEqual(
				rows[0]["warehouse_name"], supplier.supplier_name if warehouse == own else location
			)
			self.assertTrue(
				{"parent_item", "parent_item_name", "item", "warehouse", "warehouse_name"}
				<= {c["fieldname"] for c in columns}
			)

	def test_remove_zero_balance_preserves_current_behavior(self):
		warehouse = self.make_warehouse(f"Balance Zero {frappe.generate_hash(length=8)}")
		item = _physical_items()[0]
		self.add_entry(item, warehouse, 7)
		self.add_entry(item, warehouse, -7)
		base = {"from_date": today(), "to_date": today(), "warehouse": warehouse}
		self.assertEqual(execute({**base, "remove_zero_balance_item": 1})[1], [])
		self.assertEqual(execute({**base, "remove_zero_balance_item": 0})[1][0]["bal_qty"], 0)
		# Movements before the period leave only zero qty/value, even with a valuation rate.
		frappe.db.sql(
			"""update `tabYRP Stock Ledger Entry`
			set posting_date = %s, posting_datetime = timestamp(%s, posting_time), valuation_rate = 10
			where warehouse = %s""",
			(frappe.utils.add_days(today(), -1), frappe.utils.add_days(today(), -1), warehouse),
		)
		self.assertEqual(execute({**base, "remove_zero_balance_item": 1})[1], [])

	def test_dimension_metadata_is_config_driven_including_no_dimensions(self):
		from yrp.yrp_stock.report.yrp_stock_balance import yrp_stock_balance as report

		for dimensions in (
			[],
			[{"fieldname": "quality", "label": "Quality", "dimension_doctype": "Quality"}],
		):
			with (
				self.subTest(dimensions=dimensions),
				patch.object(report, "get_stock_dimensions", return_value=dimensions),
			):
				self.assertEqual(report.get_filter_dimensions(), dimensions)

	def test_dimension_filter_values_match_mrp_list_semantics(self):
		for value, expected in (
			(None, []),
			("", []),
			([], []),
			("[]", []),
			("A", ["A"]),
			(["A", "B", "", None], ["A", "B"]),
			(' ["A", "B"]', ["A", "B"]),
			("[A", ["[A"]),
		):
			with self.subTest(value=value):
				self.assertEqual(parse_list_filter(value), expected)

	def test_dimension_lists_restrict_ledger_balance_and_fifo_rows(self):
		warehouse = self.make_warehouse(f"Balance Dimensions {frappe.generate_hash(length=8)}")
		item = _physical_items()[0]
		# Insert controlled ledger fixtures without depending on dimension master data.
		for index, (lot, received_type) in enumerate(
			product(("BAL-LOT-A", "BAL-LOT-B", "BAL-LOT-C"), ("Accepted", "Rejected")), 1
		):
			doc = frappe.get_doc(
				{
					"doctype": "YRP Stock Ledger Entry",
					"item": item,
					"warehouse": warehouse,
					"posting_date": today(),
					"posting_time": "01:00:00",
					"posting_datetime": f"{today()} 01:00:00",
					"creation": frappe.utils.now(),
					"qty": index,
					"qty_after_transaction": index,
					"stock_value_difference": index * 10,
					"stock_value": index * 10,
					"valuation_rate": 10,
					"voucher_type": "YRP Stock Entry",
					"voucher_no": frappe.generate_hash(length=10),
					"is_cancelled": 0,
					"docstatus": 1,
					"lot": lot,
					"received_type": received_type,
				}
			)
			doc.db_insert()
		# A fully consumed group must survive when remove_zero is off.
		consumed = doc.as_dict()
		consumed.update(
			name=frappe.generate_hash(length=10),
			posting_time="02:00:00",
			posting_datetime=f"{today()} 02:00:00",
			qty=-6,
			qty_after_transaction=0,
			stock_value_difference=-60,
			stock_value=0,
		)
		frappe.get_doc(consumed).db_insert()
		quantities = {
			key: index
			for index, key in enumerate(
				product(("BAL-LOT-A", "BAL-LOT-B", "BAL-LOT-C"), ("Accepted", "Rejected")), 1
			)
		}
		quantities[("BAL-LOT-C", "Rejected")] = 0
		cases = [
			(
				{},
				{
					("BAL-LOT-A", "Accepted"),
					("BAL-LOT-A", "Rejected"),
					("BAL-LOT-B", "Accepted"),
					("BAL-LOT-B", "Rejected"),
					("BAL-LOT-C", "Accepted"),
					("BAL-LOT-C", "Rejected"),
				},
			),
			({"lot": ["BAL-LOT-A"]}, {("BAL-LOT-A", "Accepted"), ("BAL-LOT-A", "Rejected")}),
			(
				{"received_type": ["Accepted"]},
				{("BAL-LOT-A", "Accepted"), ("BAL-LOT-B", "Accepted"), ("BAL-LOT-C", "Accepted")},
			),
			(
				{"lot": ["BAL-LOT-A", "BAL-LOT-B"], "received_type": ["Accepted", "Rejected"]},
				{
					("BAL-LOT-A", "Accepted"),
					("BAL-LOT-A", "Rejected"),
					("BAL-LOT-B", "Accepted"),
					("BAL-LOT-B", "Rejected"),
				},
			),
			(
				{"lot": '["BAL-LOT-B", "BAL-LOT-C"]', "received_type": "Accepted"},
				{("BAL-LOT-B", "Accepted"), ("BAL-LOT-C", "Accepted")},
			),
			(
				{"lot": "[]", "received_type": []},
				{
					("BAL-LOT-A", "Accepted"),
					("BAL-LOT-A", "Rejected"),
					("BAL-LOT-B", "Accepted"),
					("BAL-LOT-B", "Rejected"),
					("BAL-LOT-C", "Accepted"),
					("BAL-LOT-C", "Rejected"),
				},
			),
			({"lot": ["NO-MATCH"]}, set()),
		]
		for extra, expected in cases:
			for ageing, split, remove_zero in product((0, 1), repeat=3):
				with self.subTest(extra=extra, ageing=ageing, split=split, remove_zero=remove_zero):
					_columns, rows = execute(
						{
							"from_date": today(),
							"to_date": today(),
							"warehouse": warehouse,
							"item": item,
							"show_stock_ageing_data": ageing,
							"show_inward_date_split": split,
							"remove_zero_balance_item": remove_zero,
							**extra,
						}
					)
					visible = {key for key in expected if not remove_zero or quantities[key]}
					self.assertEqual({(row["lot"], row["received_type"]) for row in rows}, visible)
					for row in rows:
						self.assertEqual(row["bal_qty"], quantities[(row["lot"], row["received_type"])])
						if split:
							self.assertEqual(
								sum(slot["qty"] for slot in row["inward_date_breakdown"]), row["bal_qty"]
							)
						if ageing:
							self.assertEqual(row["average_age"], 0)
		from yrp.yrp_stock.report.yrp_stock_balance import yrp_stock_balance as report

		with (
			patch.object(report, "get_dimension_fieldnames", return_value=[]),
			patch.object(report, "get_stock_dimensions", return_value=[]),
		):
			columns, rows = execute(
				{"from_date": today(), "to_date": today(), "warehouse": warehouse, "lot": "NO-MATCH"}
			)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["bal_qty"], 15)
			self.assertFalse({"lot", "received_type"} & {column["fieldname"] for column in columns})

	def test_ledger_query_streams_in_posting_datetime_order_without_a_limit(self):
		filters = {"from_date": today(), "to_date": today(), "warehouse": "NO-SUCH-BALANCE-WAREHOUSE"}
		with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
			entries = get_stock_ledger_entries(filters, None, [])
			self.assertEqual(list(entries), [])
		query_call = next(call for call in sql.call_args_list if "tabYRP Stock Ledger Entry" in str(call))
		query = str(query_call.args[0])
		self.assertIn("ORDER BY `posting_datetime`,`creation`", query)
		self.assertNotIn("TIMESTAMP(", query)
		self.assertNotIn("LIMIT", query)
		self.assertTrue(query_call.kwargs["as_iterator"])

	def test_stream_can_aggregate_more_than_the_former_row_limit(self):
		row = frappe._dict(
			item="BAL-STREAM-TEST",
			warehouse="BAL-STREAM-TEST",
			posting_date=frappe.utils.getdate(),
			qty=1,
			valuation_rate=10,
			voucher_type="YRP Stock Entry",
			voucher_no="BAL-STREAM-TEST",
			stock_value_difference=10,
		)
		original_sql = frappe.db.sql

		def stream_rows(query, *args, **kwargs):
			if "tabYRP Stock Ledger Entry" in str(query):
				return repeat(row, 500_001)
			return original_sql(query, *args, **kwargs)

		filters = {"from_date": today(), "to_date": today()}
		with patch.object(frappe.db, "sql", side_effect=stream_rows):
			balances = get_item_warehouse_map(filters, get_stock_ledger_entries(filters, None, []), [])
		self.assertEqual(len(balances), 1)
		balance = balances[(row.item, row.warehouse)]
		self.assertEqual(balance.bal_qty, 500_001)
		self.assertEqual(balance.bal_val, 5_000_010)

	def test_mid_period_reconciliation_resets_balance_and_preserves_ageing(self):
		warehouse = self.make_warehouse(f"Balance Reconciliation {frappe.generate_hash(length=8)}")
		item = _physical_items()[0]
		for offset, qty, balance, value, reconciliation in (
			(-4, 10, 10, 100, False),
			(-3, 3, 13, 30, False),
			(-2, 999, 8, -50, True),
			(-1, -2, 6, -20, False),
		):
			posting_date = frappe.utils.add_days(today(), offset)
			frappe.get_doc(
				{
					"doctype": "YRP Stock Ledger Entry",
					"item": item,
					"warehouse": warehouse,
					"posting_date": posting_date,
					"posting_time": "01:00:00",
					"posting_datetime": f"{posting_date} 01:00:00",
					"qty": qty,
					"qty_after_transaction": balance,
					"stock_value_difference": value,
					"stock_value": balance * 10,
					"valuation_rate": 10,
					"voucher_type": "YRP Stock Reconciliation" if reconciliation else "YRP Stock Entry",
					"voucher_no": frappe.generate_hash(length=10),
					"docstatus": 1,
					**_dimension_values(),
				}
			).db_insert()
		for ageing in (0, 1):
			with self.subTest(ageing=ageing):
				_, rows = execute(
					{
						"from_date": frappe.utils.add_days(today(), -3),
						"to_date": today(),
						"warehouse": warehouse,
						"show_stock_ageing_data": ageing,
						"show_inward_date_split": ageing,
					}
				)
				self.assertEqual(len(rows), 1)
				for field, expected in {
					"opening_qty": 10,
					"opening_val": 100,
					"in_qty": 3,
					"in_val": 30,
					"out_qty": 7,
					"out_val": 70,
					"bal_qty": 6,
					"bal_val": 60,
					"val_rate": 10,
				}.items():
					self.assertEqual(rows[0][field], expected, field)
				if ageing:
					self.assertEqual(rows[0]["average_age"], 3.5)
					self.assertEqual(rows[0]["earliest_age"], 4)
					self.assertEqual(rows[0]["latest_age"], 3)
					self.assertEqual([slot["qty"] for slot in rows[0]["inward_date_breakdown"]], [3, 3])


class TestStockBalanceItemPermissions(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.items = _physical_items()
		if len(self.items) < 2:
			self.skipTest("needs two physical stock Items")
		company = frappe.db.get_value("Company", {}, "name")
		if not company:
			self.skipTest("needs one Company")
		self.warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"Stock Balance Permission {frappe.generate_hash(length=8)}",
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.dimensions = _dimension_values()
		self.user = _make_stock_user()
		self.ledger_entries = [_insert_ledger(item, self.warehouse, self.dimensions) for item in self.items]
		self.user_permission = (
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": self.user,
					"allow": "Item",
					"for_value": self.items[0],
					"apply_to_all_doctypes": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		if getattr(self, "user_permission", None) and frappe.db.exists(
			"User Permission", self.user_permission
		):
			frappe.delete_doc("User Permission", self.user_permission, ignore_permissions=True, force=True)
		for name in getattr(self, "ledger_entries", []):
			if frappe.db.exists("YRP Stock Ledger Entry", name):
				frappe.delete_doc("YRP Stock Ledger Entry", name, ignore_permissions=True, force=True)
		if getattr(self, "warehouse", None) and frappe.db.exists("Warehouse", self.warehouse):
			frappe.delete_doc("Warehouse", self.warehouse, ignore_permissions=True, force=True)
		if getattr(self, "user", None) and frappe.db.exists("User", self.user):
			frappe.delete_doc("User", self.user, ignore_permissions=True, force=True)

	def test_item_name_is_data_and_report_filters_by_item_permission(self):
		item_name = next(
			column for column in get_columns({}, get_stock_dimensions()) if column["fieldname"] == "item_name"
		)
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
