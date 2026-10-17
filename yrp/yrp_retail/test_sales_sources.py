"""Retail source accounting tests with fictional native records and rollback."""
import unittest
from unittest.mock import patch

import frappe
from frappe.utils import add_days, getdate, today

from yrp.yrp_retail import api, sales_sources
from yrp.yrp_retail.test_retail_flow import TestRetailFlow


class TestSourceQuantityPolicy(unittest.TestCase):
	def test_capacity_finite_positive_and_float_noise(self):
		with patch.object(sales_sources.frappe, "throw", side_effect=ValueError), patch("yrp.yrp_retail.logic._", side_effect=lambda value: value):
			for qty in (0, -1, float("nan"), float("inf"), 5.01):
				with self.subTest(qty=qty), self.assertRaises(ValueError):
					sales_sources._check_quantity(qty, 10, 5)
			self.assertEqual(sales_sources._check_quantity(5, 10, 5), 5)
			self.assertEqual(sales_sources._check_quantity(0.1 + 0.2, 0.3, 0), 0.1 + 0.2)

	def test_source_factor_matches_current_native_item_conversion(self):
		item = frappe._dict(stock_uom="Unit", uoms=[frappe._dict(uom="Case", conversion_factor=12)])
		source = frappe._dict(doctype="YRP Retail Order")
		row = frappe._dict(item_code="Fictional", uom="Case", conversion_factor=12)
		with patch.object(sales_sources.frappe, "get_doc", return_value=item), patch.object(sales_sources.frappe, "throw", side_effect=ValueError), patch("yrp.yrp_retail.logic._", side_effect=lambda value: value):
			self.assertEqual(sales_sources._factor(source, row), 12)
			row.conversion_factor = 6
			with self.assertRaises(ValueError):
				sales_sources._factor(source, row)
			row.uom = "Unknown"
			with self.assertRaises(ValueError):
				sales_sources._factor(source, row)


