# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import secrets
import unittest

import frappe
from frappe.utils import nowdate

from yrp.stock.dimensions import get_dimension_fieldnames, get_stock_dimensions
from yrp.stock.utils import get_or_make_bin
from yrp.yrp_sales.doctype.yrp_delivery_note.yrp_delivery_note import (
	create_delivery_note,
	get_delivery_note,
	get_pending_rows,
	make_delivery_note,
)


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

	def test_read_only_roles_cannot_change(self):
		for role in ["Sales Manager", "Sales User", "Accounts User"]:
			user = frappe.get_doc({"doctype": "User", "email": frappe.generate_hash(length=10) + "@example.invalid",
				"first_name": "Fictional Reader", "send_welcome_email": 0, "roles": [{"role": role}]}).insert().name
			self.assertTrue(frappe.has_permission("YRP Delivery Note", "read", user=user), role)
			for ptype in ("create", "write", "delete", "submit", "cancel"):
				self.assertFalse(frappe.has_permission("YRP Delivery Note", ptype, user=user), (role, ptype))

	def make_price_list(self, rate=None):
		name = frappe.get_doc({"doctype": "Price List", "price_list_name": self.label("Prices"), "selling": 1,
			"currency": frappe.db.get_value("Company", self.company, "default_currency")}).insert().name
		if rate:
			frappe.get_doc({"doctype": "Item Price", "price_list": name, "item_code": self.item, "uom": self.uom,
				"price_list_rate": rate}).insert()
		return name

	def test_selected_rows_take_listed_quantities_only(self):
		first, second = self.make_order(qty=6), self.make_order(qty=4)
		note = get_delivery_note(self.customer, [first.name, second.name],
			items=[{"so_detail": first.items[0].name, "qty": 2}])
		self.assertEqual([(row.so_detail, row.qty) for row in note.items], [(first.items[0].name, 2)])
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds pending 6"):
			get_delivery_note(self.customer, [first.name], items=[{"so_detail": first.items[0].name, "qty": 7}])
		with self.assertRaisesRegex(frappe.ValidationError, "not on the selected Sales Orders"):
			get_delivery_note(self.customer, [first.name], items=[{"so_detail": second.items[0].name, "qty": 1}])

	def test_warehouse_overrides_every_row(self):
		other = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": self.label("Dispatch"),
			"company": self.company}).insert().name
		note = get_delivery_note(self.customer, [self.make_order().name], warehouse=other)
		self.assertEqual((note.set_warehouse, {row.warehouse for row in note.items}), (other, {other}))

	def test_mixed_price_lists_need_a_chosen_list(self):
		first, second = self.make_order(), self.make_order()
		frappe.db.set_value("YRP Sales Order", second.name, "selling_price_list", self.make_price_list())
		with self.assertRaisesRegex(frappe.ValidationError, "share one Price List"):
			get_delivery_note(self.customer, [first.name, second.name])
		chosen = self.make_price_list(rate=30)
		note = get_delivery_note(self.customer, [first.name, second.name], selling_price_list=chosen)
		self.assertEqual((note.selling_price_list, {row.rate for row in note.items}), (chosen, {30}))

	def test_additional_row_needs_no_sales_order(self):
		order = self.make_order(qty=2)
		note = get_delivery_note(self.customer, [order.name], selling_price_list=self.make_price_list(rate=30),
			items=[{"so_detail": order.items[0].name, "qty": 2}, {"item_code": self.item, "uom": self.uom, "qty": 3}])
		for row in note.items:
			row.update(self.dimensions)
		note.insert()
		extra = note.items[1]
		self.assertEqual((extra.sales_order, extra.so_detail, extra.rate, extra.stock_qty), (None, None, 30, 3))
		self.assertEqual(self.get_reserved_qty(), 5)

	def test_pending_rows_exclude_allocated_quantity(self):
		order = self.make_order(qty=6)
		note = self.make_note(order)
		note.items[0].qty = 4
		note.insert()
		rows = get_pending_rows(customer=self.customer)
		self.assertEqual([(row.so_detail, row.pending) for row in rows], [(order.items[0].name, 2)])
		note.items[0].qty = 6
		note.save()
		self.assertEqual(get_pending_rows(sales_orders=[order.name]), [])

	def test_create_submits_two_orders_with_reservations(self):
		first, second = self.make_order(qty=4), self.make_order(qty=3)
		note = create_delivery_note(self.customer, [first.name, second.name],
			header={"remarks": "Fictional remarks", "status": "Delivered"})
		self.assertEqual((note.docstatus, note.status, note.remarks), (1, "Submitted", "Fictional remarks"))
		self.assertEqual({row.lot for row in note.items}, {self.dimensions["lot"]})
		self.assertEqual(self.get_reserved_qty(), 7)
		self.assertEqual(len(self.get_reservations(note, "Reserved")), 2)

	def test_create_rolls_back_on_stock_shortage(self):
		first, second = self.make_order(qty=6), self.make_order(qty=6)
		with self.assertRaisesRegex(frappe.ValidationError, "only 4"):
			create_delivery_note(self.customer, [first.name, second.name])
		self.assertFalse(frappe.db.exists("YRP Delivery Note", {"customer": self.customer}))
		self.assertFalse(frappe.db.exists("YRP Stock Reservation Entry", {"voucher_type": "YRP Delivery Note",
			"item_code": self.item}))
		self.assertEqual(self.get_reserved_qty(), 0)

	def test_warehouse_of_another_company_is_rejected(self):
		company = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if not company:
			self.skipTest("Needs a second Company")
		self.warehouse = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": self.label("Other"),
			"company": company}).insert().name
		self.receive(10)
		with self.assertRaisesRegex(frappe.ValidationError, "belongs to another Company"):
			create_delivery_note(self.customer, [self.make_order(qty=2).name], warehouse=self.warehouse)
		self.assertFalse(frappe.db.exists("YRP Delivery Note", {"customer": self.customer}))


class TestAllocateDimensions(unittest.TestCase):
	"""allocate_dimensions with mocked bins: rows whose filters overlap share each bin's free stock."""

	def test_overlapping_filters_share_a_bin(self):
		from unittest.mock import patch

		from yrp.yrp_sales.doctype.yrp_delivery_note import yrp_delivery_note as module

		fieldnames = get_dimension_fieldnames()
		if len(fieldnames) < 2:
			self.skipTest("needs two stock dimensions")
		first, second = fieldnames[:2]
		bins = [{first: "L1", second: "P", "free_qty": 4}, {first: "L2", second: "P", "free_qty": 4}]

		def free_bins(_query, values, as_dict):
			fixed = values[2:]
			return [frappe._dict(row) for row in bins if all(value in row.values() for value in fixed)]

		note = frappe.new_doc("YRP Delivery Note")
		note.set_warehouse = "Stores"
		for values in ({second: "P"}, {}):
			note.append("items", {"item_code": "ZZ Item", "qty": 4, "conversion_factor": 1, "uom": "Nos", **values})
		with (
			patch.object(module, "get_mandatory_dimensions", return_value=[{"fieldname": first}, {"fieldname": second}]),
			patch.object(module, "get_dimension_fieldnames", return_value=[first, second]),
			patch.object(module.frappe.db, "sql", side_effect=free_bins),
			patch.object(module.frappe.db, "get_value", return_value=0),
		):
			module.allocate_dimensions(note)
		self.assertEqual(sorted((row.get(first), row.qty) for row in note.items), [("L1", 4), ("L2", 4)])
