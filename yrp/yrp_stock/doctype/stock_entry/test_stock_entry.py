"""Tests for Stock Entry — all 5 purposes + validation error paths."""

import frappe
from frappe.tests.utils import FrappeTestCase


# Use existing test data from the site
ITEM_VARIANT = "Item-00005-45 cm-Blue"  # existing simple variant
WH_FROM = "Supplier WH-1"
WH_TO = "Supplier WH-2"
UOM = "Piece"


def _ensure_transit_warehouse():
	"""Make sure transit warehouse is configured."""
	tw = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
	if not tw:
		if not frappe.db.exists("Warehouse", "Transit WH"):
			frappe.get_doc({"doctype": "Warehouse", "name1": "Transit WH"}).insert(ignore_permissions=True)
		frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", "Transit WH")
		frappe.db.commit()
	return frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")


def _seed_stock(warehouse, qty=100):
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
			"rate": 10,
			"uom": UOM,
			"row_index": 0,
			"table_index": 0,
		}],
	})
	se.insert(ignore_permissions=True)
	se.submit()
	frappe.db.commit()
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

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.transit_wh = _ensure_transit_warehouse()
		# Seed stock at both warehouses so issue/consume/send don't fail
		_seed_stock(WH_FROM, 500)
		_seed_stock(WH_TO, 500)
		_seed_stock(cls.transit_wh, 500)

	# ---------------------------------------------------------------
	# 1. Material Issue — reduces stock at from_warehouse
	# ---------------------------------------------------------------
	def test_material_issue(self):
		se = _make_se("Material Issue", from_wh=WH_FROM)
		se.insert(ignore_permissions=True)
		se.submit()
		frappe.db.commit()

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
		frappe.db.commit()

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
		frappe.db.commit()

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
		frappe.db.commit()

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
		frappe.db.commit()

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)

	# ---------------------------------------------------------------
	# 6. Send to WH — missing transit setting
	# ---------------------------------------------------------------
	def test_send_to_wh_missing_transit(self):
		# Temporarily clear transit warehouse
		orig = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
		frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", "")
		frappe.db.commit()

		try:
			se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO)
			self.assertRaises(frappe.ValidationError, se.insert, ignore_permissions=True)
		finally:
			frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", orig)
			frappe.db.commit()

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
		frappe.db.commit()

		original_sles = _get_sles(se.name, cancelled=0)
		self.assertEqual(len(original_sles), 1)

		se.cancel()
		frappe.db.commit()

		# Original SLEs marked cancelled
		cancelled_sles = _get_sles(se.name, cancelled=1)
		self.assertTrue(len(cancelled_sles) >= 1)
