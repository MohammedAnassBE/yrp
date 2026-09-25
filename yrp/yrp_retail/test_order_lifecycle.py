"""Native retail cancellation with legacy draft demand and source protections."""
import unittest

import frappe
from frappe.utils import today

from yrp.yrp_retail import api, reporting
from yrp.yrp_retail import test_retail_flow as retail_fixtures
from yrp.yrp_retail import test_sales_sources as sales_fixtures


class TestOrderLifecycle(unittest.TestCase):
	def setUp(self):
		# Reuse the fictional fixtures without inheriting and rerunning their tests.
		self.fixture = retail_fixtures.TestRetailFlow()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()

	def demand_names(self):
		filters = {"from_date": today(), "to_date": today(), "customer": self.fixture.customer.name}
		return {row["document"] for row in reporting.run("demand", filters)[1]}

	def test_native_submit_cancel_preserves_visit_history_and_excludes_demand(self):
		fixture = self.fixture
		name = fixture.order(10)
		frappe.set_user("Administrator")
		order = frappe.get_doc("YRP Retail Order", name)
		self.assertEqual(order.docstatus, 0)
		self.assertIn(name, self.demand_names())
		with self.assertRaises(frappe.DocstatusTransitionError):
			order.cancel()
		order.reload().submit()
		self.assertEqual(order.docstatus, 1)
		self.assertIn(name, self.demand_names())
		order.items[0].qty = 20
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			order.save()
		order.reload().cancel()
		self.assertEqual(frappe.db.get_value(order.doctype, name, "docstatus"), 2)
		self.assertNotIn(name, self.demand_names())
		self.assertEqual(frappe.db.get_value("YRP Visit", order.visit, "has_order"), 1)
		frappe.set_user(fixture.user.name)
		with self.assertRaises(frappe.UniqueValidationError):
			api.create_retail_order(order.visit, fixture.items())

	def test_summary_accepts_existing_drafts_and_submitted_orders(self):
		fixture = self.fixture
		draft = fixture.order(3)
		submitted = fixture.order(4)
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order", submitted).submit()
		frappe.set_user(fixture.user.name)
		name = api.create_summary([draft, submitted])["name"]
		api.submit_summary(name)
		self.assertEqual(frappe.db.get_value("YRP Retail Order Summary", name, "total_requested_qty"), 7)
		self.assertEqual(frappe.db.get_value("YRP Retail Order", draft, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("YRP Retail Order", submitted, "docstatus"), 1)
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order Summary", name).cancel()
		for source in (draft, submitted):
			self.assertFalse(frappe.db.get_value("YRP Retail Order", source, "summary"))

	def test_draft_and_submitted_summary_claims_block_order_cancellation(self):
		fixture = self.fixture
		name = fixture.order()
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order", name).submit()
		frappe.set_user(fixture.user.name)
		summary = api.create_summary([name])["name"]
		for status in (0, 1):
			with self.subTest(summary_docstatus=status):
				if status:
					frappe.set_user(fixture.user.name)
					api.submit_summary(summary)
				frappe.set_user("Administrator")
				with self.assertRaisesRegex(frappe.ValidationError, "included in a summary"):
					frappe.get_doc("YRP Retail Order", name).cancel()
				self.assertEqual(frappe.db.get_value("YRP Retail Order", name, "docstatus"), 1)
				self.assertEqual(frappe.db.get_value("YRP Retail Order", name, "summary"), summary)
		frappe.get_doc("YRP Retail Order Summary", summary).cancel()
		frappe.get_doc("YRP Retail Order", name).cancel()
		self.assertEqual(frappe.db.get_value("YRP Retail Order", name, "docstatus"), 2)

	def test_cancelled_order_cannot_be_claimed_by_summary(self):
		fixture = self.fixture
		name = fixture.order()
		frappe.set_user("Administrator")
		order = frappe.get_doc("YRP Retail Order", name)
		order.submit().cancel()
		frappe.set_user(fixture.user.name)
		before = frappe.db.count("YRP Retail Order Summary")
		with self.assertRaises(frappe.CancelledLinkError):
			api.create_summary([name])
		self.assertEqual(frappe.db.count("YRP Retail Order Summary"), before)
		self.assertFalse(frappe.db.get_value("YRP Retail Order", name, "summary"))
		# The controller's locked read must also reject cancellation independently
		# of Frappe's earlier cached link validation.
		summary = frappe.get_doc({"doctype": "YRP Retail Order Summary",
			"customer": order.customer, "sales_person": order.sales_person,
			"order_type": "Secondary", "from_date": order.order_date, "to_date": order.order_date,
			"source_orders": [{"order": name}]})
		with self.assertRaisesRegex(frappe.ValidationError, "Cancelled Retail Orders"):
			summary.validate()


class TestOrderSalesLifecycle(unittest.TestCase):
	def setUp(self):
		self.fixture = sales_fixtures.TestSalesSources()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()

	def test_primary_draft_and_submitted_sources_still_create_sales_orders(self):
		fixture = self.fixture
		source = fixture.source_progress()
		self.assertEqual(source.docstatus, 0)
		first = fixture.make_so(qty=4)
		source.reload().submit()
		second = fixture.make_so(qty=6)
		self.assertEqual((first.items[0].qty, second.items[0].qty), (4, 6))
		self.assertEqual(fixture.source_progress().ordered_qty, 10)

	def test_active_sales_orders_block_source_cancel_and_cancelled_source_cannot_map(self):
		fixture = self.fixture
		source = fixture.source_progress()
		source.submit()
		order = fixture.make_so(qty=4)
		for status in (0, 1):
			with self.subTest(sales_order_docstatus=status):
				if status:
					order.submit()
				with self.assertRaisesRegex(frappe.ValidationError, "linked Sales Orders"):
					source.reload().cancel()
				self.assertEqual(fixture.source_progress().docstatus, 1)
		order.cancel()
		self.assertEqual(fixture.source_progress().ordered_qty, 0)
		source.reload().cancel()
		self.assertEqual(fixture.source_progress().docstatus, 2)
		before = frappe.db.count("Sales Order", {"yrp_retail_order": source.name})
		with self.assertRaisesRegex(frappe.ValidationError, "Cancelled Retail Orders"):
			fixture.make_so(qty=1)
		self.assertEqual(frappe.db.count("Sales Order", {"yrp_retail_order": source.name}), before)
