"""Tests for Bill Tracking lifecycle and Purchase Invoice link mechanics."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
	_purchase_order,
	_purchase_order_grn,
	_supplier,
	_warehouse,
)


def _department(name_prefix):
	name = f"{name_prefix}_{frappe.generate_hash(length=6)}"
	doc = frappe.get_doc({"doctype": "Department", "department_name": name})
	doc.insert(ignore_permissions=True)
	return doc.name


def _bill(supplier=None, bill_no=None, bill_date=None, invoice_value=1000, received_via="HO"):
	doc = frappe.get_doc({
		"doctype": "Bill Tracking",
		"supplier": supplier or _supplier(f"_T_BT_Sup_{frappe.generate_hash(length=6)}"),
		"bill_no": bill_no or f"BILL-{frappe.generate_hash(length=8)}",
		"bill_date": bill_date or nowdate(),
		"received_date": nowdate(),
		"invoice_value": invoice_value,
		"received_via": received_via,
	})
	doc.insert(ignore_permissions=True)
	return doc


def _build_pi_against_po(bill, link=True):
	"""Builds a draft Purchase Invoice tied to a fresh PO+GRN and optionally
	linked to `bill` via bill_tracking."""
	warehouse = _warehouse(f"_T_BT_PIWH_{frappe.generate_hash(length=6)}")
	po = _purchase_order(qty=5, warehouse=warehouse)
	grn = _purchase_order_grn(po, qty=5)
	grn.submit()
	pi = frappe.get_doc({
		"doctype": "Purchase Invoice",
		"supplier": po.supplier,
		"billing_supplier": po.supplier,
		"bill_no": bill.bill_no,
		"bill_date": bill.bill_date,
		"bill_tracking": bill.name if link else None,
		"against": "Purchase Order",
		"against_id": po.name,
		"grn": [{"grn": grn.name}],
		"items": [{
			"item": grn.items[0].item_variant,
			"qty": grn.items[0].quantity,
			"uom": grn.items[0].uom,
			"rate": grn.items[0].rate,
		}],
	})
	pi.insert(ignore_permissions=True)
	return pi


class TestBillTracking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()

	# ---------- Lifecycle ----------

	def test_01_submit_appends_open_history(self):
		bill = _bill()
		bill.submit()
		bill.reload()
		self.assertEqual(bill.form_status, "Open")
		self.assertEqual(len(bill.bill_tracking_history), 1)
		self.assertEqual(bill.bill_tracking_history[0].action, "Open")

	def test_02_assign_updates_status_and_history(self):
		bill = _bill()
		bill.submit()
		dept = _department("_T_BT_Dept")

		assign_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.assign_vendor_bill"
		)
		assign_fn(bill.name, dept, remarks="please process")

		bill.reload()
		self.assertEqual(bill.form_status, "Assigned")
		self.assertEqual(bill.assigned_to, dept)
		actions = [r.action for r in bill.bill_tracking_history]
		self.assertIn("Assign", actions)

	def test_03_assign_propagates_to_supplier_when_empty(self):
		supplier = _supplier(f"_T_BT_PropSup_{frappe.generate_hash(length=6)}")
		frappe.db.set_value("Supplier", supplier, "department", None)
		bill = _bill(supplier=supplier)
		bill.submit()
		dept = _department("_T_BT_PropDept")

		assign_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.assign_vendor_bill"
		)
		assign_fn(bill.name, dept)

		self.assertEqual(frappe.db.get_value("Supplier", supplier, "department"), dept)

	def test_04_close_sets_pi_link_and_status(self):
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.close_vendor_bill"
		)
		close_fn(bill.name, pi.name, remarks="closed for test")
		bill.reload()
		self.assertEqual(bill.form_status, "Closed")
		self.assertEqual(bill.purchase_invoice, pi.name)

	def test_05_reopen_clears_pi_and_flips_status(self):
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.close_vendor_bill"
		)
		close_fn(bill.name, pi.name)

		reopen_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.reopen_vendor_bill"
		)
		reopen_fn(bill.name, remarks="reopen test")
		bill.reload()
		self.assertEqual(bill.form_status, "Reopen")
		self.assertIsNone(bill.purchase_invoice)

	def test_06_double_close_rejected(self):
		bill = _bill()
		bill.submit()
		pi_a = _build_pi_against_po(bill, link=False)
		pi_b = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.close_vendor_bill"
		)
		close_fn(bill.name, pi_a.name)
		with self.assertRaisesRegex(frappe.ValidationError, "already closed"):
			close_fn(bill.name, pi_b.name)

	def test_07_cancel_sets_status_and_history(self):
		bill = _bill()
		bill.submit()
		cancel_fn = frappe.get_attr(
			"yrp.yrp.doctype.bill_tracking.bill_tracking.cancel_vendor_bill"
		)
		cancel_fn(bill.name, "duplicate bill")
		bill.reload()
		self.assertEqual(bill.form_status, "Cancelled")
		self.assertEqual(bill.docstatus, 2)
		self.assertEqual(bill.cancel_reason, "duplicate bill")

	# ---------- Unique-bill toggle ----------

	def test_08_unique_bill_toggle_enforces_per_supplier(self):
		from frappe.utils import add_days

		try:
			frappe.db.set_single_value("YRP Settings", "unique_vendor_bill_per_year", 1)
			frappe.db.set_single_value("YRP Settings", "fiscal_year_start_date", add_days(nowdate(), -30))
			frappe.db.set_single_value("YRP Settings", "fiscal_year_end_date", add_days(nowdate(), 30))

			sup = _supplier(f"_T_BT_Uniq_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			_bill(supplier=sup, bill_no=bill_no)
			with self.assertRaisesRegex(frappe.ValidationError, "already exist"):
				_bill(supplier=sup, bill_no=bill_no)
		finally:
			frappe.db.set_single_value("YRP Settings", "unique_vendor_bill_per_year", 0)

	# ---------- Purchase Invoice link round-trip ----------

	def test_09_draft_pi_sets_link_without_closing(self):
		"""Draft PI insert sets Bill Tracking.purchase_invoice but does NOT close — closure
		only happens on PI submit."""
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=True)

		bill.reload()
		self.assertEqual(bill.purchase_invoice, pi.name)
		self.assertEqual(bill.form_status, "Open")  # NOT Closed

	def test_10_purchase_invoice_trash_reverts_bill(self):
		"""Deleting a draft PI clears Bill Tracking.purchase_invoice. Status was never
		flipped to Closed (draft), so no Reopen step needed — but link clears."""
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=True)
		# Unlink the GRN first so PI delete isn't blocked by LinkExistsError
		pi.set("grn", [])
		pi.save(ignore_permissions=True)
		pi.delete(ignore_permissions=True)

		bill.reload()
		self.assertIsNone(bill.purchase_invoice)

	def test_11_pi_submit_closes_bill(self):
		"""Submitting the linked PI flips Bill Tracking to Closed and appends Close history."""
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=True)
		# WO-mode approval is not required for PO-mode submission
		pi.submit()

		bill.reload()
		self.assertEqual(bill.purchase_invoice, pi.name)
		self.assertEqual(bill.form_status, "Closed")
		actions = [r.action for r in bill.bill_tracking_history]
		self.assertIn("Close", actions)

	def test_12_pi_cancel_reverts_closed_bill(self):
		"""After PI submit → Closed Bill Tracking, cancelling the PI reverts Bill Tracking to Reopen
		and clears the PI link."""
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=True)
		pi.submit()
		pi.reload()
		pi.cancel()

		bill.reload()
		self.assertIsNone(bill.purchase_invoice)
		self.assertEqual(bill.form_status, "Reopen")
