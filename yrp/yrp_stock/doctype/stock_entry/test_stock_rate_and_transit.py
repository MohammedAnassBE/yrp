"""Tests for rate auto-fetch from SLE and Send/Receive transit flow.

Covers:
1. Rate auto-fetch from last uncancelled SLE (Stock Entry & Stock Update)
2. Rate picked up from Stock Reconciliation SLE (qty=0 case)
3. Skip transit — direct warehouse transfer
4. Send to Warehouse → End Transit → Receive at Warehouse full flow
5. Partial receive — per_transferred tracking
6. set_receive_links — against_stock_entry / ste_detail survive ungroup
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt


ITEM_VARIANT = "Item-00005-45 cm-Blue"
WH_FROM = "Supplier WH-1"
WH_TO = "Supplier WH-2"
UOM = "Piece"


def _ensure_transit_warehouse():
	tw = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
	if not tw:
		if not frappe.db.exists("Warehouse", "Transit WH"):
			frappe.get_doc({"doctype": "Warehouse", "name1": "Transit WH"}).insert(ignore_permissions=True)
		frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", "Transit WH")
	
	return frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")


def _seed_stock(warehouse, qty=100, rate=10):
	"""Material Receipt to seed opening stock."""
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
		}],
	})
	se.insert(ignore_permissions=True)
	se.submit()

	return se


def _make_se(purpose, from_wh=None, to_wh=None, qty=10, rate=5, skip_transit=0):
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


def _make_reconciliation(warehouse, qty=50, rate=100, allow_zero=0):
	sr = frappe.get_doc({
		"doctype": "Stock Reconciliation",
		"purpose": "Stock Reconciliation",
		"default_warehouse": warehouse,
		"posting_date": frappe.utils.today(),
		"posting_time": frappe.utils.nowtime(),
		"items": [{
			"item": ITEM_VARIANT,
			"qty": qty,
			"rate": rate,
			"uom": UOM,
			"warehouse": warehouse,
			"allow_zero_valuation_rate": allow_zero,
			"row_index": 0,
			"table_index": 0,
		}],
	})
	sr.insert(ignore_permissions=True)
	sr.submit()

	return sr


def _get_sles(voucher_no, cancelled=0):
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_no": voucher_no, "is_cancelled": cancelled},
		fields=["warehouse", "qty", "item", "valuation_rate", "rate", "voucher_type"],
		order_by="creation asc",
	)


class TestRateAutoFetch(FrappeTestCase):
	"""Rate auto-fetch from last uncancelled SLE."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.transit_wh = _ensure_transit_warehouse()
		_seed_stock(WH_FROM, 500, rate=10)
		_seed_stock(WH_TO, 500, rate=10)
		_seed_stock(cls.transit_wh, 500, rate=10)

	# ---------------------------------------------------------------
	# 1. Stock Entry picks rate from last SLE
	# ---------------------------------------------------------------
	def test_rate_fetched_from_last_sle(self):
		"""After seeding stock at rate=10, set_rate_from_last_sle should fill the rate."""
		se = _make_se("Material Issue", from_wh=WH_FROM, rate=0)
		se.set_rate_from_last_sle()

		self.assertGreater(flt(se.items[0].rate), 0, "Rate should be auto-fetched from SLE, not 0")

	# ---------------------------------------------------------------
	# 2. Rate picked up after Stock Reconciliation (qty=0 SLE)
	# ---------------------------------------------------------------
	def test_rate_from_reconciliation_sle(self):
		"""Stock Reconciliation creates SLE with qty=0 but valid valuation_rate.
		Stock Entry should still pick it up (no qty>0 filter)."""
		sr = _make_reconciliation(WH_FROM, qty=200, rate=100)

		# Verify SLE was created with qty=0
		sr_sles = _get_sles(sr.name)
		self.assertTrue(len(sr_sles) >= 1)
		self.assertEqual(sr_sles[0]["qty"], 0, "Reconciliation SLE should have qty=0")

		# Now create a Stock Entry and call set_rate_from_last_sle
		se = _make_se("Material Issue", from_wh=WH_FROM, rate=0)
		se.set_rate_from_last_sle()

		# valuation_rate from the reconciliation should be picked up
		self.assertGreater(flt(se.items[0].rate), 0,
			"Rate should be fetched from reconciliation SLE (qty=0)")

	# ---------------------------------------------------------------
	# 3. Rate is 0 when no SLE exists (no error for Stock Entry)
	# ---------------------------------------------------------------
	def test_rate_zero_when_no_sle(self):
		"""If no SLE exists for an item, rate stays 0 — no error thrown."""
		se = _make_se("Material Issue", from_wh=WH_FROM, rate=0)
		# Temporarily mark all SLEs cancelled so no rate is found
		frappe.db.sql(
			"UPDATE `tabStock Ledger Entry` SET is_cancelled=1 WHERE item=%s",
			ITEM_VARIANT,
		)
		try:
			se.set_rate_from_last_sle()
			self.assertEqual(flt(se.items[0].rate), 0)
		finally:
			frappe.db.sql(
				"UPDATE `tabStock Ledger Entry` SET is_cancelled=0 WHERE item=%s",
				ITEM_VARIANT,
			)
		


