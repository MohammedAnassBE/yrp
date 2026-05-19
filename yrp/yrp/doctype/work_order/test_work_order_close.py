import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.purchase_invoice.test_purchase_invoice import (
	_work_order_for_invoice,
	_work_order_grn,
)
from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_test_item_variant,
	_warehouse,
)


class TestWorkOrderClose(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.set_single_value("YRP Settings", "work_order_closing_approver_role", "System Manager")
		frappe.db.set_single_value("YRP Settings", "debit_approval_role", "System Manager")
		frappe.db.set_single_value("YRP Settings", "debit_request_role", "System Manager")

	def test_close_requires_submitted_grn(self):
		wo = _work_order_for_invoice(qty=1)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_attr("yrp.yrp.doctype.work_order.work_order.update_stock")(wo.name)

	def test_unapproved_debit_blocks_close_until_approved(self):
		wo = _work_order_for_invoice(qty=1)
		_work_order_grn(wo, qty=1)

		debit = frappe.get_attr("yrp.yrp.doctype.debit.debit.create_debit")(
			wo.name,
			debit_no=f"DEBIT-{frappe.generate_hash(length=6)}",
			debit_value=5,
			reason="Test close debit",
			on_close=1,
		)
		frappe.db.set_value("Debit", debit.name, "status", "Debit Requested")

		with self.assertRaises(frappe.ValidationError):
			frappe.get_attr("yrp.yrp.doctype.work_order.work_order.update_stock")(wo.name)

		frappe.get_attr("yrp.yrp.doctype.debit.debit.approve_debit")(debit.name)
		status = frappe.get_attr("yrp.yrp.doctype.work_order.work_order.update_stock")(
			wo.name,
			close_reason="Others",
			close_other_reason="Test",
			close_remarks="Closed from regression test",
		)

		wo.reload()
		self.assertEqual(status, "Close")
		self.assertEqual(wo.open_status, "Close")
		self.assertEqual(wo.is_delivered, 1)


class TestWorkOrderCancelReservationCleanup(FrappeTestCase):
	"""Cancelling a Work Order cancels every active Stock Reservation Entry
	(SRE) linked to it. The previous behavior only auto-closed reservations on
	explicit close; the cancellation path left them orphaned."""

	def _make_sre(self, wo, qty):
		"""Create + submit a Stock Reservation Entry against a WO. Seeds the
		warehouse with stock first so the SRE's live-availability check passes.
		"""
		from frappe.utils import nowdate, nowtime
		from yrp.stock.dimensions import get_mandatory_dimensions

		item_variant = _test_item_variant()
		parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
		uom = frappe.db.get_value("Item", parent_item, "default_unit_of_measure") or "Piece"
		wh = _warehouse(f"_Test WO Cancel SRE WH {frappe.generate_hash(length=6)}")

		# Fill mandatory stock dimensions from the site's current config so the
		# SRE saves regardless of which dims are flagged mandatory locally.
		dim_values = {}
		for d in get_mandatory_dimensions():
			fn = d["fieldname"]
			if fn == "received_type":
				dim_values[fn] = "Accepted"
				continue
			target_dt = d.get("dimension_doctype")
			if target_dt:
				existing = frappe.db.get_value(target_dt, {}, "name")
				if existing:
					dim_values[fn] = existing

		# Seed stock in the bin so SRE's before_submit live check passes.
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Receipt",
			"to_warehouse": wh,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"items": [{
				"item": item_variant,
				"qty": qty * 2,
				"rate": 1,
				"uom": uom,
				"row_index": 0,
				"table_index": 0,
				**dim_values,
			}],
		})
		se.flags.ignore_permissions = True
		se.insert(ignore_permissions=True)
		se.submit()

		sre = frappe.get_doc({
			"doctype": "Stock Reservation Entry",
			"item_code": item_variant,
			"warehouse": wh,
			"reserved_qty": qty,
			"available_qty": 9999,
			"voucher_type": "Work Order",
			"voucher_no": wo.name,
			**dim_values,
		})
		sre.flags.ignore_permissions = True
		sre.flags.ignore_links = True
		sre.insert(ignore_permissions=True)
		sre.flags.ignore_links = True
		sre.submit()
		return sre

	def test_cancel_cancels_linked_reservations(self):
		wo = _work_order_for_invoice(qty=1)
		sre = self._make_sre(wo, qty=1)
		self.assertEqual(sre.docstatus, 1)

		wo.reload()
		wo.cancel()

		sre.reload()
		self.assertEqual(sre.docstatus, 2)
		self.assertEqual(sre.status, "Cancelled")

	def test_cancel_skips_already_delivered_reservations(self):
		"""An SRE that's already Delivered/Cancelled is left alone (the helper
		filters on `status NOT IN ('Delivered','Cancelled')`)."""
		wo = _work_order_for_invoice(qty=1)
		sre = self._make_sre(wo, qty=1)
		frappe.db.set_value("Stock Reservation Entry", sre.name, "status", "Delivered")

		wo.reload()
		wo.cancel()

		sre.reload()
		self.assertEqual(sre.docstatus, 1)  # still submitted, untouched
		self.assertEqual(sre.status, "Delivered")