class TestSalesSources(TestRetailFlow):
	def setUp(self):
		super().setUp()
		self.primary = api.create_retail_order(self.visit(secondary=False), self.items(10))["name"]
		frappe.set_user("Administrator")
		# Source conversion tests also exercise the optional Customer count API.
		from frappe.permissions import add_permission, update_permission_property
		add_permission("YRP Customer Stock", "YRP Sales Person")
		update_permission_property("YRP Customer Stock", "YRP Sales Person", 0, "write", 1)
		suffix = frappe.generate_hash(length=8)
		# Keep fictional companies outside the installed GST integration.
		self.company = frappe.get_doc({
			"doctype": "Company", "company_name": "Test Source Company " + suffix,
			"abbr": "SC" + suffix[:3], "country": "United States", "default_currency": "INR",
			"chart_of_accounts": "Standard", "enable_perpetual_inventory": 0,
		}).insert()
		year = getdate(today()).year
		frappe.get_doc({"doctype": "Fiscal Year", "year": self.label("Source Fiscal Year"),
			"year_start_date": f"{year}-01-01", "year_end_date": f"{year}-12-31",
			"companies": [{"company": self.company.name}]}).insert()
		self.price_list = frappe.get_doc({
			"doctype": "Price List", "price_list_name": self.label("Source Prices"),
			"enabled": 1, "selling": 1, "currency": "INR",
		}).insert()
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name,
			"price_list": self.price_list.name, "price_list_rate": 20, "uom": self.uom.name}).insert()

	def make_so(self, qty=None, source=None, doctype="YRP Retail Order"):
		source = source or self.primary
		doc = frappe.get_doc(doctype, source)
		selection = [{"source_row": doc.items[0].name, "qty": qty}] if qty is not None else None
		result = sales_sources.make_sales_order(doctype, source, self.company.name,
			add_days(today(), 7), items=selection, selling_price_list=self.price_list.name)
		return frappe.get_doc("YRP Sales Order", result["name"])

	def source_progress(self):
		return frappe.get_doc("YRP Retail Order", self.primary)

	def set_pack_conversion(self, factor):
		"""Use fractional packs containing whole pieces; never change test demand."""
		frappe.set_user("Administrator")
		item = frappe.get_doc("Item", self.item.name)
		if not hasattr(self, "piece_uom"):
			self.piece_uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Pieces"),
				"must_be_whole_number": 1}).insert().name
			item.stock_uom = self.piece_uom
			# ERPNext clears old conversions when the Stock UOM changes.
			item.save()
		item.set("uoms", [{"uom": self.piece_uom, "conversion_factor": 1},
			{"uom": self.uom.name, "conversion_factor": factor}])
		item.save()

	def test_summary_conversion_snapshot_is_preserved_and_drift_is_rejected(self):
		self.set_pack_conversion(12)
		frappe.set_user(self.user.name)
		secondary = self.order(10)
		summary = api.create_summary([secondary])["name"]
		api.submit_summary(summary)
		frappe.set_user("Administrator")
		order = self.make_so(source=summary, doctype="YRP Retail Order Summary")
		self.assertEqual((order.items[0].qty, order.items[0].uom, order.items[0].stock_qty),
			(10, self.uom.name, 120))
		order.delete()
		self.set_pack_conversion(6)
		with self.assertRaisesRegex(frappe.ValidationError, "differs from the current Item conversion"):
			self.make_so(source=summary, doctype="YRP Retail Order Summary")
		self.assertEqual(frappe.db.count("YRP Sales Order", {"retail_order_summary": summary}), 0)
		self.assertEqual(frappe.db.get_value("YRP Retail Order Summary", summary, "ordered_qty"), 0)
		self.assertEqual(frappe.get_doc("YRP Retail Order", secondary).items[0].conversion_factor, 12)

	def test_summary_rejects_mixed_conversion_snapshots_before_claiming_orders(self):
		self.set_pack_conversion(12)
		frappe.set_user(self.user.name)
		first = self.order(3)
		self.set_pack_conversion(6)
		frappe.set_user(self.user.name)
		second = self.order(4)
		with self.assertRaisesRegex(frappe.ValidationError, "different conversion factors"):
			api.create_summary([first, second])
		for name in (first, second):
			self.assertFalse(frappe.db.get_value("YRP Retail Order", name, "summary"))

	def test_summary_validates_stock_uom_allocation_and_preserves_customer_bin_uom(self):
		self.set_pack_conversion(12)
		frappe.set_user(self.user.name)
		secondary = self.order(10)
		api.update_customer_stock(self.customer.name, self.item.name, 6)
		summary = api.create_summary([secondary])["name"]
		allocation = {"item_code": self.item.name, "uom": self.uom.name, "customer_stock_qty": 0.1}
		with self.assertRaisesRegex(frappe.ValidationError, "whole number"):
			api.update_summary(summary, [dict(allocation)])
		self.assertEqual(frappe.get_doc("YRP Retail Order Summary", summary).items[0].customer_stock_qty, 0)
		self.assertEqual(api.get_customer_stock(self.customer.name, self.item.name)["qty"], 6)

		# Half a pack is six whole pieces and must remain valid throughout the
		# flow. The Customer bin records packs, not its converted six pieces.
		allocation["customer_stock_qty"] = 0.5
		api.update_summary(summary, [allocation])
		api.submit_summary(summary)
		balance = api.get_customer_stock(self.customer.name, self.item.name)
		self.assertEqual((balance["qty"], balance["uom"]), (5.5, self.uom.name))
		frappe.set_user("Administrator")
		order = self.make_so(source=summary, doctype="YRP Retail Order Summary")
		self.assertEqual((order.items[0].qty, order.items[0].uom, order.items[0].stock_qty),
			(9.5, self.uom.name, 114))

	def test_summary_rejects_remainder_lost_by_sales_order_precision(self):
		self.set_pack_conversion(3)
		frappe.set_user(self.user.name)
		secondary = self.order(1)
		api.update_customer_stock(self.customer.name, self.item.name, 6)
		meta = frappe.get_meta("YRP Sales Order Item")
		# Scope the native field precision to this test, without changing the
		# site's settings. 2/3 becomes 0.667, which is 2.001 whole-piece units.
		with patch.object(meta.get_field("qty"), "precision", "3"), \
			patch.object(meta.get_field("stock_qty"), "precision", "3"):
			with self.assertRaisesRegex(frappe.ValidationError, "Company quantity at Sales Order precision"):
				api.create_summary([secondary], [{"item_code": self.item.name,
					"uom": self.uom.name, "customer_stock_qty": 1 / 3}])
		self.assertFalse(frappe.db.get_value("YRP Retail Order", secondary, "summary"))
		self.assertEqual(api.get_customer_stock(self.customer.name, self.item.name)["qty"], 6)

	def test_summary_rejects_zeroed_or_changed_whole_stock_remainder(self):
		self.set_pack_conversion(10000)
		frappe.set_user(self.user.name)
		secondary = self.order(1)
		api.update_customer_stock(self.customer.name, self.item.name, 6)
		meta = frappe.get_meta("YRP Sales Order Item")
		with patch.object(meta.get_field("qty"), "precision", "3"), \
			patch.object(meta.get_field("stock_qty"), "precision", "3"):
			for allocation, error in ((0.9996, "too small"), (0.9994, "stock quantity changes")):
				# Four pieces must not round to zero; six must not become ten.
				with self.subTest(allocation=allocation), self.assertRaisesRegex(frappe.ValidationError, error):
					api.create_summary([secondary], [{"item_code": self.item.name,
						"uom": self.uom.name, "customer_stock_qty": allocation}])
		self.assertFalse(frappe.db.get_value("YRP Retail Order", secondary, "summary"))
		self.assertEqual(api.get_customer_stock(self.customer.name, self.item.name)["qty"], 6)

	def test_summary_rejects_native_rounding_past_selected_uom_capacity(self):
		self.set_pack_conversion(0.1)
		frappe.db.set_value("UOM", self.piece_uom, "must_be_whole_number", 0)
		frappe.set_user(self.user.name)
		secondary = self.order(1)
		meta = frappe.get_meta("YRP Sales Order Item")
		with patch.object(meta.get_field("qty"), "precision", "3"), \
			patch.object(meta.get_field("stock_qty"), "precision", "3"):
			# 0.9999 rounds to 1, exceeding source capacity, even though both
			# stock quantities round to the same 0.100 default units.
			with self.assertRaisesRegex(frappe.ValidationError, "Company quantity changes"):
				api.create_summary([secondary], [{"item_code": self.item.name,
					"uom": self.uom.name, "customer_stock_qty": 0.0001}])
		self.assertFalse(frappe.db.get_value("YRP Retail Order", secondary, "summary"))

	def test_drafts_reserve_remaining_quantity_and_delete_releases(self):
		first = self.make_so(qty=4)
		self.assertEqual(first.docstatus, 0)
		self.assertEqual(first.items[0].rate, 20)
		self.assertEqual(first.retail_order, self.primary)
		source = self.source_progress()
		self.assertEqual(source.items[0].ordered_qty, 4)
		self.assertEqual(source.ordered_qty, 4)
		self.assertEqual(source.per_ordered, 40)
		with self.assertRaises(frappe.ValidationError):
			self.make_so(qty=7)
		second = self.make_so()
		self.assertEqual(second.items[0].qty, 6)
		self.assertEqual(self.source_progress().per_ordered, 100)
		first.delete()
		self.assertEqual(self.source_progress().ordered_qty, 6)
		self.assertEqual(self.make_so().items[0].qty, 4)

	def test_edit_excludes_self_and_enforces_combined_capacity(self):
		first = self.make_so(qty=3)
		self.make_so(qty=4)
		first.items[0].qty = 6
		first.save()
		self.assertEqual(self.source_progress().ordered_qty, 10)
		first.items[0].qty = 7
		with self.assertRaises(frappe.ValidationError):
			first.save()

	def test_defaults_read_remaining_capacity_without_reserving(self):
		self.make_so(qty=4)
		values = sales_sources.get_sales_order_defaults("YRP Retail Order", self.primary)
		self.assertEqual((values["customer"], values["order_type"]), (self.customer.name, "Primary"))
		self.assertEqual([row["qty"] for row in values["items"]], [6])
		self.assertEqual(values["items"][0]["retail_order_item"], self.source_progress().items[0].name)
		self.assertIsNotNone(values["visit"])
		self.assertEqual(self.source_progress().ordered_qty, 4)

	def test_delivery_date_is_optional(self):
		result = sales_sources.make_sales_order("YRP Retail Order", self.primary, self.company.name,
			selling_price_list=self.price_list.name)
		self.assertIsNone(frappe.db.get_value("YRP Sales Order", result["name"], "delivery_date"))

	def test_discard_frees_capacity_and_cancels_status(self):
		order = self.make_so(qty=4)
		order.discard()
		self.assertEqual(frappe.db.get_value("YRP Sales Order", order.name, "status"), "Cancelled")
		self.assertEqual(self.source_progress().ordered_qty, 0)

	def test_submit_cancel_releases_capacity(self):
		order = self.make_so(qty=10)
		order.submit()
		self.assertEqual(self.source_progress().ordered_qty, 10)
		order.cancel()
		self.assertEqual(self.source_progress().ordered_qty, 0)
		self.assertEqual(self.make_so().items[0].qty, 10)

	def test_source_row_item_uom_and_header_tampering_rejected(self):
		order = self.make_so(qty=2)
		for field, value in (("retail_order_item", None), ("retail_summary_item", "forged"), ("conversion_factor", 2), ("qty", -1)):
			order.reload()
			order.items[0].set(field, value)
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				sales_sources.validate_sales_order(order)
		order.reload()
		order.customer = "Wrong Customer"
		with self.assertRaises(frappe.ValidationError):
			sales_sources.validate_sales_order(order)
		order.reload()
		order.retail_order = None
		with self.assertRaises(frappe.ValidationError):
			sales_sources.validate_sales_order(order)

	def test_used_primary_source_edits_blocked_then_unlocked(self):
		order = self.make_so(qty=2)
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.ValidationError):
			api.update_retail_order(self.primary, self.items(11))
		frappe.set_user("Administrator")
		order.delete()
		frappe.set_user(self.user.name)
		api.update_retail_order(self.primary, self.items(11))

	def test_partner_cannot_create_sales_orders(self):
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			sales_sources.make_sales_order("YRP Retail Order", self.primary, self.company.name,
				add_days(today(), 7), selling_price_list=self.price_list.name)

	def test_secondary_requires_submitted_summary_and_uses_company_quantity(self):
		frappe.set_user(self.user.name)
		secondary = self.order(10)
		summary = api.create_summary([secondary], [{"item_code": self.item.name,
			"uom": self.uom.name, "customer_stock_qty": 3}])["name"]
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			self.make_so(source=secondary)
		with self.assertRaises(frappe.ValidationError):
			self.make_so(source=summary, doctype="YRP Retail Order Summary")
		frappe.set_user(self.user.name)
		api.submit_summary(summary)
		frappe.set_user("Administrator")
		order = self.make_so(source=summary, doctype="YRP Retail Order Summary")
		self.assertEqual(order.items[0].qty, 7)
		self.assertTrue(order.items[0].retail_summary_item)
		self.assertEqual(frappe.db.get_value("YRP Retail Order Summary", summary, "ordered_qty"), 7)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("YRP Retail Order Summary", summary).cancel()
		order.delete()
		frappe.get_doc("YRP Retail Order Summary", summary).cancel()

	def test_mapping_selection_rejects_forged_duplicate_and_extra_fields(self):
		row = self.source_progress().items[0].name
		for selected in ([{"source_row": "forged", "qty": 1}],
			[{"source_row": row, "qty": 1}, {"source_row": row, "qty": 1}],
			[{"source_row": row, "qty": 1, "rate": 0}]):
			with self.subTest(selected=selected), self.assertRaises(frappe.ValidationError):
				sales_sources.make_sales_order("YRP Retail Order", self.primary,
					self.company.name, add_days(today(), 7), items=selected,
					selling_price_list=self.price_list.name)

	def test_source_progress_is_server_computed(self):
		self.make_so(qty=3)
		source = self.source_progress()
		source.ordered_qty = 999
		source.per_ordered = 999
		source.items[0].ordered_qty = 999
		source.save()
		source.reload()
		self.assertEqual(source.ordered_qty, 3)
		self.assertEqual(source.per_ordered, 30)
		self.assertEqual(source.items[0].ordered_qty, 3)

	def test_mapping_failure_after_insert_rolls_back_draft_and_progress(self):
		from frappe.model.document import Document
		original = Document.insert
		def fail_after_insert(doc, *args, **kwargs):
			result = original(doc, *args, **kwargs)
			if doc.doctype == "YRP Sales Order":
				raise RuntimeError("Synthetic failure after insert")
			return result
		with patch.object(Document, "insert", new=fail_after_insert), self.assertRaises(RuntimeError):
			self.make_so(qty=4)
		self.assertEqual(frappe.db.count("YRP Sales Order", {"retail_order": self.primary}), 0)
		self.assertEqual(self.source_progress().ordered_qty, 0)


