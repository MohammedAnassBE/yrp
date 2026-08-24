"""Tests for Stock Entry — all 5 purposes + validation error paths."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


ITEM_VARIANT = None
WH_FROM = None
WH_TO = None
UOM = None
STOCK_DIMENSIONS = {}


def _test_warehouse(label):
	"""Create a transaction-scoped warehouse with no dependency on site masters."""
	return frappe.get_doc({
		"doctype": "Warehouse",
		"name1": f"_Test Stock Entry {label} {frappe.generate_hash(length=8)}",
	}).insert(ignore_permissions=True).name


def _seed_stock(warehouse, qty=100, rate=10):
	"""Add opening stock so reduces don't fail on negative stock."""
	se = frappe.get_doc({
		"doctype": "Stock Entry",
		"purpose": "Material Receipt",
		"to_warehouse": warehouse,
		"posting_date": frappe.utils.today(),
		"posting_time": frappe.utils.nowtime(),
		"items": [{
			"item": ITEM_VARIANT,
			"qty": qty,
			"rate": rate,
			"uom": UOM,
			"row_index": 0,
			"table_index": 0,
			**STOCK_DIMENSIONS,
		}],
	})
	se.insert(ignore_permissions=True)
	se.submit()
	return se


def _make_se(purpose, from_wh=None, to_wh=None, qty=10, rate=5, skip_transit=0):
	"""Helper to create a Stock Entry."""
	return frappe.get_doc({
		"doctype": "Stock Entry",
		"purpose": purpose,
		"from_warehouse": from_wh,
		"to_warehouse": to_wh,
		"skip_transit": skip_transit,
		"posting_date": frappe.utils.today(),
		"posting_time": frappe.utils.nowtime(),
		"items": [{
			"item": ITEM_VARIANT,
			"qty": qty,
			"rate": rate,
			"uom": UOM,
			"row_index": 0,
			"table_index": 0,
			**STOCK_DIMENSIONS,
		}],
	})


def _get_sles(voucher_no, cancelled=0):
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_no": voucher_no, "is_cancelled": cancelled},
		fields=["warehouse", "qty", "item", "voucher_type"],
		order_by="creation asc",
	)


