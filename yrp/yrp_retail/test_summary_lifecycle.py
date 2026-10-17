"""Summary parity with non-zero local demand and native sales links."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_retail import api
from yrp.yrp_retail.test_sales_sources import TestSalesSources


class TestSummaryLifecycle(unittest.TestCase):
	def setUp(self):
		self.fixture = TestSalesSources()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()
		frappe.set_user(self.fixture.user.name)
		self.source = self.fixture.order(10)
		self.name = api.create_summary([self.source])["name"]

	def link_order(self):
		frappe.set_user("Administrator")
		try:
			return self.fixture.make_so(source=self.name, doctype="YRP Retail Order Summary")
		finally:
			frappe.set_user(self.fixture.user.name)

	def summary(self):
		return frappe.get_doc("YRP Retail Order Summary", self.name)

	def assert_source(self, status, summary):
		order = frappe.get_doc("YRP Retail Order", self.source)
		self.assertEqual((order.docstatus, order.summary or None), (status, summary))
		self.assertEqual(order.items[0].qty, 10)
		self.assertEqual(order.items[0].docstatus, status)

	def test_submit_requires_sales_order_and_closes_sources(self):
		with self.assertRaisesRegex(frappe.ValidationError, "linked Sales Order"):
			api.submit_summary(self.name, self.fixture.person.name)
		self.assertEqual(self.summary().docstatus, 0)
		self.assert_source(0, self.name)
		self.link_order()
		self.summary().submit()
		self.assert_source(1, self.name)
		with self.assertRaises(frappe.ValidationError):
			api.cancel_summary(self.name, self.fixture.person.name, reason="LOCAL linked order")
		self.assertEqual(self.summary().docstatus, 1)
		self.assert_source(1, self.name)

	def test_explicit_bypass_and_cancel_reopen_sources_without_stock_changes(self):
		from yrp.yrp_retail.customer_stock import report

		fixture = self.fixture
		report(fixture.customer.name, fixture.item.name, 7)
		doc = self.summary()
		doc.items[0].customer_stock_qty = 3
		doc.flags.submit_without_order = True
		doc.submit()
		self.assert_source(1, self.name)
		api.cancel_summary(self.name, fixture.person.name, reason="LOCAL shop closed")
		self.assertEqual(self.summary().docstatus, 2)
		self.assert_source(0, None)
		self.assertEqual(
			frappe.db.get_value(
				"YRP Customer Stock",
				{"customer": fixture.customer.name, "item_code": fixture.item.name},
				"qty",
			),
			7,
		)
		comments = frappe.get_all("Comment", {"reference_name": self.name}, pluck="content")
		self.assertTrue(any("LOCAL shop closed" in comment for comment in comments))

	def test_draft_cancellation_retains_history_and_rejects_repeat(self):
		api.cancel_summary(self.name, self.fixture.person.name, reason="LOCAL duplicate")
		self.assertEqual(self.summary().docstatus, 2)
		self.assert_source(0, None)
		with self.assertRaises(frappe.ValidationError):
			api.cancel_summary(self.name, self.fixture.person.name, reason="LOCAL repeat")

	def test_failed_source_submission_rolls_back_summary(self):
		self.link_order()
		from yrp.yrp_retail.summary_lifecycle import source_submission

		with patch(
			"yrp.yrp_retail.summary_lifecycle.source_submission",
			wraps=source_submission,
			side_effect=frappe.ValidationError("LOCAL failure"),
		):
			with self.assertRaises(frappe.ValidationError):
				api.submit_summary(self.name, self.fixture.person.name)
		self.assertEqual(self.summary().docstatus, 0)
		self.assert_source(0, self.name)

	def test_draft_with_linked_order_cannot_cancel(self):
		self.link_order()
		with self.assertRaisesRegex(frappe.ValidationError, "linked Sales Orders"):
			api.cancel_summary(self.name, self.fixture.person.name, reason="LOCAL linked draft")
		self.assertEqual(self.summary().docstatus, 0)
		self.assert_source(0, self.name)

	def test_foreign_claim_refuses_cancellation_without_releasing_source(self):
		frappe.db.set_value("YRP Retail Order", self.source, "summary", "LOCAL another summary")
		with self.assertRaisesRegex(frappe.ValidationError, "not part of this summary"):
			api.cancel_summary(self.name, self.fixture.person.name, reason="LOCAL stale claim")
		self.assertEqual(self.summary().docstatus, 0)
		self.assert_source(0, "LOCAL another summary")
