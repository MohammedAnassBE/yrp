import secrets
import unittest
import unittest.mock

import frappe
from frappe.utils import getdate, nowdate

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_or_make_bin
from yrp.yrp_sales.doctype.yrp_delivery_note.yrp_delivery_note import make_delivery_note
from yrp.yrp_sales.invoicing import make_sales_invoice


class DeliveryNoteFixtures(unittest.TestCase):
	"""Company, item, stock and Delivery Note fixtures shared by YRP Sales tests."""

	def setUp(self):
		point = "yrp_invoicing_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.addCleanup(frappe.db.rollback, save_point=point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.company = self.make_company()
		self.price_list = frappe.get_doc({"doctype": "Price List", "price_list_name": self.label("Prices"),
			"currency": "USD", "enabled": 1, "selling": 1}).insert().name
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert().name
		self.item = self.make_item()
		self.customer = self.make_customer()
		self.warehouse = self.make_warehouse()
		self.dimensions = self.get_dimension_values()
		self.receive(10)

	def label(self, label):
		return "Test " + label + " " + frappe.generate_hash(length=10)

	def make_company(self):
		suffix = frappe.generate_hash(length=8)
		company = frappe.get_doc({"doctype": "Company", "company_name": "Test Invoicing " + suffix,
			"abbr": "TI" + suffix[:4], "country": "United States", "default_currency": "USD",
			"chart_of_accounts": "Standard", "enable_perpetual_inventory": 0}).insert()
		year = getdate(nowdate()).year
		frappe.get_doc({"doctype": "Fiscal Year", "year": self.label("Year"), "year_start_date": f"{year}-01-01",
			"year_end_date": f"{year}-12-31", "companies": [{"company": company.name}]}).insert()
		return company.name

	def make_item(self):
		group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		hsn = frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
			"description": "Fictional invoicing test"}).insert()
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

	def make_warehouse(self):
		return frappe.get_doc({"doctype": "Warehouse", "warehouse_name": self.label("Store"),
			"company": self.company}).insert().name

	def get_dimension_values(self):
		values = {}
		for dimension in get_stock_dimensions():
			value = frappe.db.get_value(dimension["dimension_doctype"], {}, "name")
			if dimension["fieldname"] == "received_type":
				value = frappe.db.get_single_value("YRP Stock Settings", "default_received_type") or value
			values[dimension["fieldname"]] = value
		return values

	def receive(self, qty, warehouse=None):
		entry = frappe.get_doc({"doctype": "YRP Stock Entry", "purpose": "Material Receipt",
			"posting_date": nowdate(), "to_warehouse": warehouse or self.warehouse, "items": [{"item": self.item,
			"qty": qty, "rate": 10, "uom": self.uom, "row_index": 0, "table_index": 0, **self.dimensions}]})
		entry.insert()
		entry.submit()

	def make_note(self, *quantities, warehouses=None, submit=True):
		"""Submit a Delivery Note with one row per quantity, each from its own Sales Order."""
		orders = []
		for index, qty in enumerate(quantities):
			order = frappe.get_doc({"doctype": "YRP Sales Order", "customer": self.customer, "company": self.company,
				"selling_price_list": self.price_list, "currency": "USD",
				"set_warehouse": (warehouses or [self.warehouse] * len(quantities))[index],
				"items": [{"item_code": self.item, "uom": self.uom, "qty": qty, "rate": 25}]}).insert()
			order.submit()
			orders.append(order.name)
		note = make_delivery_note(self.customer, orders)
		for row in note.items:
			row.update(self.dimensions)
		note.insert()
		if submit:
			note.submit()
		return note

	def invoice(self, note, items=None, submit=True):
		invoice = make_sales_invoice(note.name, items)
		if submit:
			invoice.submit()
		return invoice

	def get_bin(self, warehouse=None):
		bin_name = get_or_make_bin(self.item, warehouse or self.warehouse, **self.dimensions)
		return tuple(frappe.db.get_value("YRP Bin", bin_name, ["actual_qty", "reserved_qty"]))

	def get_billing(self, note):
		note.reload()
		return note.per_billed, note.status, [row.billed_qty for row in note.items]

	def get_reservation(self, note):
		return frappe.db.get_value("YRP Stock Reservation Entry", {"voucher_type": "YRP Delivery Note",
			"voucher_no": note.name, "docstatus": 1}, ["delivered_qty", "status"])


