"""Tests for Bill Tracking lifecycle and Purchase Invoice link mechanics."""

from unittest.mock import patch

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_years, nowdate

from yrp.yrp.doctype.yrp_goods_received_note.test_purchase_order_grn import (
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


def _received_via(value="HO"):
	if not frappe.db.exists("YRP Bill Tracking Received Via", value):
		frappe.get_doc(
			{
				"doctype": "YRP Bill Tracking Received Via",
				"received_via": value,
			}
		).insert(ignore_permissions=True)
	return value


def _bill(supplier=None, bill_no=None, bill_date=None, invoice_value=1000, received_via="HO"):
	doc = frappe.get_doc(
		{
			"doctype": "YRP Bill Tracking",
			"supplier": supplier or _supplier(f"_T_BT_Sup_{frappe.generate_hash(length=6)}"),
			"bill_no": bill_no or f"BILL-{frappe.generate_hash(length=8)}",
			"bill_date": bill_date or nowdate(),
			"received_date": nowdate(),
			"invoice_value": invoice_value,
			"received_via": received_via,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _unique_bill_setting(enabled):
	"""Patch YRP Settings.unique_vendor_bill_per_year without touching the site value."""
	original_get_single_value = frappe.db.get_single_value

	def get_single_value(doctype, fieldname, *args, **kwargs):
		if (doctype, fieldname) == ("YRP Settings", "unique_vendor_bill_per_year"):
			return enabled
		return original_get_single_value(doctype, fieldname, *args, **kwargs)

	return patch.object(frappe.db, "get_single_value", side_effect=get_single_value)


def _build_pi_against_po(bill, link=True):
	"""Builds a draft Purchase Invoice tied to a fresh PO+GRN and optionally
	linked to `bill` via bill_tracking."""
	warehouse = _warehouse(f"_T_BT_PIWH_{frappe.generate_hash(length=6)}")
	po = _purchase_order(qty=5, warehouse=warehouse, supplier=bill.supplier)
	grn = _purchase_order_grn(po, qty=5)
	grn.submit()
	fetch_method = "yrp.yrp.doctype.yrp_purchase_invoice.yrp_purchase_invoice.fetch_grn_details"
	if "essdee_yrp" in frappe.get_installed_apps():
		fetch_method = "essdee_yrp.purchase_invoice.fetch_grn_details"
	payload = frappe.get_attr(fetch_method)([grn.name], "Purchase Order", po.supplier)
	pi = frappe.get_doc(
		{
			"doctype": "YRP Purchase Invoice",
			"supplier": po.supplier,
			"billing_supplier": po.supplier,
			"bill_no": bill.bill_no,
			"bill_date": bill.bill_date,
			"bill_tracking": bill.name if link else None,
			"against": "Purchase Order",
			"against_id": po.name,
			"grn": [{"grn": grn.name}],
			"items": payload["items"],
			"pi_work_order_billed_details": payload.get("wo_items") or [],
			"total_quantity": payload["total_quantity"],
			**payload.get("additional_field_values", {}),
		}
	)
	pi.insert(ignore_permissions=True)
	return pi


class TestBillTracking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()
		for value in ("HO", "Post", "Email", "Warehouse", "Others"):
			_received_via(value)

	def test_00_erp_purchase_invoice_is_a_standard_link(self):
		field = frappe.get_meta("YRP Bill Tracking", cached=False).get_field("erp_purchase_invoice")
		self.assertEqual(
			(field.fieldtype, field.options, field.read_only, field.no_copy),
			("Link", "Purchase Invoice", 1, 1),
		)
		self.assertFalse(
			frappe.db.exists(
				"Custom Field",
				{
					"dt": "YRP Bill Tracking",
					"fieldname": "erp_purchase_invoice",
				},
			)
		)

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

		assign_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.assign_vendor_bill")
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

		assign_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.assign_vendor_bill")
		assign_fn(bill.name, dept)

		self.assertEqual(frappe.db.get_value("Supplier", supplier, "department"), dept)

	def test_04_close_sets_pi_link_and_status(self):
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.close_vendor_bill")
		close_fn(bill.name, pi.name, remarks="closed for test")
		bill.reload()
		self.assertEqual(bill.form_status, "Closed")
		self.assertEqual(bill.purchase_invoice, pi.name)

	def test_05_reopen_clears_pi_and_flips_status(self):
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.close_vendor_bill")
		close_fn(bill.name, pi.name)

		reopen_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.reopen_vendor_bill")
		reopen_fn(bill.name, remarks="reopen test")
		bill.reload()
		self.assertEqual(bill.form_status, "Reopen")
		self.assertIsNone(bill.purchase_invoice)

	def test_06_double_close_rejected(self):
		bill = _bill()
		bill.submit()
		pi_a = _build_pi_against_po(bill, link=False)
		pi_b = _build_pi_against_po(bill, link=False)
		close_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.close_vendor_bill")
		close_fn(bill.name, pi_a.name)
		with self.assertRaisesRegex(frappe.ValidationError, "already closed"):
			close_fn(bill.name, pi_b.name)

	def test_07_cancel_sets_status_and_history(self):
		bill = _bill()
		bill.submit()
		cancel_fn = frappe.get_attr("yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.cancel_vendor_bill")
		cancel_fn(bill.name, "duplicate bill")
		bill.reload()
		self.assertEqual(bill.form_status, "Cancelled")
		self.assertEqual(bill.docstatus, 2)
		self.assertEqual(bill.cancel_reason, "duplicate bill")

	# ---------- Unique bill no per supplier per fiscal year ----------

	def test_08_duplicate_bill_no_blocks_submit_in_same_fiscal_year(self):
		with _unique_bill_setting(1):
			supplier = _supplier(f"_T_BT_Uniq_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			first = _bill(supplier=supplier, bill_no=bill_no)
			first.submit()
			second = _bill(supplier=supplier, bill_no=f"  {bill_no.lower()} ")
			fiscal_year = get_fiscal_year(second.bill_date)[0]
			expected = f"Bill No {second.bill_no.strip()} already exists for supplier {supplier} in fiscal year {fiscal_year}"
			with self.assertRaisesRegex(frappe.ValidationError, expected) as raised:
				second.submit()
			self.assertIn(first.name, str(raised.exception))

	def test_08a_duplicate_draft_warns_without_blocking_save(self):
		with _unique_bill_setting(1):
			supplier = _supplier(f"_T_BT_Warn_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			_bill(supplier=supplier, bill_no=bill_no).submit()
			frappe.clear_messages()
			draft = _bill(supplier=supplier, bill_no=bill_no)
			self.assertEqual(draft.docstatus, 0)
			messages = " ".join(message.get("message", "") for message in frappe.get_message_log())
			self.assertIn(f"Bill No {bill_no} already exists", messages)

	def test_08b_drafts_cancelled_and_other_suppliers_do_not_block(self):
		with _unique_bill_setting(1):
			supplier = _supplier(f"_T_BT_Free_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			_bill(supplier=supplier, bill_no=bill_no)
			cancelled = _bill(supplier=supplier, bill_no=bill_no)
			cancelled.submit()
			frappe.db.set_value("YRP Bill Tracking", cancelled.name, "docstatus", 2)
			_bill(bill_no=bill_no).submit()
			_bill(supplier=supplier, bill_no=bill_no).submit()

	def test_08c_same_bill_no_allowed_in_another_fiscal_year(self):
		with _unique_bill_setting(1):
			supplier = _supplier(f"_T_BT_Year_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			bill_date = add_years(nowdate(), -2)
			_bill(supplier=supplier, bill_no=bill_no, bill_date=bill_date).submit()
			previous_year_date = add_days(get_fiscal_year(bill_date)[1], -1)
			_bill(supplier=supplier, bill_no=bill_no, bill_date=previous_year_date).submit()

	def test_08d_duplicate_allowed_when_setting_disabled(self):
		with _unique_bill_setting(0):
			supplier = _supplier(f"_T_BT_Off_{frappe.generate_hash(length=6)}")
			bill_no = f"DUP-{frappe.generate_hash(length=6)}"
			_bill(supplier=supplier, bill_no=bill_no).submit()
			frappe.clear_messages()
			second = _bill(supplier=supplier, bill_no=bill_no)
			self.assertFalse(frappe.get_message_log())
			second.submit()
			self.assertEqual(second.docstatus, 1)

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

	def test_13_linked_pi_must_match_bill_tracking_details(self):
		bill = _bill()
		bill.submit()
		pi = _build_pi_against_po(bill, link=True)

		pi.supplier = _supplier(f"_T_BT_Mismatch_{frappe.generate_hash(length=6)}")
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier must match"):
			pi.save(ignore_permissions=True)

		pi.reload()
		pi.bill_no = f"{bill.bill_no}-DIFF"
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier Invoice No must match"):
			pi.save(ignore_permissions=True)

		pi.reload()
		pi.bill_date = add_days(bill.bill_date, 1)
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier Invoice Date must match"):
			pi.save(ignore_permissions=True)
