"""Tests for the remaining GRN cancel guards:
  - validate_closed_purchase_order  (PO open_status == "Close")
  - validate_age_limit              (posting_date older than YRP Stock Settings window)

Downstream-consumption is intentionally handled by the stock engine's
NegativeStockError, not by a custom guard (see docs/claude/conventions.md).
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from yrp.yrp.doctype.yrp_goods_received_note.test_purchase_order_grn import (
	_default_received_type,
	_purchase_order,
	_purchase_order_grn,
	_warehouse,
)
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import (
	close_purchase_order,
	reopen_purchase_order,
)


def _set_cancel_window(days):
	"""Update the singleton and invalidate the value cached by get_single_value."""
	frappe.db.set_single_value('YRP YRP Stock Settings', "grn_cancel_window_days", days)
	frappe.clear_cache(doctype='YRP YRP Stock Settings')


class TestGRNCancelGuards(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()
		cls._original_window = frappe.db.get_single_value(
			'YRP YRP Stock Settings', "grn_cancel_window_days"
		)
		_set_cancel_window(0)

	@classmethod
	def tearDownClass(cls):
		_set_cancel_window(cls._original_window or 0)
		super().tearDownClass()

	# ---------- Closed Purchase Order guard ----------

	def test_01_cancel_allowed_when_po_open(self):
		warehouse = _warehouse(f"_T_Guard_Open_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _purchase_order_grn(po, qty=2)
		grn.submit()
		grn.reload()
		grn.cancel()  # should succeed
		self.assertEqual(grn.docstatus, 2)

	def test_02_cancel_blocked_when_po_closed(self):
		warehouse = _warehouse(f"_T_Guard_Closed_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _purchase_order_grn(po, qty=5)  # full receipt → eligible for close
		grn.submit()
		close_purchase_order(po.name)

		grn.reload()
		with self.assertRaisesRegex(frappe.ValidationError, "Purchase Order .* is closed"):
			grn.cancel()

		# Reopen and confirm cancel now works
		reopen_purchase_order(po.name)
		grn.reload()
		grn.cancel()
		self.assertEqual(grn.docstatus, 2)

	# ---------- Age-limit guard ----------

	def test_03_age_limit_disabled_by_default(self):
		warehouse = _warehouse(f"_T_Guard_AgeOff_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _purchase_order_grn(po, qty=2)
		# Backdate posting by 30 days; window=0 means guard is off
		grn.edit_posting_date_and_time = 1
		grn.posting_date = add_days(nowdate(), -30)
		grn.save(ignore_permissions=True)
		grn.submit()
		grn.reload()
		grn.cancel()
		self.assertEqual(grn.docstatus, 2)

	def test_04_age_limit_within_window_allowed(self):
		_set_cancel_window(30)
		try:
			warehouse = _warehouse(f"_T_Guard_AgeIn_{frappe.generate_hash(length=6)}")
			po = _purchase_order(qty=5, warehouse=warehouse)
			grn = _purchase_order_grn(po, qty=2)
			grn.edit_posting_date_and_time = 1
			grn.posting_date = add_days(nowdate(), -5)
			grn.save(ignore_permissions=True)
			grn.submit()
			grn.reload()
			grn.cancel()
			self.assertEqual(grn.docstatus, 2)
		finally:
			_set_cancel_window(0)

	def test_05_age_limit_outside_window_blocked(self):
		_set_cancel_window(7)
		try:
			warehouse = _warehouse(f"_T_Guard_AgeOut_{frappe.generate_hash(length=6)}")
			po = _purchase_order(qty=5, warehouse=warehouse)
			grn = _purchase_order_grn(po, qty=2)
			grn.edit_posting_date_and_time = 1
			grn.posting_date = add_days(nowdate(), -30)
			grn.save(ignore_permissions=True)
			grn.submit()
			grn.reload()
			self.assertEqual(
				int(frappe.db.get_single_value('YRP YRP Stock Settings', "grn_cancel_window_days")),
				7,
			)
			self.assertGreater((getdate(nowdate()) - getdate(grn.posting_date)).days, 7)
			with self.assertRaisesRegex(frappe.ValidationError, "posted .* days ago"):
				grn.cancel()
		finally:
			_set_cancel_window(0)