class TestDeliveryNoteInvoicing(DeliveryNoteFixtures):
	def test_full_invoice_issues_all_stock(self):
		note = self.make_note(6)
		self.assertEqual(self.get_bin(), (10, 6))
		invoice = self.invoice(note)
		self.assertEqual(self.get_bin(), (4, 0))
		self.assertEqual(self.get_billing(note), (100, "Invoiced", [6]))
		self.assertEqual(self.get_reservation(note), (6, "Delivered"))
		entry = frappe.get_doc("YRP Stock Entry", frappe.db.get_value("Sales Invoice", invoice.name, "yrp_stock_entries"))
		self.assertEqual((entry.docstatus, entry.purpose, entry.against, entry.against_id),
			(1, "Material Issue", "YRP Delivery Note", note.name))
		self.assertEqual((entry.items[0].stock_qty, entry.items[0].against_id_detail), (6, note.items[0].name))
		self.assertEqual(getdate(entry.posting_date), getdate(invoice.posting_date))
		self.assertFalse(invoice.update_stock)

	def test_partial_invoices_issue_only_their_quantity(self):
		note = self.make_note(6)
		first = self.invoice(note, {note.items[0].name: 3})
		self.assertEqual(self.get_bin(), (7, 3))
		self.assertEqual(self.get_billing(note), (50, "Submitted", [3]))
		self.assertEqual(self.get_reservation(note), (3, "Partially Delivered"))
		second = self.invoice(note)
		self.assertEqual(second.items[0].qty, 3)
		self.assertEqual(self.get_bin(), (4, 0))
		self.assertEqual(self.get_billing(note), (100, "Invoiced", [6]))
		self.assertNotEqual(first.yrp_stock_entries, second.yrp_stock_entries)

	def test_rows_bill_separately_and_issue_per_warehouse(self):
		second_warehouse = self.make_warehouse()
		self.receive(5, second_warehouse)
		note = self.make_note(3, 3, warehouses=[self.warehouse, second_warehouse])
		self.invoice(note, {note.items[0].name: 3})
		self.assertEqual(self.get_billing(note), (50, "Submitted", [3, 0]))
		invoice = self.invoice(note)
		self.assertEqual(self.get_billing(note), (100, "Invoiced", [3, 3]))
		self.assertEqual((self.get_bin(), self.get_bin(second_warehouse)), ((7, 0), (2, 0)))
		self.assertEqual(frappe.db.get_value("YRP Stock Entry", invoice.yrp_stock_entries, "from_warehouse"),
			second_warehouse)

	def test_over_billing_across_draft_and_submitted_invoices_is_rejected(self):
		note = self.make_note(6)
		row = note.items[0].name
		self.invoice(note, {row: 4})
		self.invoice(note, {row: 1}, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "would be invoiced"):
			self.invoice(note, {row: 2}, submit=False)
		self.assertEqual(self.invoice(note, submit=False).items[0].qty, 1)

	def test_billed_quantity_is_a_locking_read(self):
		"""Under REPEATABLE READ a plain read after the note lock still sees the request's old snapshot,
		so two concurrent drafts could each bill the whole note."""
		from yrp.yrp_sales.invoicing import get_invoiced_qty

		queries = []
		sql = frappe.db.sql

		def record(query, *args, **kwargs):
			queries.append(query)
			return sql(query, *args, **kwargs)

		with unittest.mock.patch.object(frappe.db, "sql", record):
			get_invoiced_qty(self.make_note(6).name)
		self.assertIn("for update", queries[-1])

	def test_repeated_row_is_aggregated(self):
		note = self.make_note(6)
		invoice = self.invoice(note, {note.items[0].name: 4}, submit=False)
		invoice.append("items", invoice.items[0].as_dict(no_default_fields=True))
		with self.assertRaisesRegex(frappe.ValidationError, "would be invoiced"):
			invoice.save()

	def test_update_stock_is_rejected(self):
		invoice = self.invoice(self.make_note(6), submit=False)
		invoice.update_stock = 1
		invoice.items[0].warehouse = self.warehouse
		with self.assertRaisesRegex(frappe.ValidationError, "cannot update stock"):
			invoice.save()

	def test_rate_must_match_the_note(self):
		invoice = self.invoice(self.make_note(6), submit=False)
		invoice.items[0].rate = 20
		with self.assertRaisesRegex(frappe.ValidationError, "Rate must match"):
			invoice.save()

	def test_invoice_requires_a_submitted_note(self):
		note = self.make_note(6)
		draft = self.make_note(2, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			make_sales_invoice(draft.name)
		invoice = self.invoice(note, submit=False)
		invoice.yrp_delivery_note = draft.name
		invoice.items[0].yrp_delivery_note_item = draft.items[0].name
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			invoice.save()

	def test_cancel_restores_stock_and_reservation(self):
		note = self.make_note(6)
		invoice = self.invoice(note, {note.items[0].name: 4})
		invoice = frappe.get_doc("Sales Invoice", invoice.name)
		invoice.cancel()
		self.assertEqual(self.get_bin(), (10, 6))
		self.assertEqual(self.get_reservation(note), (0, "Reserved"))
		self.assertEqual(self.get_billing(note), (0, "Submitted", [0]))
		self.assertEqual(frappe.db.get_value("YRP Stock Entry", invoice.yrp_stock_entries, "docstatus"), 2)
		self.invoice(note)
		self.assertEqual(self.get_bin(), (4, 0))

	def test_amended_invoice_keeps_the_note_and_issues_again(self):
		note = self.make_note(6)
		invoice = self.invoice(note)
		invoice.cancel()
		amended = frappe.copy_doc(invoice)
		amended.update({"amended_from": invoice.name, "docstatus": 0})
		amended.insert()
		self.assertEqual((amended.yrp_delivery_note, amended.items[0].yrp_delivery_note_item, amended.yrp_stock_entries),
			(note.name, note.items[0].name, None))
		amended.submit()
		self.assertEqual(self.get_bin(), (4, 0))
		self.assertEqual(self.get_billing(note), (100, "Invoiced", [6]))
		self.assertEqual(self.get_reservation(note), (6, "Delivered"))
		self.assertNotEqual(amended.yrp_stock_entries, invoice.yrp_stock_entries)
		self.assertEqual(frappe.db.get_value("YRP Stock Entry", amended.yrp_stock_entries, "docstatus"), 1)

	def test_duplicate_of_a_fully_billed_invoice_is_rejected(self):
		invoice = self.invoice(self.make_note(6))
		duplicate = frappe.copy_doc(invoice)
		self.assertEqual(duplicate.yrp_delivery_note, invoice.yrp_delivery_note)
		with self.assertRaisesRegex(frappe.ValidationError, "would be invoiced"):
			duplicate.insert()

	def test_cancel_is_blocked_after_delivery(self):
		note = self.make_note(6)
		invoice = self.invoice(note)
		note.db_set("per_delivered", 50)
		with self.assertRaisesRegex(frappe.ValidationError, "has deliveries"):
			invoice.cancel()

	def test_issue_entry_cannot_be_cancelled_directly(self):
		invoice = self.invoice(self.make_note(6))
		with self.assertRaisesRegex(frappe.ValidationError, "cancel that invoice"):
			frappe.get_doc("YRP Stock Entry", invoice.yrp_stock_entries).cancel()

	def test_note_cancel_is_blocked_while_invoiced(self):
		note = self.make_note(6)
		draft = self.invoice(note, submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "Cancel Sales Invoices"):
			note.cancel()
		draft.delete()
		invoice = self.invoice(note)
		note.reload()
		with self.assertRaises(frappe.ValidationError):
			note.cancel()
		invoice.cancel()
		note.reload()
		note.cancel()
		self.assertEqual(self.get_bin(), (10, 0))

	def test_reservation_floor_is_not_tripped(self):
		note = self.make_note(6)
		other = self.make_note(4)
		self.assertEqual(self.get_bin(), (10, 10))
		self.invoice(note)
		self.assertEqual(self.get_bin(), (4, 4))
		self.assertEqual(self.get_reservation(other), (0, "Reserved"))

	def test_issue_runs_as_the_invoicing_user(self):
		note = self.make_note(6)
		user = frappe.get_doc({"doctype": "User", "email": self.label("biller").replace(" ", "-") + "@example.invalid",
			"first_name": "Fictional Biller", "send_welcome_email": 0,
			"roles": [{"role": "Accounts User"}, {"role": "Accounts Manager"}]}).insert().name
		warehouse = frappe.get_doc("Warehouse", self.warehouse)
		warehouse.append("warehouse_users", {"user": "Administrator"})
		warehouse.save()
		invoice = self.invoice(note, submit=False)
		frappe.set_user(user)
		frappe.db.savepoint("unpermitted_issue")
		with self.assertRaisesRegex(frappe.ValidationError, "not permitted to operate"):
			frappe.get_doc("Sales Invoice", invoice.name).submit()
		frappe.db.rollback(save_point="unpermitted_issue")
		frappe.set_user("Administrator")
		warehouse.append("warehouse_users", {"user": user})
		warehouse.save()
		frappe.set_user(user)
		frappe.get_doc("Sales Invoice", invoice.name).submit()
		self.assertEqual(self.get_bin(), (4, 0))


class TestStocklessNotes(DeliveryNoteFixtures):
	"""Return notes bill a Credit Note and skip-stock notes bill an invoice, neither moving stock."""

	def make_stockless_note(self, qty=4, **flags):
		note = frappe.get_doc({"doctype": "YRP Delivery Note", "customer": self.customer, "company": self.company,
			"selling_price_list": self.price_list, "currency": "USD", "set_warehouse": self.warehouse, **flags,
			"items": [{"item_code": self.item, "uom": self.uom, "qty": qty, "rate": 25, **self.dimensions}]})
		note.insert()
		note.submit()
		return note

	def test_return_note_bills_a_credit_note_without_stock(self):
		note = self.make_stockless_note(is_return=1)
		self.assertEqual((note.skip_stock, self.get_bin(), self.get_reservation(note)), (1, (10, 0), None))
		credit_note = self.invoice(note)
		self.assertEqual((credit_note.is_return, credit_note.items[0].qty), (1, -4))
		self.assertFalse(credit_note.yrp_stock_entries)
		self.assertEqual(self.get_bin(), (10, 0))
		self.assertEqual(self.get_billing(note), (100, "Invoiced", [4]))
		with self.assertRaisesRegex(frappe.ValidationError, "Nothing is pending billing"):
			make_sales_invoice(note.name)
		credit_note.cancel()
		self.assertEqual(self.get_billing(note), (0, "Submitted", [0]))

	def test_return_note_needs_a_credit_note(self):
		note = self.make_stockless_note(is_return=1)
		invoice = make_sales_invoice(note.name)
		invoice.is_return = 0
		invoice.items[0].qty = 4
		with self.assertRaisesRegex(frappe.ValidationError, "bill it with a Credit Note"):
			invoice.insert()
		ordinary = self.make_note(2)
		credit_note = make_sales_invoice(ordinary.name)
		credit_note.is_return = 1
		credit_note.items[0].qty = -2
		with self.assertRaisesRegex(frappe.ValidationError, "A return cannot reference"):
			credit_note.insert()

	def test_credit_note_quantity_must_be_negative_and_within_the_note(self):
		note = self.make_stockless_note(is_return=1)
		credit_note = make_sales_invoice(note.name)
		credit_note.items[0].qty = 4
		with self.assertRaisesRegex(frappe.ValidationError, "must be negative"):
			credit_note.insert()
		credit_note.items[0].qty = -5
		with self.assertRaisesRegex(frappe.ValidationError, "would be invoiced"):
			credit_note.insert()

	def test_skip_stock_note_bills_without_reserving_or_issuing(self):
		note = self.make_stockless_note(qty=20, skip_stock=1)
		self.assertEqual((note.is_return, self.get_reservation(note)), (0, None))
		invoice = self.invoice(note)
		self.assertEqual((invoice.is_return, invoice.items[0].qty, invoice.yrp_stock_entries), (0, 20, None))
		self.assertEqual((self.get_bin(), self.get_billing(note)), ((10, 0), (100, "Invoiced", [20])))

	def test_skip_stock_note_takes_no_sales_order_rows(self):
		order = frappe.get_doc({"doctype": "YRP Sales Order", "customer": self.customer, "company": self.company,
			"selling_price_list": self.price_list, "currency": "USD", "set_warehouse": self.warehouse,
			"items": [{"item_code": self.item, "uom": self.uom, "qty": 2, "rate": 25}]}).insert()
		order.submit()
		note = make_delivery_note(self.customer, [order.name])
		note.is_return = 1
		with self.assertRaisesRegex(frappe.ValidationError, "cannot dispatch Sales Order rows"):
			note.insert()
