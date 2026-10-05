# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import secrets
import unittest

import frappe
from frappe.utils import nowdate

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_or_make_bin
from yrp.yrp_sales.doctype.yrp_delivery_note.yrp_delivery_note import make_delivery_note


class TestYRPDeliveryNote(unittest.TestCase):
	def setUp(self):
		point = "yrp_delivery_note_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.addCleanup(frappe.db.rollback, save_point=point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.company = frappe.db.get_value("Company", {}, "name")
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert().name
		self.item = self.make_item()
		self.customer = self.make_customer()
		self.warehouse = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": self.label("Store"),
			"company": self.company}).insert().name
		self.dimensions = self.get_dimension_values()
		self.receive(10)

	def label(self, label):
		return "Test " + label + " " + frappe.generate_hash(length=10)

	def make_item(self):
		group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		hsn = frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
			"description": "Fictional delivery note test"}).insert()
		return frappe.get_doc({"doctype": "Item", "item_code": self.label("Item"), "item_group": group.name,
			"stock_uom": self.uom, "is_stock_item": 1, "is_sales_item": 1, "gst_hsn_code": hsn.name}).insert().name

	def make_customer(self):
		customer_group = frappe.get_doc({"doctype": "Customer Group", "customer_group_name": self.label("Customer Group"),
			"parent_customer_group": frappe.db.get_value("Customer Group", {"lft": 1}, "name"), "is_group": 0}).insert()
		territory = frappe.get_doc({"doctype": "Territory", "territory_name": self.label("Territory"),
			"parent_territory": frappe.db.get_value("Territory", {"lft": 1}, "name"), "is_group": 0}).insert()
		return frappe.get_doc({"doctype": "Customer", "customer_name": self.label("Customer"),
			"customer_type": "Individual", "customer_group": customer_group.name,
			"territory": territory.name}).insert().name

	def get_dimension_values(self):
		values = {}
		for dimension in get_stock_dimensions():
			value = frappe.db.get_value(dimension["dimension_doctype"], {}, "name")
			if dimension["fieldname"] == "received_type":
				value = frappe.db.get_single_value("YRP Stock Settings", "default_received_type") or value
			values[dimension["fieldname"]] = value
		return values

	def receive(self, qty):
		entry = frappe.get_doc({"doctype": "YRP Stock Entry", "purpose": "Material Receipt",
			"posting_date": nowdate(), "to_warehouse": self.warehouse, "items": [{"item": self.item,
			"qty": qty, "rate": 10, "uom": self.uom, "row_index": 0, "table_index": 0, **self.dimensions}]})
		entry.insert()
		entry.submit()

	def make_order(self, qty=6, customer=None):
		order = frappe.get_doc({"doctype": "YRP Sales Order", "customer": customer or self.customer,
			"company": self.company, "set_warehouse": self.warehouse,
			"items": [{"item_code": self.item, "uom": self.uom, "qty": qty, "rate": 25}]}).insert()
		order.submit()
		return order

	def make_note(self, *orders, customer=None):
		note = make_delivery_note(customer or self.customer, [order.name for order in orders])
		for row in note.items:
			row.update(self.dimensions)
		return note

	def get_reserved_qty(self):
		return frappe.db.get_value("YRP Bin", get_or_make_bin(self.item, self.warehouse, **self.dimensions),
			"reserved_qty")

	def get_reservations(self, note, status=None):
		filters = {"voucher_type": "YRP Delivery Note", "voucher_no": note.name, "docstatus": 1}
		if status:
			filters["status"] = status
		return frappe.get_all("YRP Stock Reservation Entry", filters=filters, fields=["name", "reserved_qty", "status"])

	def test_mapper_pulls_pending_quantity(self):
		order = self.make_order(qty=6)
		first = self.make_note(order)
		first.items[0].qty = 4
		first.insert()
		second = self.make_note(order)
		row = second.items[0]
		self.assertEqual((row.qty, row.so_detail, row.rate, row.warehouse), (2, order.items[0].name, 25, self.warehouse))
		second.insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Nothing is pending"):
			make_delivery_note(self.customer, [order.name])

	def test_over_allocation_across_drafts_is_rejected(self):
		order = self.make_order(qty=6)
		first = self.make_note(order)
		first.items[0].qty = 4
		first.insert()
		second = self.make_note(order)
		second.items[0].qty = 3
		with self.assertRaisesRegex(frappe.ValidationError, "only 6"):
			second.insert()

	def test_repeated_sales_order_row_is_aggregated(self):
		note = self.make_note(self.make_order(qty=6))
		note.append("items", note.items[0].as_dict(no_default_fields=True))
		with self.assertRaisesRegex(frappe.ValidationError, "only 6"):
			note.insert()

	def test_mixed_customers_are_rejected(self):
		other = self.make_order(customer=self.make_customer())
		with self.assertRaisesRegex(frappe.ValidationError, "another Customer"):
			make_delivery_note(self.customer, [self.make_order().name, other.name])
		note = self.make_note(self.make_order())
		note.append("items", {**self.make_note(other, customer=other.customer).items[0].as_dict(no_default_fields=True)})
		with self.assertRaisesRegex(frappe.ValidationError, "another Customer"):
			note.insert()

	def test_item_must_match_sales_order_row(self):
		note = self.make_note(self.make_order())
		note.items[0].item_code = self.make_item()
		with self.assertRaisesRegex(frappe.ValidationError, "Item must match"):
			note.insert()

	def test_draft_reserves_and_edits_re_reserve(self):
		note = self.make_note(self.make_order(qty=6))
		note.insert()
		self.assertEqual(self.get_reserved_qty(), 6)
		note.items[0].qty = 2
		note.save()
		self.assertEqual(self.get_reserved_qty(), 2)
		self.assertEqual([row.reserved_qty for row in self.get_reservations(note, "Reserved")], [2])
		note.save()
		self.assertEqual(len(self.get_reservations(note, "Reserved")), 1)

	def test_removing_a_row_releases_its_reservation(self):
		note = self.make_note(self.make_order(qty=3), self.make_order(qty=4))
		note.insert()
		self.assertEqual(self.get_reserved_qty(), 7)
		note.items = note.items[:1]
		note.save()
		self.assertEqual(self.get_reserved_qty(), 3)

	def test_insufficient_stock_blocks_save(self):
		note = self.make_note(self.make_order(qty=11))
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds live available"):
			note.insert()

	def test_delete_releases_reservations(self):
		note = self.make_note(self.make_order())
		note.insert()
		note.delete()
		self.assertEqual(self.get_reserved_qty(), 0)

	def test_submit_keeps_and_cancel_releases_reservations(self):
		order = self.make_order()
		note = self.make_note(order)
		note.insert()
		note.submit()
		self.assertEqual((note.status, self.get_reserved_qty()), ("Submitted", 6))
		with self.assertRaises(frappe.ValidationError):
			order.cancel()
		note.cancel()
		self.assertEqual(self.get_reserved_qty(), 0)
		self.assertEqual(frappe.db.get_value("YRP Delivery Note", note.name, "status"), "Cancelled")
		order.reload()
		order.cancel()

	def test_draft_note_blocks_sales_order_cancel(self):
		order = self.make_order()
		self.make_note(order).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Delivery Notes"):
			order.cancel()

	def test_submit_requires_reservation(self):
		note = self.make_note(self.make_order())
		note.insert()
		frappe.get_doc("YRP Stock Reservation Entry", self.get_reservations(note)[0].name).cancel()
		with self.assertRaisesRegex(frappe.ValidationError, "no matching stock reservation"):
			note.submit()
