import frappe

from yrp.yrp_sales.cartons import (
	build_packing_slips,
	get_packing_slip_workspace,
	split_into_cartons,
	split_number_into_parts,
)
from yrp.yrp_sales.test_invoicing import DeliveryNoteFixtures


class TestCartons(DeliveryNoteFixtures):
	def cartons(self, note, *cartons):
		"""Cartons of `(row index, qty)` pairs against the note's rows."""
		return [
			[{"dn_detail": note.items[index].name, "qty": qty} for index, qty in carton] for carton in cartons
		]

	def get_slips(self, note):
		return [
			(slip.carton_no, slip.status, [(row.dn_detail, row.qty) for row in slip["items"]])
			for slip in get_packing_slip_workspace(note.name)["packing_slips"]
		]

	def test_split_spreads_the_remainder_over_the_first_parts(self):
		self.assertEqual(split_number_into_parts(7, 3), [3, 2, 2])
		self.assertEqual(split_number_into_parts(2, 3), [1, 1, 0])

	def test_split_into_cartons_takes_rows_in_order_and_drops_empty_cartons(self):
		lines = [
			{"carton_nos": [1, 2], "rows": [{"dn_detail": "a", "qty": 3}, {"dn_detail": "b", "qty": 2}]},
			{"carton_nos": [3, 4], "rows": [{"dn_detail": "c", "qty": 1}]},
		]
		self.assertEqual(
			split_into_cartons(lines),
			[
				[{"dn_detail": "a", "qty": 3}],
				[{"dn_detail": "b", "qty": 2}],
				[{"dn_detail": "c", "qty": 1}],
			],
		)

	def test_build_makes_one_numbered_submitted_slip_per_carton(self):
		note = self.make_note(4, 2)
		build_packing_slips(note.name, self.cartons(note, [(0, 3)], [(0, 1), (1, 2)]))
		self.assertEqual(
			self.get_slips(note),
			[
				(1, "Packed", [(note.items[0].name, 3)]),
				(2, "Packed", [(note.items[0].name, 1), (note.items[1].name, 2)]),
			],
		)
		note.reload()
		self.assertEqual((note.per_packed, [row.packed_qty for row in note.items]), (100, [4, 2]))

	def test_resave_replaces_the_cartons(self):
		note = self.make_note(4)
		first = build_packing_slips(note.name, self.cartons(note, [(0, 2)], [(0, 2)]))
		second = build_packing_slips(note.name, self.cartons(note, [(0, 1)], [(0, 1)], [(0, 2)]))
		self.assertEqual({frappe.db.get_value("YRP Packing Slip", name, "docstatus") for name in first}, {2})
		self.assertEqual([carton for carton, *_ in self.get_slips(note)], [1, 2, 3])
		self.assertEqual(len(second), 3)
		note.reload()
		self.assertEqual(note.per_packed, 100)

	def test_cartons_must_hold_every_quantity_exactly(self):
		note = self.make_note(4, 2)
		for cartons in (
			self.cartons(note, [(0, 4)]),
			self.cartons(note, [(0, 5), (1, 2)]),
			self.cartons(note, [(0, 4), (1, 1)], [(1, 2)]),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Move every packing-slip quantity"):
				build_packing_slips(note.name, cartons)
		with self.assertRaisesRegex(frappe.ValidationError, "Carton 2 has no items"):
			build_packing_slips(note.name, [*self.cartons(note, [(0, 4), (1, 2)]), []])
		self.assertEqual(self.get_slips(note), [])

	def test_delivered_cartons_cannot_change(self):
		note = self.make_note(4)
		build_packing_slips(note.name, self.cartons(note, [(0, 4)]))
		self.invoice(note)
		frappe.get_doc("YRP Delivery Note", note.name).deliver()
		with self.assertRaisesRegex(frappe.ValidationError, "already delivered"):
			build_packing_slips(note.name, self.cartons(note, [(0, 2)], [(0, 2)]))
		self.assertEqual(self.get_slips(note), [(1, "Delivered", [(note.items[0].name, 4)])])

	def test_carton_numbers_are_unique_per_note(self):
		note = self.make_note(4)
		build_packing_slips(note.name, self.cartons(note, [(0, 2)], [(0, 2)]))
		slip = frappe.get_doc(
			{
				"doctype": "YRP Packing Slip",
				"delivery_note": note.name,
				"posting_date": frappe.utils.nowdate(),
				"carton_no": 2,
				"items": [{"dn_detail": note.items[0].name, "qty": 1}],
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Carton 2 already exists"):
			slip.insert()