class TestSkipTransit(FrappeTestCase):
	"""Send to Warehouse with skip_transit bypasses transit warehouse."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.transit_wh = _ensure_transit_warehouse()
		_seed_stock(WH_FROM, 500, rate=10)
		_seed_stock(WH_TO, 500, rate=10)

	# ---------------------------------------------------------------
	# 4. Skip transit — stock goes directly to target warehouse
	# ---------------------------------------------------------------
	def test_skip_transit_direct_transfer(self):
		"""With skip_transit=1, Send to Warehouse should move stock
		from source directly to target, not via transit."""
		se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, skip_transit=1)
		se.insert(ignore_permissions=True)
		se.submit()
	

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 2)
		# SLE 1: -qty at from_warehouse
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)
		# SLE 2: +qty at to_warehouse (NOT transit)
		self.assertEqual(sles[1]["warehouse"], WH_TO)
		self.assertTrue(sles[1]["qty"] > 0)

	# ---------------------------------------------------------------
	# 5. Without skip_transit — stock goes to transit
	# ---------------------------------------------------------------
	def test_no_skip_transit_goes_to_transit(self):
		"""Without skip_transit, Send to Warehouse should go via transit."""
		se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, skip_transit=0)
		se.insert(ignore_permissions=True)
		se.submit()
	

		sles = _get_sles(se.name)
		self.assertEqual(len(sles), 2)
		self.assertEqual(sles[0]["warehouse"], WH_FROM)
		self.assertTrue(sles[0]["qty"] < 0)
		# Goes to transit, not directly to target
		self.assertEqual(sles[1]["warehouse"], self.transit_wh)
		self.assertTrue(sles[1]["qty"] > 0)


class TestSendReceiveTransitFlow(FrappeTestCase):
	"""Full Send to Warehouse → End Transit → Receive at Warehouse flow."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.transit_wh = _ensure_transit_warehouse()
		_seed_stock(WH_FROM, 1000, rate=10)
		_seed_stock(WH_TO, 500, rate=10)
		_seed_stock(cls.transit_wh, 500, rate=10)

	# ---------------------------------------------------------------
	# 6. make_stock_in_entry creates correct Receive document
	# ---------------------------------------------------------------
	def test_make_stock_in_entry(self):
		"""End Transit (make_stock_in_entry) should create a Receive at Warehouse
		with correct purpose, outgoing_stock_entry, and item qty."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=20)
		send.insert(ignore_permissions=True)
		send.submit()
	

		receive = make_stock_in_entry(send.name)

		self.assertEqual(receive.purpose, "Receive at Warehouse")
		self.assertEqual(receive.outgoing_stock_entry, send.name)
		self.assertTrue(len(receive.items) > 0)
		self.assertEqual(flt(receive.items[0].qty), 20)

	# ---------------------------------------------------------------
	# 7. Full flow — per_transferred updated after receive submit
	# ---------------------------------------------------------------
	def test_full_transit_flow_per_transferred(self):
		"""Submit Send → create Receive via make_stock_in_entry → submit Receive.
		per_transferred on Send should become 100."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=30)
		send.insert(ignore_permissions=True)
		send.submit()
	

		# per_transferred should be 0 initially
		send.reload()
		self.assertEqual(flt(send.per_transferred), 0)

		# Create and submit receive
		receive = make_stock_in_entry(send.name)
		receive.insert(ignore_permissions=True)
		receive.submit()
	

		# per_transferred on source should be 100
		send.reload()
		self.assertEqual(flt(send.per_transferred), 100)

		# transferred_qty on source row should equal original qty
		self.assertEqual(flt(send.items[0].transferred_qty), 30)

	# ---------------------------------------------------------------
	# 8. Receive sets against_stock_entry and ste_detail on rows
	# ---------------------------------------------------------------
	def test_receive_links_set_on_rows(self):
		"""After save, Receive at Warehouse rows should have
		against_stock_entry and ste_detail linking back to source."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=15)
		send.insert(ignore_permissions=True)
		send.submit()
	

		receive = make_stock_in_entry(send.name)
		receive.insert(ignore_permissions=True)
	

		receive.reload()
		for row in receive.items:
			self.assertEqual(row.against_stock_entry, send.name,
				"against_stock_entry should point to source Send entry")
			self.assertTrue(row.ste_detail,
				"ste_detail should be set to source row name")

	# ---------------------------------------------------------------
	# 9. Partial receive — per_transferred reflects partial qty
	# ---------------------------------------------------------------
	def test_partial_receive(self):
		"""If Send has qty=40 and Receive only takes 20,
		per_transferred should be 50, not 100."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=40)
		send.insert(ignore_permissions=True)
		send.submit()
	

		# Create receive but reduce qty to half
		receive = make_stock_in_entry(send.name)
		receive.items[0].qty = 20
		receive.insert(ignore_permissions=True)
		receive.submit()
	

		send.reload()
		self.assertEqual(flt(send.per_transferred), 50)
		self.assertEqual(flt(send.items[0].transferred_qty), 20)

	# ---------------------------------------------------------------
	# 10. Cancel receive — per_transferred resets
	# ---------------------------------------------------------------
	def test_cancel_receive_resets_per_transferred(self):
		"""Cancelling a Receive at Warehouse should reset per_transferred
		on the source Send entry."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=25)
		send.insert(ignore_permissions=True)
		send.submit()
	

		receive = make_stock_in_entry(send.name)
		receive.insert(ignore_permissions=True)
		receive.submit()
	

		send.reload()
		self.assertEqual(flt(send.per_transferred), 100)

		# Cancel the receive
		receive.cancel()
	

		send.reload()
		self.assertEqual(flt(send.per_transferred), 0)
		self.assertEqual(flt(send.items[0].transferred_qty), 0)

	# ---------------------------------------------------------------
	# 11. Skip transit — no End Transit button needed
	# ---------------------------------------------------------------
	def test_skip_transit_no_receive_needed(self):
		"""With skip_transit=1, stock goes directly to target.
		make_stock_in_entry should not be called (no transit to end)."""
		se = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO,
			qty=10, skip_transit=1)
		se.insert(ignore_permissions=True)
		se.submit()
	

		# SLEs should show direct transfer (no transit warehouse)
		sles = _get_sles(se.name)
		warehouses = [s["warehouse"] for s in sles]
		self.assertNotIn(self.transit_wh, warehouses,
			"Transit warehouse should not appear when skip_transit=1")

	# ---------------------------------------------------------------
	# 12. Receive SLE correctness — transit out, target in
	# ---------------------------------------------------------------
	def test_receive_creates_correct_sles(self):
		"""Receive at Warehouse should create 2 SLEs:
		-qty from transit, +qty to target warehouse."""
		from yrp.yrp_stock.doctype.stock_entry.stock_entry import make_stock_in_entry

		send = _make_se("Send to Warehouse", from_wh=WH_FROM, to_wh=WH_TO, qty=10)
		send.insert(ignore_permissions=True)
		send.submit()
	

		receive = make_stock_in_entry(send.name)
		receive.insert(ignore_permissions=True)
		receive.submit()
	

		sles = _get_sles(receive.name)
		self.assertEqual(len(sles), 2)
		# SLE 1: -qty from transit
		self.assertEqual(sles[0]["warehouse"], self.transit_wh)
		self.assertTrue(sles[0]["qty"] < 0)
		# SLE 2: +qty to target
		self.assertEqual(sles[1]["warehouse"], WH_TO)
		self.assertTrue(sles[1]["qty"] > 0)


class TestReconciliationRate(FrappeTestCase):
	"""Stock Reconciliation rate auto-fetch and mandatory validation."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Seed stock so there's a valid SLE with valuation_rate
		_seed_stock(WH_FROM, 100, rate=50)

	# ---------------------------------------------------------------
	# 13. Reconciliation auto-fills rate from last SLE when user omits it
	# ---------------------------------------------------------------
	def test_reconciliation_rate_auto_filled_from_sle(self):
		"""If user creates reconciliation without rate, it should be
		auto-filled from the last uncancelled SLE."""
		sr = frappe.get_doc({
			"doctype": "Stock Reconciliation",
			"purpose": "Stock Reconciliation",
			"default_warehouse": WH_FROM,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"items": [{
				"item": ITEM_VARIANT,
				"qty": 80,
				"rate": 0,
				"uom": UOM,
				"warehouse": WH_FROM,
				"row_index": 0,
				"table_index": 0,
			}],
		})
		sr.insert(ignore_permissions=True)
	

		sr.reload()
		self.assertGreater(flt(sr.items[0].rate), 0,
			"Rate should be auto-fetched from last SLE when user enters 0")

	# ---------------------------------------------------------------
	# 14. Reconciliation keeps user-entered rate (doesn't overwrite)
	# ---------------------------------------------------------------
	def test_reconciliation_keeps_user_rate(self):
		"""If user enters a rate, it should NOT be overwritten by SLE rate."""
		sr = frappe.get_doc({
			"doctype": "Stock Reconciliation",
			"purpose": "Stock Reconciliation",
			"default_warehouse": WH_FROM,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"items": [{
				"item": ITEM_VARIANT,
				"qty": 80,
				"rate": 999,
				"uom": UOM,
				"warehouse": WH_FROM,
				"row_index": 0,
				"table_index": 0,
			}],
		})
		sr.insert(ignore_permissions=True)
	

		sr.reload()
		self.assertEqual(flt(sr.items[0].rate), 999,
			"User-entered rate should not be overwritten")

	# ---------------------------------------------------------------
	# 15. Reconciliation throws if no SLE and allow_zero not checked
	# ---------------------------------------------------------------
	def test_reconciliation_throws_when_no_sle_and_no_allow_zero(self):
		"""New item with no SLE and no rate → should throw error
		unless allow_zero_valuation_rate is checked."""
		sr = frappe.get_doc({
			"doctype": "Stock Reconciliation",
			"purpose": "Stock Reconciliation",
			"default_warehouse": WH_FROM,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"items": [{
				"item": ITEM_VARIANT,
				"qty": 10,
				"rate": 0,
				"uom": UOM,
				"warehouse": WH_FROM,
				"allow_zero_valuation_rate": 0,
				"row_index": 0,
				"table_index": 0,
			}],
		})
		# Clear all SLEs for this item so no rate can be found
		frappe.db.sql(
			"UPDATE `tabStock Ledger Entry` SET is_cancelled=1 WHERE item=%s",
			ITEM_VARIANT,
		)
	
		try:
			self.assertRaises(frappe.ValidationError, sr.insert, ignore_permissions=True)
		finally:
			# Restore SLEs
			frappe.db.sql(
				"UPDATE `tabStock Ledger Entry` SET is_cancelled=0 WHERE item=%s AND voucher_type!='Stock Reconciliation'",
				ITEM_VARIANT,
			)
		

	# ---------------------------------------------------------------
	# 16. Reconciliation allows zero rate when allow_zero is checked
	# ---------------------------------------------------------------
	def test_reconciliation_allows_zero_rate_with_flag(self):
		"""No SLE for item but allow_zero_valuation_rate checked
		should NOT throw — rate stays 0."""
		sr = frappe.get_doc({
			"doctype": "Stock Reconciliation",
			"purpose": "Stock Reconciliation",
			"default_warehouse": WH_FROM,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"items": [{
				"item": ITEM_VARIANT,
				"qty": 10,
				"rate": 0,
				"uom": UOM,
				"warehouse": WH_FROM,
				"allow_zero_valuation_rate": 1,
				"row_index": 0,
				"table_index": 0,
			}],
		})
		# Clear all SLEs for this item so no rate can be found
		frappe.db.sql(
			"UPDATE `tabStock Ledger Entry` SET is_cancelled=1 WHERE item=%s",
			ITEM_VARIANT,
		)
	
		try:
			sr.insert(ignore_permissions=True)
		
			self.assertEqual(flt(sr.items[0].rate), 0)
		finally:
			frappe.db.sql(
				"UPDATE `tabStock Ledger Entry` SET is_cancelled=0 WHERE item=%s AND voucher_type!='Stock Reconciliation'",
				ITEM_VARIANT,
			)
		