# setUp ends as Administrator, so the inherited retail-flow tests (run in test_retail_flow) must not rerun here.
for _name in dir(TestRetailFlow):
	if _name.startswith("test_") and _name not in TestSalesSources.__dict__:
		setattr(TestSalesSources, _name, None)


class RetailWriterConnection:
	def writer(self, doc, action, raw=False):
		from contextvars import Context
		from queue import Queue
		from threading import Thread

		site, sites_path = frappe.local.site, frappe.local.sites_path
		values = doc.as_dict()
		results = Queue()

		def run():
			queries = []
			try:
				frappe.init(site, sites_path=sites_path)
				frappe.connect()
				frappe.flags.in_test = True
				frappe.db.sql("set session innodb_lock_wait_timeout=1")
				other = frappe.get_doc(values)
				if raw or action == "invoice":
					# A private, uncommitted clone lets this connection own its
					# parent while the fixture owns pricing. No fixture commits.
					other.name = "Test Lock Contender " + frappe.generate_hash(length=10)
					if other.doctype == "YRP Retail Order":
						other.visit = None
					other.db_insert()
					other.set_parent_in_children()
					for child in other.get_all_children():
						child.name = None
						child.db_insert()
				original_sql = frappe.db.sql

				def observe(query, *args, **kwargs):
					if "for update" in str(query).lower():
						queries.append(str(query))
					return original_sql(query, *args, **kwargs)

				with patch.object(frappe.db, "sql", side_effect=observe):
					if action == "invoice":
						from yrp.yrp_sales.invoicing import get_sales_invoice
						get_sales_invoice(other.name)
					elif raw:
						frappe.delete_doc(other.doctype, other.name)
					else:
						getattr(other, action)()
			except Exception as error:
				results.put((queries, error))
			else:
				results.put((queries, None))
			finally:
				if frappe.local.db:
					frappe.db.rollback()
				frappe.destroy()

		thread = Thread(target=Context().run, args=(run,), daemon=True)
		thread.start()
		# This is the opposing summary operation: it owns pricing and must
		# still reach the linked Sales Order and reject the live reservation.
		if self.summary:
			with self.assertRaisesRegex(frappe.ValidationError, "linked Sales Orders"):
				frappe.get_doc("YRP Retail Order Summary", self.summary).cancel()
		thread.join(timeout=10)
		self.assertFalse(thread.is_alive(), "Writer did not finish within its lock timeout")
		queries, error = results.get_nowait()
		self.assertIsInstance(error, frappe.QueryTimeoutError)
		return queries, error


