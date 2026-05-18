"""Tests for Vendor Bill Tracking lifecycle and Purchase Invoice link mechanics."""

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


def _vbt(supplier=None, bill_no=None, bill_date=None, invoice_value=1000, received_via="HO"):
	doc = frappe.get_doc({
		"doctype": "Vendor Bill Tracking",
		"supplier": supplier or _supplier(f"_T_VBT_Sup_{frappe.generate_hash(length=6)}"),
		"bill_no": bill_no or f"BILL-{frappe.generate_hash(length=8)}",
		"bill_date": bill_date or nowdate(),
		"received_date": nowdate(),
		"invoice_value": invoice_value,
		"received_via": received_via,
	})
	doc.insert(ignore_permissions=True)
	return doc


def _build_pi_against_po(vbt, link=True):
	"""Builds a draft Purchase Invoice tied to a fresh PO+GRN and optionally
	linked to `vbt` via vendor_bill_tracking."""
	warehouse = _warehouse(f"_T_VBT_PIWH_{frappe.generate_hash(length=6)}")
	po = _purchase_order(qty=5, warehouse=warehouse)
	grn = _purchase_order_grn(po, qty=5)
	grn.submit()
	pi = frappe.get_doc({
		"doctype": "Purchase Invoice",
		"supplier": po.supplier,
		"billing_supplier": po.supplier,
		"bill_no": vbt.bill_no,
		"bill_date": vbt.bill_date,
		"vendor_bill_tracking": vbt.name if link else None,
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


class TestVendorBillTracking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()

	# ---------- Lifecycle ----------

	def test_01_submit_appends_open_history(self):
		vbt = _vbt()
		vbt.submit()
		vbt.reload()
		self.assertEqual(vbt.form_status, "Open")
		self.assertEqual(len(vbt.vendor_bill_tracking_history), 1)
		self.assertEqual(vbt.vendor_bill_tracking_history[0].action, "Open")

	def test_02_assign_updates_status_and_history(self):
		vbt = _vbt()
		vbt.submit()
		dept = _department("_T_VBT_Dept")

		assign_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.assign_vendor_bill"
		)
		assign_fn(vbt.name, dept, remarks="please process")

		vbt.reload()
		self.assertEqual(vbt.form_status, "Assigned")
		self.assertEqual(vbt.assigned_to, dept)
		actions = [r.action for r in vbt.vendor_bill_tracking_history]
		self.assertIn("Assign", actions)

	def test_03_assign_propagates_to_supplier_when_empty(self):
		supplier = _supplier(f"_T_VBT_PropSup_{frappe.generate_hash(length=6)}")
		frappe.db.set_value("Supplier", supplier, "department", None)
		vbt = _vbt(supplier=supplier)
		vbt.submit()
		dept = _department("_T_VBT_PropDept")

		assign_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.assign_vendor_bill"
		)
		assign_fn(vbt.name, dept)

		self.assertEqual(frappe.db.get_value("Supplier", supplier, "department"), dept)

	def test_04_close_sets_pi_link_and_status(self):
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.close_vendor_bill"
		)
		close_fn(vbt.name, pi.name, remarks="closed for test")
		vbt.reload()
		self.assertEqual(vbt.form_status, "Closed")
		self.assertEqual(vbt.purchase_invoice, pi.name)

	def test_05_reopen_clears_pi_and_flips_status(self):
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.close_vendor_bill"
		)
		close_fn(vbt.name, pi.name)

		reopen_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.reopen_vendor_bill"
		)
		reopen_fn(vbt.name, remarks="reopen test")
		vbt.reload()
		self.assertEqual(vbt.form_status, "Reopen")
		self.assertIsNone(vbt.purchase_invoice)

	def test_06_double_close_rejected(self):
		vbt = _vbt()
		vbt.submit()
		pi_a = _build_pi_against_po(vbt, link=False)
		pi_b = _build_pi_against_po(vbt, link=False)
		close_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.close_vendor_bill"
		)
		close_fn(vbt.name, pi_a.name)
		with self.assertRaisesRegex(frappe.ValidationError, "already closed"):
			close_fn(vbt.name, pi_b.name)

	def test_07_cancel_sets_status_and_history(self):
		vbt = _vbt()
		vbt.submit()
		cancel_fn = frappe.get_attr(
			"yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.cancel_vendor_bill"
		)
		cancel_fn(vbt.name, "duplicate bill")
		vbt.reload()
		self.assertEqual(vbt.form_status, "Cancelled")
		self.assertEqual(vbt.docstatus, 2)
		self.assertEqual(vbt.cancel_reason, "duplicate bill")

	# ---------- Unique-bill toggle ----------

	def test_08_unique_bill_toggle_enforces_per_supplier(self):
		from frappe.utils import add_days

		try:
			frappe.db.set_single_value("YRP Settings", "unique_vendor_bill_per_year", 1)
			frappe.db.set_single_value("YRP Settings", "fiscal_year_start_date", add_days(nowdate(), -30))
			frappe.db.set_single_value("YRP Settings", "fiscal_year_end_date", add_days(nowdate(), 30))

			sup = _supplier(f"_T_VBT_Uniq_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			_vbt(supplier=sup, bill_no=bill_no)
			with self.assertRaisesRegex(frappe.ValidationError, "already exist"):
				_vbt(supplier=sup, bill_no=bill_no)
		finally:
			frappe.db.set_single_value("YRP Settings", "unique_vendor_bill_per_year", 0)

	# ---------- Purchase Invoice link round-trip ----------

	def test_09_draft_pi_sets_link_without_closing(self):
		"""Draft PI insert sets VBT.purchase_invoice but does NOT close — closure
		only happens on PI submit."""
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=True)

		vbt.reload()
		self.assertEqual(vbt.purchase_invoice, pi.name)
		self.assertEqual(vbt.form_status, "Open")  # NOT Closed

	def test_10_purchase_invoice_trash_reverts_vbt(self):
		"""Deleting a draft PI clears VBT.purchase_invoice. Status was never
		flipped to Closed (draft), so no Reopen step needed — but link clears."""
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=True)
		# Unlink the GRN first so PI delete isn't blocked by LinkExistsError
		pi.set("grn", [])
		pi.save(ignore_permissions=True)
		pi.delete(ignore_permissions=True)

		vbt.reload()
		self.assertIsNone(vbt.purchase_invoice)

	def test_11_pi_submit_closes_vbt(self):
		"""Submitting the linked PI flips VBT to Closed and appends Close history."""
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=True)
		# WO-mode approval is not required for PO-mode submission
		pi.submit()

		vbt.reload()
		self.assertEqual(vbt.purchase_invoice, pi.name)
		self.assertEqual(vbt.form_status, "Closed")
		actions = [r.action for r in vbt.vendor_bill_tracking_history]
		self.assertIn("Close", actions)

	def test_12_pi_cancel_reverts_closed_vbt(self):
		"""After PI submit → Closed VBT, cancelling the PI reverts VBT to Reopen
		and clears the PI link."""
		vbt = _vbt()
		vbt.submit()
		pi = _build_pi_against_po(vbt, link=True)
		pi.submit()
		pi.reload()
		pi.cancel()

		vbt.reload()
		self.assertIsNone(vbt.purchase_invoice)
		self.assertEqual(vbt.form_status, "Reopen")
