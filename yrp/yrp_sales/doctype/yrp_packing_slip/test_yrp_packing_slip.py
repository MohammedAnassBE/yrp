# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import frappe

from yrp.yrp_sales.doctype.yrp_packing_slip.yrp_packing_slip import make_packing_slip
from yrp.yrp_sales.test_invoicing import DeliveryNoteFixtures


class TestYRPPackingSlip(DeliveryNoteFixtures):
	def pack(self, note, *quantities, submit=True):
		"""Pack ``quantities`` against the note's rows in order; zero leaves a row out."""
		slip = make_packing_slip(note.name)
		rows = {row.dn_detail: row for row in slip.items}
		slip.items = []
		for source, qty in zip(note.items, quantities, strict=False):
			if qty:
				slip.append("items", {**rows[source.name].as_dict(no_default_fields=True), "qty": qty})
		slip.insert()
		if submit:
			slip.submit()
		return slip

	def deliver(self, doc):
		frappe.get_doc(doc.doctype, doc.name).mark_delivered()
		doc.reload()

	def get_delivery(self, note):
		note.reload()
		order = frappe.get_doc("YRP Sales Order", note.items[0].sales_order)
		return (note.status, note.per_delivered, [row.delivered_qty for row in note.items],
			order.status, order.items[0].delivered_qty)

	def test_mapper_offers_unpacked_quantity(self):
		note = self.make_note(4, 6)
		self.pack(note, 1, 2)
		self.pack(note, 0, 3, submit=False)
		self.assertEqual([row.qty for row in make_packing_slip(note.name).items], [3, 1])
		note.reload()
		self.assertEqual(([row.packed_qty for row in note.items], note.per_packed), ([1, 2], 30))

	def test_over_packing_across_slips_is_rejected(self):
		note = self.make_note(6)
		self.pack(note, 4, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "would be packed"):
			self.pack(note, 3)
		slip = make_packing_slip(note.name)
		slip.append("items", slip.items[0].as_dict(no_default_fields=True))
		with self.assertRaisesRegex(frappe.ValidationError, "would be packed"):
			slip.insert()

	def test_slip_rows_follow_the_note(self):
		note = self.make_note(6)
		slip = make_packing_slip(note.name)
		slip.items[0].update({"item_code": None, "item_name": "Wrong", "uom": None, "qty": 2})
		slip.insert()
		row = slip.items[0]
		self.assertEqual((row.item_code, row.uom, row.stock_qty, slip.customer), (self.item, self.uom, 2, self.customer))
		slip.items[0].dn_detail = "missing-row"
		with self.assertRaisesRegex(frappe.ValidationError, "must reference a row"):
			slip.save()

	def test_slip_requires_a_submitted_note(self):
		draft = self.make_note(2, submit=False)
		slip = frappe.get_doc({"doctype": "YRP Packing Slip", "delivery_note": draft.name,
			"items": [{"dn_detail": draft.items[0].name, "qty": 1}]})
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			slip.insert()

	def test_slip_deliveries_roll_up_to_note_and_order(self):
		note = self.make_note(6)
		self.invoice(note)
		first, second = self.pack(note, 2), self.pack(note, 4)
		self.deliver(first)
		self.assertEqual((first.status, bool(first.delivered_at)), ("Delivered", True))
		self.assertEqual(self.get_delivery(note), ("Partially Delivered", 33.333, [2], "Partially Delivered", 2))
		self.assertFalse(note.delivered_at)
		self.deliver(second)
		self.assertEqual(self.get_delivery(note), ("Delivered", 100, [6], "Delivered", 6))
		self.assertEqual(note.delivered_at, second.delivered_at)

	def test_note_without_slips_is_delivered_whole(self):
		note = self.make_note(6)
		self.invoice(note)
		self.deliver(note)
		self.assertEqual(self.get_delivery(note), ("Delivered", 100, [6], "Delivered", 6))
		self.assertTrue(note.delivered_at)
		with self.assertRaisesRegex(frappe.ValidationError, "already delivered"):
			self.deliver(note)

	def test_note_delivery_delivers_covering_slips(self):
		note = self.make_note(6)
		self.invoice(note)
		first, second = self.pack(note, 2), self.pack(note, 4)
		self.deliver(first)
		self.deliver(note)
		second.reload()
		self.assertEqual((second.status, bool(second.delivered_at)), ("Delivered", True))
		self.assertEqual(self.get_delivery(note), ("Delivered", 100, [6], "Delivered", 6))

	def test_note_delivery_is_blocked_by_incomplete_or_draft_slips(self):
		note = self.make_note(6)
		self.invoice(note)
		slip = self.pack(note, 2)
		with self.assertRaisesRegex(frappe.ValidationError, "Packing Slips cover 2"):
			self.deliver(note)
		draft = self.pack(note, 4, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "draft Packing Slips"):
			self.deliver(note)
		draft.submit()
		self.deliver(note)
		slip.reload()
		self.assertEqual(slip.status, "Delivered")

	def test_delivered_slip_and_its_note_cannot_be_cancelled(self):
		note = self.make_note(6)
		self.invoice(note)
		slip = self.pack(note, 6)
		self.deliver(slip)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be cancelled"):
			frappe.get_doc("YRP Packing Slip", slip.name).cancel()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("YRP Delivery Note", note.name).cancel()

	def test_packed_slip_can_be_cancelled_and_blocks_note_cancel(self):
		note = self.make_note(6)
		slip = self.pack(note, 6)
		with self.assertRaisesRegex(frappe.ValidationError, "Cancel Packing Slips"):
			frappe.get_doc("YRP Delivery Note", note.name).cancel()
		slip.cancel()
		note.reload()
		self.assertEqual((slip.status, note.items[0].packed_qty, note.per_packed), ("Cancelled", 0, 0))
		self.assertEqual(make_packing_slip(note.name).items[0].qty, 6)

	def test_delivered_at_cannot_be_cleared(self):
		note = self.make_note(6)
		self.invoice(note)
		slip = self.pack(note, 6)
		self.deliver(slip)
		slip.delivered_at = None
		with self.assertRaisesRegex(frappe.ValidationError, "Use Mark Delivered"):
			slip.save()

	def test_new_slip_on_directly_delivered_note_is_rejected(self):
		note = self.make_note(6)
		self.invoice(note)
		self.deliver(note)
		with self.assertRaisesRegex(frappe.ValidationError, "already delivered"):
			make_packing_slip(note.name)
		slip = frappe.get_doc({"doctype": "YRP Packing Slip", "delivery_note": note.name,
			"items": [{"dn_detail": note.items[0].name, "qty": 1}]})
		with self.assertRaisesRegex(frappe.ValidationError, "already delivered"):
			slip.insert()

	def test_delivery_beyond_billed_quantity_is_rejected(self):
		note = self.make_note(6)
		self.invoice(note, {note.items[0].name: 3})
		with self.assertRaisesRegex(frappe.ValidationError, "6.0 would be delivered, but only 3.0 is invoiced"):
			self.deliver(note)
		first, second = self.pack(note, 3), self.pack(note, 1)
		self.deliver(first)
		with self.assertRaisesRegex(frappe.ValidationError, "4.0 would be delivered, but only 3.0 is invoiced"):
			self.deliver(second)
		self.invoice(note)
		self.deliver(second)
		self.assertEqual(self.get_delivery(note)[:3], ("Partially Delivered", 66.667, [4]))

	def test_read_only_roles_cannot_change(self):
		for role in ["Sales Manager", "Sales User", "Accounts User"]:
			user = frappe.get_doc({"doctype": "User", "email": frappe.generate_hash(length=10) + "@example.invalid",
				"first_name": "Fictional Reader", "send_welcome_email": 0, "roles": [{"role": role}]}).insert().name
			self.assertTrue(frappe.has_permission("YRP Packing Slip", "read", user=user), role)
			for ptype in ("create", "write", "delete", "submit", "cancel"):
				self.assertFalse(frappe.has_permission("YRP Packing Slip", ptype, user=user), (role, ptype))