class TestStockEntry(FrappeTestCase):
	def test_before_cancel_preserves_owner_link_exemption(self):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.ignore_linked_doctypes = ("Owning Voucher",)

		stock_entry.before_cancel()

		self.assertEqual(
			stock_entry.ignore_linked_doctypes,
			(
				"Owning Voucher",
				"Stock Ledger Entry",
				"Repost Item Valuation",
				"Stock Valuation Adjustment",
			),
		)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		global ITEM_VARIANT, STOCK_DIMENSIONS, UOM, WH_FROM, WH_TO

		ITEM_VARIANT = frappe.db.get_value("Item Variant", {}, "name")
		if not ITEM_VARIANT:
			raise frappe.DoesNotExistError("Stock Entry tests require one Item Variant")
		parent_item = frappe.db.get_value("Item Variant", ITEM_VARIANT, "item")
		UOM = frappe.db.get_value("Item", parent_item, "default_unit_of_measure")
		if not UOM:
			raise frappe.DoesNotExistError(f"{parent_item} requires a default UOM")

		from yrp.stock.dimensions import get_mandatory_dimensions

		STOCK_DIMENSIONS = {}
		for dimension in get_mandatory_dimensions():
			fieldname = dimension["fieldname"]
			target_doctype = dimension.get("dimension_doctype")
			if fieldname == "received_type":
				value = frappe.db.get_single_value(
					"YRP Stock Settings",
					"default_received_type",
				)
			else:
				value = frappe.db.get_value(target_doctype, {}, "name") if target_doctype else None
			if not value:
				raise frappe.DoesNotExistError(
					f"Stock Entry tests require a {target_doctype or fieldname} value"
				)
			STOCK_DIMENSIONS[fieldname] = value

		WH_FROM = _test_warehouse("From")
		WH_TO = _test_warehouse("To")
		cls.transit_wh = _test_warehouse("Transit")
		cls.transit_warehouse_override = cls.transit_wh
		cls._get_single_value = frappe.db.get_single_value

		def get_single_value(doctype, fieldname, *args, **kwargs):
			if doctype == "YRP Stock Settings" and fieldname == "transit_warehouse":
				return cls.transit_warehouse_override
			return cls._get_single_value(doctype, fieldname, *args, **kwargs)

		cls._settings_patcher = patch.object(
			frappe.db,
			"get_single_value",
			side_effect=get_single_value,
		)
		cls._settings_patcher.start()
		cls.addClassCleanup(cls._settings_patcher.stop)
		# Seed stock at both warehouses so issue/consume/send don't fail
		_seed_stock(WH_FROM, 500)
		_seed_stock(WH_TO, 500)
		_seed_stock(cls.transit_wh, 500)

	# ---------------------------------------------------------------
	# Exact outgoing value — compound production receipts
	# ---------------------------------------------------------------
	def test_make_sl_entries_returns_actual_fifo_value(self):
		from yrp.stock.stock_ledger import make_sl_entries

		warehouse = _test_warehouse("FIFO Result")
		for qty, rate in ((2, 10), (3, 20)):
			receipt = _make_se("Material Receipt", to_wh=warehouse, qty=qty, rate=rate)
			receipt.insert(ignore_permissions=True)
			make_sl_entries(
				[
					{
						"item": ITEM_VARIANT,
						"warehouse": warehouse,
						"uom": UOM,
						"voucher_type": "Stock Entry",
						"voucher_no": receipt.name,
						"voucher_detail_no": receipt.items[0].name,
						"posting_date": frappe.utils.today(),
						"posting_time": frappe.utils.nowtime(),
						"qty": qty,
						"rate": rate,
						**STOCK_DIMENSIONS,
					}
				],
				force_inline=True,
			)
		voucher = _make_se("Material Issue", from_wh=warehouse, qty=4, rate=0)
		voucher.insert(ignore_permissions=True)

		result = make_sl_entries(
			[
				{
					"item": ITEM_VARIANT,
					"warehouse": warehouse,
					"uom": UOM,
					"voucher_type": "Stock Entry",
					"voucher_no": voucher.name,
					"voucher_detail_no": voucher.items[0].name,
					"posting_date": frappe.utils.today(),
					"posting_time": frappe.utils.nowtime(),
					"qty": -4,
					"rate": 0,
					"outgoing_rate": 0,
					"_result_key": "consumed-input",
					**STOCK_DIMENSIONS,
				}
			],
			return_details=True,
			force_inline=True,
		)

		detail = result["entries"]["consumed-input"]
		self.assertAlmostEqual(detail["value"], 60)
		self.assertAlmostEqual(detail["rate"], 15)
		self.assertFalse(detail["queued_repost"])

	# ---------------------------------------------------------------
	# 1. Material Issue — reduces stock at from_warehouse
	# ---------------------------------------------------------------
	def test_material_issue(self):
		se = _make_se("Material Issue", from_wh=WH_FROM)
		se.insert(ignore_permissions=True)
		se.submit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)

	# ---------------------------------------------------------------
	# 2. Material Receipt — adds stock at to_warehouse
	# ---------------------------------------------------------------
	def test_material_receipt(self):
		se = _make_se("Material Receipt", to_wh=WH_TO, rate=10)
		se.insert(ignore_permissions=True)
		se.submit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0]["warehouse"], WH_TO)
		self.assertTrue(sles[0]["qty"] > 0)

	# ---------------------------------------------------------------
	# 3. Send to Warehouse — from_wh → transit
	# ---------------------------------------------------------------
	def test_send_to_warehouse(self):
		se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO)
		se.insert(ignore_permissions=True)
		se.submit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 2)
		# First SLE: -qty at from_warehouse
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)
		# Second SLE: +qty at transit
		self.assertEqual(sles[1]["warehouse"], self.transit_wh)
		self.assertTrue(sles[1]["qty"] > 0)

	# ---------------------------------------------------------------
	# 4. Receive at Warehouse — transit → to_wh
	# ---------------------------------------------------------------
	def test_receive_at_warehouse(self):
		se = _make_se("Receive at Warehouse", from_wh=WH_FROM, to_wh=WH_TO)
		se.insert(ignore_permissions=True)
		se.submit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 2)
		# First SLE: -qty at transit
		self.assertEqual(sles[0]["warehouse"], self.transit_wh)
		self.assertTrue(sles[0]["qty"] < 0)
		# Second SLE: +qty at to_warehouse
		self.assertEqual(sles[1]["warehouse"], WH_TO)
		self.assertTrue(sles[1]["qty"] > 0)

	# ---------------------------------------------------------------
	# 5. Material Consumed — reduces stock at from_warehouse
	# ---------------------------------------------------------------
	def test_material_consumed(self):
		se = _make_se("Material Consumed", from_wh=WH_FROM)
		se.insert(ignore_permissions=True)
		se.submit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)

	# ---------------------------------------------------------------
	# 6. Send to WH — missing transit setting
	# ---------------------------------------------------------------
	def test_send_to_wh_missing_transit(self):
		type(self).transit_warehouse_override = ""
		try:
			se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO)
			self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)
		finally:
			type(self).transit_warehouse_override = self.transit_wh

	# ---------------------------------------------------------------
	# 7. Material Issue — no from_warehouse
	# ---------------------------------------------------------------
	def test_material_issue_no_from_wh(self):
		se = _make_se("Material Issue")
		self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)

	# ---------------------------------------------------------------
	# 8. Material Receipt — no to_warehouse
	# ---------------------------------------------------------------
	def test_material_receipt_no_to_wh(self):
		se = _make_se("Material Receipt")
		self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)

	# ---------------------------------------------------------------
	# 9. Empty items
	# ---------------------------------------------------------------
	def test_empty_items(self):
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"from_warehouse": WH_FROM,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"items": [],
		})
		self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)

	# ---------------------------------------------------------------
	# 10. Zero qty row
	# ---------------------------------------------------------------
	def test_zero_qty(self):
		se = _make_se("Material Issue", from_wh=WH_FROM, qty=0)
		self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)

	# ---------------------------------------------------------------
	# 11. Cancel after submit — reversal SLEs
	# ---------------------------------------------------------------
	def test_cancel_reverses_sles(self):
		se = _make_se("Material Receipt", to_wh=WH_TO, rate=10)
		se.insert(ignore_permissions=True)
		se.submit()

		original_sles = _get_sles(se.name, cancelled=0)
		self.assertEqual(len(original_sles), 1)

		se.cancel()

		# Original SLEs marked cancelled
		cancelled_sles = _get_sles(se.name, cancelled=1)
		self.assertTrue(len(cancelled_sles) >= 1)
