import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.purchase_invoice.test_purchase_invoice import (
	_work_order_for_invoice,
	_work_order_grn,
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

		debit = frappe.get_attr("yrp.yrp.doctype.essdee_debit.essdee_debit.create_debit")(
			wo.name,
			debit_no=f"DEBIT-{frappe.generate_hash(length=6)}",
			debit_value=5,
			reason="Test close debit",
			on_close=1,
		)
		frappe.db.set_value("Debit", debit.name, "status", "Debit Requested")

		with self.assertRaises(frappe.ValidationError):
			frappe.get_attr("yrp.yrp.doctype.work_order.work_order.update_stock")(wo.name)

		frappe.get_attr("yrp.yrp.doctype.essdee_debit.essdee_debit.approve_debit")(debit.name)
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