class TestRetailWriterLocks(RetailWriterConnection, unittest.TestCase):
	"""Two real transactions; all fictional rows and failed writes roll back.

	The fixture transaction holds pricing and the source/order rows. A second
	writer must wait on pricing before attempting a parent lock. Observing SQL
	keeps this deterministic without committing fixtures or relying on sleeps.
	"""

	def setUp(self):
		self.fixture = TestSalesSources()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()
		frappe.set_user(self.fixture.user.name)
		secondary = self.fixture.order(10)
		self.visit = frappe.get_doc("YRP Retail Order", secondary).visit
		self.summary = api.create_summary([secondary])["name"]
		api.submit_summary(self.summary)
		frappe.set_user("Administrator")
		self.order = self.fixture.make_so(source=self.summary, doctype="YRP Retail Order Summary")

	def test_linked_sales_order_lifecycle_waits_on_pricing_before_parent(self):
		for action in ("save", "submit", "discard", "delete"):
			with self.subTest(action=action):
				queries, _ = self.writer(self.order, action)
				self.assertEqual(len(queries), 1, queries)
				self.assertIn("tabDocType", queries[0])
				self.assertIn("YRP Item Master Template", queries[0])
		self.order.submit()
		queries, _ = self.writer(self.order, "cancel")
		self.assertEqual(len(queries), 1, queries)
		self.assertIn("tabDocType", queries[0])

	def test_other_retail_writers_wait_on_pricing_before_parent(self):
		fixture = self.fixture
		for doc, action in (
			(frappe.get_doc("YRP Retail Order", fixture.primary), "discard"),
			(frappe.get_doc("YRP Retail Order", fixture.primary), "delete"),
			(frappe.get_doc("YRP Visit", self.visit), "save"),
		):
			with self.subTest(doctype=doc.doctype, action=action):
				queries, _ = self.writer(doc, action)
				self.assertEqual(len(queries), 1, queries)
				self.assertIn("tabDocType", queries[0])

	def test_summary_discard_and_delete_use_shared_pricing_lock(self):
		fixture = self.fixture
		frappe.set_user(fixture.user.name)
		secondary = fixture.order(3)
		name = api.create_summary([secondary])["name"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc("YRP Retail Order Summary", name)
		for action in ("discard", "delete"):
			with self.subTest(action=action):
				queries, _ = self.writer(doc, action)
				self.assertEqual(len(queries), 1, queries)
				self.assertIn("tabDocType", queries[0])
				getattr(doc, action)()
				self.assertFalse(frappe.db.get_value("YRP Retail Order", secondary, "summary"))
		self.assertFalse(frappe.db.exists("YRP Retail Order Summary", name))

	def test_sales_invoice_save_waits_on_pricing_before_parent(self):
		# Persist just the invoice rows: this regression stops at the first
		# mutex, before accounting validation or any integration can run.
		invoice = frappe.get_doc({
			"doctype": "Sales Invoice", "name": self.fixture.label("Lock Invoice"),
			"company": self.fixture.company.name, "customer": self.fixture.customer.name,
			"currency": "INR", "conversion_rate": 1, "grand_total": 40,
			"items": [{"item_code": self.fixture.item.name, "qty": 2, "rate": 20,
				"uom": self.fixture.uom.name, "conversion_factor": 1, "amount": 40}],
		})
		invoice.db_insert()
		for row in invoice.items:
			row.db_insert()
		queries, _ = self.writer(invoice, "save")
		self.assertEqual(len(queries), 1, queries)
		self.assertIn("tabDocType", queries[0])

	def test_invoice_mapping_waits_on_pricing_before_existing_invoice_rows(self):
		note = frappe.get_doc({
			"doctype": "YRP Delivery Note", "name": self.fixture.label("Lock Note"),
			"docstatus": 1, "company": self.fixture.company.name,
			"customer": self.fixture.customer.name, "total_qty": 2, "total": 40,
			"items": [{"sales_order": self.order.name, "so_detail": self.order.items[0].name,
				"item_code": self.fixture.item.name, "qty": 2, "rate": 20, "amount": 40,
				"uom": self.fixture.uom.name, "conversion_factor": 1, "stock_qty": 2}],
		})
		queries, _ = self.writer(note, "invoice")
		self.assertEqual(len(queries), 1, queries)
		self.assertIn("tabDocType", queries[0])


class TestRawSalesOrderDeletionLocks(RetailWriterConnection, unittest.TestCase):
	def setUp(self):
		# Do not allocate a Sales Order here: that would gap-lock the very
		# index into which the second connection needs to insert its clone.
		self.fixture = TestRetailFlow()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()
		primary = api.create_retail_order(self.fixture.visit(secondary=False), self.fixture.items(10))["name"]
		frappe.set_user("Administrator")
		self.summary = None
		self.order = frappe.get_doc({
			"doctype": "YRP Sales Order", "name": self.fixture.label("Raw Order"),
			"customer": self.fixture.customer.name, "retail_order": primary,
			"items": [{"item_code": self.fixture.item.name, "qty": 2, "rate": 20,
				"uom": self.fixture.uom.name, "retail_order_item":
				frappe.get_doc("YRP Retail Order", primary).items[0].name}],
		})

	def test_raw_linked_sales_order_delete_refuses_pricing_contention(self):
		queries, error = self.writer(self.order, "delete", raw=True)
		self.assertIn("retail document cannot be deleted", str(error))
		self.assertEqual(len(queries), 2, queries)
		self.assertIn("tabYRP Sales Order", queries[0])
		self.assertIn("tabDocType", queries[1])
		self.assertIn("nowait", queries[1].lower())

	def test_raw_retail_order_delete_guards_before_touching_visit(self):
		self.order = frappe.get_doc("YRP Retail Order", self.order.retail_order)
		queries, error = self.writer(self.order, "delete", raw=True)
		self.assertIn("retail document cannot be deleted", str(error))
		self.assertEqual(len(queries), 2, queries)
		self.assertIn("tabYRP Retail Order", queries[0])
		self.assertIn("tabDocType", queries[1])
		self.assertIn("nowait", queries[1].lower())
