"""Historical boundaries plus real local sales workflows, without installing Custom Fields."""

import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import frappe
from frappe.utils import now_datetime

from yrp.yrp_sales import cartons, invoicing
from yrp.yrp_sales.doctype.yrp_delivery_note import yrp_delivery_note as delivery
from yrp.yrp_sales.doctype.yrp_packing_slip import yrp_packing_slip as packing
from yrp.yrp_sales.historical import is_historical
from yrp.yrp_sales.test_invoicing import DeliveryNoteFixtures

MESSAGE = "This is a migrated historical record and cannot be changed."
ORDER = "YRP Sales Order"
NOTE = "YRP Delivery Note"
SLIP = "YRP Packing Slip"


class TestHistoricalBoundaries(unittest.TestCase):
	def setUp(self):
		frappe._(MESSAGE)  # Warm translation metadata before asserting no business queries.

	def test_predicate_accepts_optional_field_and_named_documents(self):
		for value, expected in ((None, False), (0, False), ("0", False), (1, True), ("1", True)):
			with self.subTest(value=value):
				doc = frappe._dict(doctype=NOTE, is_historical=value)
				self.assertEqual(is_historical(doc), expected)
				with patch.object(frappe, "get_doc", return_value=doc):
					self.assertEqual(is_historical("Test Historical Note", NOTE), expected)
		self.assertFalse(is_historical(None, NOTE))
		self.assertFalse(is_historical(frappe._dict(doctype=ORDER)))

	def test_predicate_does_not_query_absent_custom_field(self):
		doc = frappe._dict(doctype=ORDER, name="Test Historical Order")
		with (
			patch.object(frappe, "get_meta", return_value=Mock(has_field=Mock(return_value=False))),
			patch.object(frappe.db, "get_value") as read,
		):
			self.assertFalse(is_historical(doc))
			read.assert_not_called()

	def test_clearing_marker_cannot_bypass_stored_history(self):
		doc = frappe._dict(doctype=ORDER, name="Test Historical Order", is_historical=0)
		with (
			patch.object(frappe, "get_meta", return_value=Mock(has_field=Mock(return_value=True))),
			patch.object(frappe.db, "get_value", return_value=1),
		):
			self.assertTrue(is_historical(doc))
			doc["__islocal"] = 1
			self.assertTrue(is_historical(doc))

	def test_historical_controller_actions_reject_before_business_effects(self):
		actions = {
			ORDER: {
				"_save": (),
				"validate": (),
				"before_update_after_submit": (),
				"on_submit": (),
				"before_cancel": (),
				"on_cancel": (),
				"on_trash": (),
				"before_discard": (),
				"on_discard": (),
				"cancel_qty": ({"Test Row": 2},),
			},
			NOTE: {
				"_save": (),
				"validate": (),
				"before_update_after_submit": (),
				"on_submit": (),
				"before_cancel": (),
				"on_cancel": (),
				"on_trash": (),
				"before_discard": (),
				"update_rates": ({"Test Row": 30},),
				"initiate_packing": (),
				"complete_packing": ("Test Local Packer",),
				"cancel_with_reason": ("Test reason",),
				"cancel_packing_slips": (),
				"deliver": (),
				"mark_delivered": (),
				"lock_for_delivery": (),
			},
			SLIP: {
				"_save": (),
				"validate": (),
				"before_update_after_submit": (),
				"on_submit": (),
				"before_cancel": (),
				"on_cancel": (),
				"on_trash": (),
				"before_discard": (),
				"mark_delivered": (),
				"set_delivered": (now_datetime(),),
			},
		}
		for doctype, methods in actions.items():
			for method, args in methods.items():
				with self.subTest(doctype=doctype, method=method):
					doc = frappe.get_doc({"doctype": doctype, "is_historical": 1, "items": [{"qty": 6}]})
					with patch.object(doc, "db_set") as write, patch.object(frappe.db, "sql") as sql:
						with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
							getattr(doc, method)(*args)
						write.assert_not_called()
						sql.assert_not_called()

	def test_historical_rollups_and_reservations_leave_values_untouched(self):
		for doctype, methods in {
			ORDER: ["on_update", "update_delivered_qty"],
			NOTE: [
				"on_update",
				"update_billing",
				"update_packing",
				"update_delivery",
				"sync_reservations",
				"validate_reservations",
				"make_reservation",
			],
		}.items():
			for method in methods:
				with self.subTest(doctype=doctype, method=method):
					doc = frappe.get_doc(
						{
							"doctype": doctype,
							"is_historical": 1,
							"status": "Migrated",
							"per_delivered": 47,
							"items": [{"qty": 6, "delivered_qty": 3}],
						}
					)
					before = doc.as_dict()
					with (
						patch.object(doc, "db_set") as write,
						patch.object(frappe.db, "sql") as sql,
						patch("yrp.yrp_retail.sales_sources.refresh_progress") as progress,
					):
						getattr(doc, method)(*([doc.items[0]] if method == "make_reservation" else []))
						self.assertEqual(doc.as_dict(), before)
						self.assertEqual(doc.get_status(), "Migrated")
						write.assert_not_called()
						sql.assert_not_called()
						progress.assert_not_called()

	def test_normal_order_update_still_refreshes_retail_progress(self):
		doc = frappe.get_doc({"doctype": ORDER})
		with patch("yrp.yrp_retail.sales_sources.refresh_progress") as progress:
			doc.on_update()
			progress.assert_called_once_with(doc, "on_update")

	def test_invoice_hooks_skip_historical_stock_and_billing(self):
		note = frappe.get_doc({"doctype": NOTE, "is_historical": 1, "per_delivered": 100})
		invoice = frappe._dict(yrp_delivery_note="Test Historical Note", yrp_stock_entries="Test Issue")
		for method in (
			invoicing.issue_delivery_note_stock,
			invoicing.reverse_delivery_note_stock,
			invoicing.refresh_delivery_note_billing,
		):
			with (
				self.subTest(method=method.__name__),
				patch.object(frappe, "get_doc", return_value=note),
				patch.object(frappe.db, "exists", return_value=True),
				patch.object(note, "update_billing") as billing,
				patch.object(invoicing, "consume_voucher_reservation") as consume,
				patch.object(invoicing, "get_stock_issues") as issues,
			):
				method(invoice)
				billing.assert_not_called()
				consume.assert_not_called()
				issues.assert_not_called()

	def test_invoice_hooks_without_note_keep_existing_noop(self):
		for method in (
			invoicing.issue_delivery_note_stock,
			invoicing.reverse_delivery_note_stock,
			invoicing.refresh_delivery_note_billing,
		):
			with self.subTest(method=method.__name__), patch.object(frappe, "get_doc") as read:
				method(frappe._dict())
				read.assert_not_called()

	def test_invoice_creation_and_manual_links_reject_history(self):
		note = frappe.get_doc(
			{
				"doctype": NOTE,
				"name": "Test Historical Note",
				"is_historical": 1,
				"items": [{"qty": 6, "rate": 25}],
			}
		)
		with patch.object(frappe, "get_doc", return_value=note), patch.object(frappe, "new_doc") as create:
			for action in (
				lambda: invoicing.get_sales_invoice(note.name),
				lambda: invoicing.make_sales_invoice(note.name),
				lambda: invoicing.preview_sales_invoice(note.name),
				lambda: invoicing.validate_sales_invoice(frappe._dict(yrp_delivery_note=note.name)),
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()
			create.assert_not_called()

	def test_pending_selection_preserves_normal_quantities(self):
		orders = {
			"Test Historical Order": frappe._dict(doctype=ORDER, is_historical=1),
			"Test Normal Order": frappe._dict(doctype=ORDER),
		}
		rows = [
			frappe._dict(
				sales_order=name,
				stock_qty=12,
				cancelled_qty=1,
				conversion_factor=2,
				allocated=4,
				is_historical=orders[name].get("is_historical"),
			)
			for name in orders
		]
		with (
			patch.object(frappe.db, "sql", return_value=rows),
			patch.object(frappe, "get_doc", side_effect=lambda doctype, name: orders[name]),
		):
			pending = delivery.get_pending_rows("Test Customer")
			self.assertEqual(
				[(row.sales_order, row.pending_qty) for row in pending], [("Test Normal Order", 3)]
			)
			with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
				delivery.get_pending_rows("Test Customer", ["Test Historical Order"])

	def test_explicit_source_apis_reject_historical_orders(self):
		order = frappe.get_doc(
			{
				"doctype": ORDER,
				"name": "Test Historical Order",
				"is_historical": 1,
				"docstatus": 1,
				"customer": "Test Customer",
				"items": [{"qty": 6}],
			}
		)
		with patch.object(frappe, "get_doc", return_value=order), patch.object(frappe, "new_doc") as create:
			for action in (
				lambda: delivery.get_source_orders(order.customer, [order.name]),
				lambda: delivery.make_delivery_note(order.customer, [order.name]),
				lambda: delivery.create_delivery_note(order.customer, [order.name]),
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()
			create.assert_not_called()
		order.is_historical = 0
		order.name = None
		with patch.object(frappe, "get_doc", return_value=order):
			self.assertEqual(delivery.get_source_orders(order.customer, ["Test Normal Order"]), [order])

	def test_manual_source_rows_reject_historical_orders(self):
		note = frappe.get_doc(
			{"doctype": NOTE, "items": [{"sales_order": "Test Historical Order", "qty": 6}]}
		)
		order = frappe._dict(name="Test Historical Order", is_historical=1)
		with (
			patch.object(delivery, "lock_sales_orders", return_value={order.name: order}),
			patch.object(frappe, "get_doc", return_value=order),
		):
			with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
				note.get_source_rows()

	def test_amendment_and_carton_apis_reject_historical_notes(self):
		note = frappe.get_doc({"doctype": NOTE, "name": "Test Historical Note", "is_historical": 1})
		slip = frappe.get_doc({"doctype": SLIP, "delivery_note": note.name, "items": [{"qty": 6}]})
		amended = frappe.get_doc({"doctype": NOTE, "amended_from": note.name, "items": [{"qty": 6}]})
		with patch.object(frappe, "get_doc", return_value=note), patch.object(frappe, "new_doc") as create:
			for action in (
				lambda: delivery.amend_delivery_note(note.name, "Test Customer"),
				lambda: packing.make_packing_slip(note.name),
				lambda: cartons.build_packing_slips(note.name, [[{"dn_detail": "Test Row", "qty": 6}]]),
				lambda: cartons.cancel_packing_slips(note.name),
				slip.validate,
				slip._save,
				amended.validate,
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()
			create.assert_not_called()


class TestHistoricalSalesFlows(DeliveryNoteFixtures):
	def label(self, label):
		return "_Test Historical " + label + " " + frappe.generate_hash(length=10)

	@contextmanager
	def historical(self, *documents):
		"""Supply only the migration marker; real records and all business methods remain active.

		The local site need not have the partner app's staged Custom Fields installed.
		"""
		keys = {(doc.doctype, doc.name) for doc in documents}
		get_doc = frappe.get_doc
		sql = frappe.db.sql

		def read(*args, **kwargs):
			doc = get_doc(*args, **kwargs)
			if (doc.doctype, doc.name) in keys:
				doc.is_historical = 1
			return doc

		def query(*args, **kwargs):
			rows = sql(*args, **kwargs)
			for row in rows or []:
				if (
					isinstance(row, dict)
					and "is_historical" in row
					and (ORDER, row.get("sales_order")) in keys
				):
					row["is_historical"] = 1
			return rows

		for doc in documents:
			doc.is_historical = 1
		with (
			patch.object(frappe, "get_doc", side_effect=read),
			patch.object(frappe.db, "sql", side_effect=query),
		):
			yield

	def test_document_save_submit_cancel_delete_paths(self):
		note = self.make_note(6)
		order = frappe.get_doc(ORDER, note.items[0].sales_order)
		slip = packing.make_packing_slip(note.name).insert()
		for doc in (order, note, slip):
			with self.subTest(doctype=doc.doctype), self.historical(doc):
				for method in (doc.save, doc.submit, doc.cancel):
					with (
						self.subTest(method=method.__name__),
						self.assertRaisesRegex(frappe.ValidationError, MESSAGE),
					):
						method()
				# Frappe rejects deleting submitted documents before invoking on_trash.
				with self.assertRaises(frappe.ValidationError):
					doc.delete()
		self.assertEqual(self.get_bin(), (10, 6))
		self.assertEqual(frappe.db.get_value(NOTE, note.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value(SLIP, slip.name, "docstatus"), 0)

	def test_historical_sources_are_excluded_and_explicit_sources_rejected(self):
		note = self.make_note(6, submit=False)
		order = frappe.get_doc(ORDER, note.items[0].sales_order)
		note.delete()  # Release the normal draft's allocation before testing pending selection.
		self.assertEqual(delivery.get_pending_rows(self.customer)[0].pending_qty, 6)
		with self.historical(order):
			self.assertEqual(delivery.get_pending_rows(self.customer), [])
			for action in (
				lambda: delivery.get_pending_rows(self.customer, [order.name]),
				lambda: delivery.get_source_orders(self.customer, [order.name]),
				lambda: delivery.make_delivery_note(self.customer, [order.name]),
				lambda: delivery.create_delivery_note(self.customer, [order.name]),
				lambda: frappe.get_doc(
					{
						"doctype": NOTE,
						"customer": self.customer,
						"company": self.company,
						"items": [
							{
								"sales_order": order.name,
								"so_detail": order.items[0].name,
								"item_code": self.item,
								"qty": 2,
							}
						],
					}
				).insert(),
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()

	def test_historical_draft_deletion_uses_the_shared_error(self):
		note = self.make_note(3, submit=False)
		order = frappe.copy_doc(frappe.get_doc(ORDER, note.items[0].sales_order)).insert()
		for doc in (order, note):
			with self.subTest(doctype=doc.doctype), self.historical(doc):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					doc.delete()
			self.assertTrue(frappe.db.exists(doc.doctype, doc.name))
		self.assertEqual(self.get_bin(), (10, 3))

	def test_historical_notes_cannot_create_extend_or_manually_link_invoices(self):
		note = self.make_note(6)
		invoice = self.invoice(note, {note.items[0].name: 2}, submit=False)
		with self.historical(note):
			for action in (
				lambda: invoicing.get_sales_invoice(note.name),
				lambda: invoicing.make_sales_invoice(note.name),
				lambda: invoicing.preview_sales_invoice(note.name),
				lambda: invoicing.validate_sales_invoice(frappe._dict(yrp_delivery_note=note.name)),
				invoice.save,
				invoice.submit,
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()
		self.assertEqual(self.get_bin(), (10, 6))
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "docstatus"), 0)

	def test_historical_notes_cannot_be_amended_or_packed(self):
		note = self.make_note(6)
		with self.historical(note):
			for action in (
				lambda: delivery.amend_delivery_note(note.name, self.customer),
				lambda: frappe.get_doc(
					{"doctype": NOTE, "amended_from": note.name, "items": [{"qty": 2}]}
				).validate(),
				lambda: packing.make_packing_slip(note.name),
				lambda: frappe.get_doc(
					{
						"doctype": SLIP,
						"delivery_note": note.name,
						"items": [{"dn_detail": note.items[0].name, "qty": 2}],
					}
				).insert(),
				lambda: cartons.build_packing_slips(
					note.name, [[{"dn_detail": note.items[0].name, "qty": 6}]]
				),
				lambda: cartons.cancel_packing_slips(note.name),
			):
				with self.assertRaisesRegex(frappe.ValidationError, MESSAGE):
					action()
		self.assertEqual(self.get_bin(), (10, 6))
		self.assertFalse(frappe.db.exists(SLIP, {"delivery_note": note.name}))

	def test_historical_order_rollup_does_not_change_saved_quantities(self):
		note = self.make_note(6)
		order = frappe.get_doc(ORDER, note.items[0].sales_order)
		self.invoice(note)
		with self.historical(order):
			note.deliver()
		self.assertEqual(frappe.db.get_value(NOTE, note.name, "per_delivered"), 100)
		self.assertEqual(frappe.db.get_value(ORDER, order.name, "per_delivered"), 0)

	def test_normal_delivery_rolls_up_packing_billing_and_order(self):
		note = self.make_note(6)
		slip = packing.make_packing_slip(note.name).insert()
		slip.submit()
		self.invoice(note)
		slip.mark_delivered()
		note.reload()
		self.assertEqual((note.per_packed, note.per_billed, note.per_delivered), (100, 100, 100))
		self.assertEqual(frappe.db.get_value(ORDER, note.items[0].sales_order, "per_delivered"), 100)
		self.assertEqual(self.get_bin(), (4, 0))
