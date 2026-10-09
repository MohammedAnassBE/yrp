from unittest.mock import patch

import frappe
from frappe.utils import get_datetime

from yrp.yrp_sales.doctype.yrp_delivery_note.yrp_delivery_note import amend_delivery_note
from yrp.yrp_sales.doctype.yrp_packing_slip.yrp_packing_slip import make_packing_slip
from yrp.yrp_sales.invoicing import get_sales_invoice, preview_sales_invoice
from yrp.yrp_sales.test_invoicing import DeliveryNoteFixtures


class TestPackingLifecycle(DeliveryNoteFixtures):
	def get_note(self, note):
		return frappe.get_doc("YRP Delivery Note", note.name)

	def test_packing_stages_follow_in_order(self):
		note = self.make_note(6)
		with self.assertRaisesRegex(frappe.ValidationError, "has not been initiated"):
			self.get_note(note).complete_packing("Fictional Packer")
		self.get_note(note).initiate_packing()
		with self.assertRaisesRegex(frappe.ValidationError, "already started"):
			self.get_note(note).initiate_packing()
		with self.assertRaisesRegex(frappe.ValidationError, "completed by is required"):
			self.get_note(note).complete_packing(" ")
		self.get_note(note).complete_packing("Fictional Packer")
		note.reload()
		self.assertEqual(
			(note.packing_status, note.packing_completed_by), ("Packing Completed", "Fictional Packer")
		)
		self.assertTrue(note.packing_initiated_at and note.packing_completed_at)

	def test_initiate_needs_a_submitted_uninvoiced_note(self):
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			self.get_note(self.make_note(2, submit=False)).initiate_packing()
		note = self.make_note(6)
		self.invoice(note, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "already invoiced"):
			self.get_note(note).initiate_packing()

	def test_invoice_status_follows_live_invoices(self):
		note = self.make_note(6)
		self.assertFalse(self.get_note(note).invoice_status)
		draft = self.invoice(note, submit=False)
		self.assertEqual(self.get_note(note).invoice_status, "Draft")
		draft.delete()
		self.assertFalse(self.get_note(note).invoice_status)
		invoice = self.invoice(note)
		self.assertEqual(self.get_note(note).invoice_status, "Submitted")
		invoice.cancel()
		self.assertFalse(self.get_note(note).invoice_status)

	def test_cancel_with_reason_cancels_packing_slips(self):
		note = self.make_note(6)
		slip = make_packing_slip(note.name)
		slip.insert()
		slip.submit()
		with self.assertRaisesRegex(frappe.ValidationError, "reason is required"):
			self.get_note(note).cancel_with_reason("")
		self.get_note(note).cancel_with_reason(" Goods Not Available ")
		note.reload()
		self.assertEqual((note.docstatus, note.cancel_reason), (2, "Goods Not Available"))
		self.assertEqual(frappe.db.get_value("YRP Packing Slip", slip.name, "docstatus"), 2)
		self.assertEqual(self.get_bin(), (10, 0))

	def test_amend_replaces_the_note_atomically(self):
		note = self.make_note(6)
		row = note.items[0]
		items = [{"so_detail": row.so_detail, "qty": 4}]
		amended = amend_delivery_note(note.name, self.customer, [row.sales_order], items)
		self.assertEqual(
			(amended.name, amended.amended_from, amended.docstatus), (f"{note.name}-1", note.name, 1)
		)
		self.assertEqual((self.get_note(note).docstatus, [r.qty for r in amended.items]), (2, [4]))
		self.assertEqual(self.get_bin(), (10, 4))
		too_many = [{"so_detail": row.so_detail, "qty": 7}]
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds pending"):
			amend_delivery_note(amended.name, self.customer, [row.sales_order], too_many)
		self.assertEqual(
			(frappe.db.get_value("YRP Delivery Note", amended.name, "docstatus"), self.get_bin()),
			(1, (10, 4)),
		)

	def test_amend_is_blocked_by_a_live_invoice(self):
		note = self.make_note(6)
		self.invoice(note, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "Cancel Sales Invoices"):
			amend_delivery_note(note.name, self.customer, [note.items[0].sales_order])
		self.assertEqual(frappe.db.get_value("YRP Delivery Note", note.name, "docstatus"), 1)

	def test_rates_change_only_before_invoicing(self):
		note = self.make_note(6)
		row = note.items[0].name
		self.get_note(note).update_rates({row: 30})
		note.reload()
		self.assertEqual((note.items[0].rate, note.items[0].amount, note.total), (30, 180, 180))
		with self.assertRaisesRegex(frappe.ValidationError, "greater than zero"):
			self.get_note(note).update_rates({row: 0})
		self.assertEqual(self.invoice(note, submit=False).items[0].rate, 30)
		with self.assertRaisesRegex(frappe.ValidationError, "after it is invoiced"):
			self.get_note(note).update_rates({row: 35})

	def test_preview_keeps_nothing(self):
		note = self.make_note(6)
		preview = preview_sales_invoice(note.name)
		self.assertEqual((preview["total"], preview["grand_total"]), (150, 150))
		self.assertFalse(frappe.db.exists("Sales Invoice", {"yrp_delivery_note": note.name}))
		self.assertFalse(self.get_note(note).invoice_status)

	def test_mappers_adjust_the_invoice(self):
		note = self.make_note(6)
		calls = []
		original = frappe.get_hooks

		def get_hooks(hook=None, *args, **kwargs):
			if hook == "yrp_sales_invoice_mappers":
				return ["yrp.yrp_sales.test_packing_lifecycle.remark_invoice"]
			return original(hook, *args, **kwargs)

		with patch("frappe.get_hooks", get_hooks):
			invoice = get_sales_invoice(note.name)
			calls.append(invoice.remarks)
		self.assertEqual(calls, [f"Fictional mapper for {note.name}"])

	def test_deliver_records_the_chosen_time_and_remarks(self):
		note = self.make_note(6)
		self.invoice(note)
		self.get_note(note).deliver("2026-01-02 10:00:00", " Left at the dock ")
		note.reload()
		self.assertEqual(
			(note.status, get_datetime(note.delivered_at), note.delivery_remarks),
			("Delivered", get_datetime("2026-01-02 10:00:00"), "Left at the dock"),
		)

	def test_billing_roles_only_read_delivery_notes(self):
		for role in ("Billing User", "Accounts Manager"):
			perm = frappe.get_all(
				"DocPerm",
				filters={"parent": "YRP Delivery Note", "role": role, "permlevel": 0},
				fields=["read", "write", "create", "delete", "submit", "cancel", "amend", "share"],
			)
			self.assertEqual(
				perm,
				[{"read": 1, "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0, "amend": 0, "share": 0}],
				role,
			)


def remark_invoice(invoice, note):
	invoice.remarks = f"Fictional mapper for {note.name}"
