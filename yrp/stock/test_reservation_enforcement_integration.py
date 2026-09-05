"""Database-level checks for reservation enforcement in the stock engine."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.stock_ledger import NegativeStockError, UpdateEntriesAfter
from yrp.stock.utils import get_stock_balance


class TestReservationEnforcementIntegration(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		suffix = frappe.generate_hash(length=8)
		cls.uom = cls._ensure_uom()
		cls.item_group = frappe.get_doc(
			{
				"doctype": 'YRP Item Group',
				"item_group_name": f"_Test Reservation Group {suffix}",
				"is_group": 0,
			}
		).insert(ignore_permissions=True).name
		item = frappe.get_doc(
			{
				"doctype": 'YRP Item',
				"name1": f"_Test Reservation Item {suffix}",
				"item_group": cls.item_group,
				"default_unit_of_measure": cls.uom,
				"is_stock_item": 1,
			}
		).insert(ignore_permissions=True)
		cls.item_variant = frappe.get_doc(
			{"doctype": 'YRP Item Variant', "item": item.name}
		).insert(ignore_permissions=True).name
		cls.dimensions = cls._dimension_values()

	@staticmethod
	def _ensure_uom():
		name = "_Test Reservation Unit"
		if not frappe.db.exists("UOM", name):
			frappe.get_doc(
				{"doctype": "UOM", "uom_name": name, "enabled": 1}
			).insert(ignore_permissions=True)
		return name

	@classmethod
	def _dimension_values(cls):
		values = {}
		for dimension in get_stock_dimensions():
			fieldname = dimension["fieldname"]
			doctype = dimension["dimension_doctype"]
			value = None
			if fieldname == "received_type":
				value = frappe.db.get_single_value(
					"YRP Stock Settings", "default_received_type"
				)
			value = value or frappe.db.get_value(doctype, {}, "name")
			if dimension.get("mandatory") and not value:
				raise frappe.DoesNotExistError(
					f"Reservation integration tests require a {doctype} value for {fieldname}"
				)
			if value:
				values[fieldname] = value
		return values

	def _warehouse(self, label):
		return frappe.get_doc(
			{
				"doctype": 'YRP Warehouse',
				"name1": f"_Test Reservation {label} {frappe.generate_hash(length=8)}",
			}
		).insert(ignore_permissions=True).name

	def _stock_entry(self, purpose, warehouse, qty, *, posting_time=None):
		values = {
			"doctype": 'YRP Stock Entry',
			"purpose": purpose,
			"posting_date": nowdate(),
			"items": [
				{
					"item": self.item_variant,
					"qty": qty,
					"rate": 10,
					"uom": self.uom,
					"row_index": 0,
					"table_index": 0,
					**self.dimensions,
				}
			],
		}
		if purpose == "Material Receipt":
			values["to_warehouse"] = warehouse
		else:
			values["from_warehouse"] = warehouse
		if posting_time:
			values["edit_posting_date_and_time"] = 1
			values["posting_time"] = posting_time
		return frappe.get_doc(values)

	def _seed(self, warehouse, qty=50, *, posting_time=None):
		receipt = self._stock_entry(
			"Material Receipt", warehouse, qty, posting_time=posting_time
		)
		receipt.insert(ignore_permissions=True)
		receipt.submit()
		return receipt

	def _reserve(self, warehouse, qty):
		sre = frappe.get_doc(
			{
				"doctype": 'YRP Stock Reservation Entry',
				"item_code": self.item_variant,
				"warehouse": warehouse,
				"reserved_qty": qty,
				"available_qty": 9999,
				"voucher_type": 'YRP Stock Update',
				"voucher_no": f"_Test Reservation Owner {frappe.generate_hash(length=8)}",
				**self.dimensions,
			}
		)
		sre.flags.ignore_links = True
		sre.insert(ignore_permissions=True)
		sre.flags.ignore_links = True
		sre.submit()
		return sre

	def _assert_submit_rejected(self, doc):
		doc.insert(ignore_permissions=True)
		savepoint = f"reservation_submit_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(NegativeStockError):
				doc.submit()
		finally:
			frappe.db.rollback(save_point=savepoint)
		doc.reload()
		self.assertEqual(doc.docstatus, 0)
		self.assertFalse(
			frappe.db.exists(
				'YRP Stock Ledger Entry',
				{
					"voucher_type": doc.doctype,
					"voucher_no": doc.name,
					"is_cancelled": 0,
				},
			)
		)

	def test_material_issue_cannot_consume_reserved_stock(self):
		warehouse = self._warehouse("Issue")
		self._seed(warehouse)
		self._reserve(warehouse, 30)

		self._assert_submit_rejected(
			self._stock_entry("Material Issue", warehouse, 25)
		)
		self.assertAlmostEqual(
			get_stock_balance(self.item_variant, warehouse, **self.dimensions), 50
		)

	def test_stock_reconciliation_cannot_write_below_reservation(self):
		warehouse = self._warehouse("Reconciliation")
		self._seed(warehouse)
		self._reserve(warehouse, 30)
		reconciliation = frappe.get_doc(
			{
				"doctype": 'YRP Stock Reconciliation',
				"purpose": "Stock Reconciliation",
				"posting_date": nowdate(),
				"default_warehouse": warehouse,
				"items": [
					{
						"item": self.item_variant,
						"warehouse": warehouse,
						"qty": 20,
						"rate": 10,
						"uom": self.uom,
						**self.dimensions,
					}
				],
			}
		)

		self._assert_submit_rejected(reconciliation)
		self.assertAlmostEqual(
			get_stock_balance(self.item_variant, warehouse, **self.dimensions), 50
		)

	def test_cancelling_incoming_stock_cannot_strand_reservation(self):
		warehouse = self._warehouse("Cancel Receipt")
		receipt = self._seed(warehouse)
		self._reserve(warehouse, 30)
		savepoint = f"reservation_cancel_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(NegativeStockError):
				receipt.cancel()
		finally:
			frappe.db.rollback(save_point=savepoint)

		receipt.reload()
		self.assertEqual(receipt.docstatus, 1)
		self.assertAlmostEqual(
			get_stock_balance(self.item_variant, warehouse, **self.dimensions), 50
		)

	def test_backdated_issue_cannot_make_future_balance_breach_reservation(self):
		warehouse = self._warehouse("Backdated")
		self._seed(warehouse, posting_time="08:00:00")
		future_issue = self._stock_entry(
			"Material Issue", warehouse, 15, posting_time="10:00:00"
		)
		future_issue.insert(ignore_permissions=True)
		future_issue.submit()
		self._reserve(warehouse, 30)

		self._assert_submit_rejected(
			self._stock_entry(
				"Material Issue", warehouse, 10, posting_time="09:00:00"
			)
		)
		self.assertAlmostEqual(
			get_stock_balance(self.item_variant, warehouse, **self.dimensions), 35
		)

	def test_valuation_only_replay_ignores_current_reservation_at_historical_points(self):
		warehouse = self._warehouse("Valuation Replay")
		self._seed(warehouse, qty=50, posting_time="08:00:00")
		issue = self._stock_entry(
			"Material Issue", warehouse, 40, posting_time="09:00:00"
		)
		issue.insert(ignore_permissions=True)
		issue.submit()
		self._seed(warehouse, qty=90, posting_time="10:00:00")
		self._reserve(warehouse, 80)

		args = frappe._dict(
			{
				"item": self.item_variant,
				"warehouse": warehouse,
				"posting_date": nowdate(),
				"posting_time": "08:00:00",
				"voucher_type": 'YRP Stock Valuation Adjustment',
				"voucher_no": None,
				**self.dimensions,
			}
		)
		UpdateEntriesAfter(args).run()

		self.assertAlmostEqual(
			get_stock_balance(self.item_variant, warehouse, **self.dimensions), 100
		)
