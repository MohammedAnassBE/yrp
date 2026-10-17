"""Summary-owned closing and reopening with transactional source claims."""
import unittest

import frappe

from yrp.yrp_retail import api
from yrp.yrp_retail import test_retail_flow as retail_fixtures


class TestSummaryLifecycle(unittest.TestCase):
	def setUp(self):
		self.fixture = retail_fixtures.TestRetailFlow()
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()

	def summary(self, orders):
		name = api.create_summary(orders)["name"]
		return frappe.get_doc("YRP Retail Order Summary", name)

	def assert_order(self, name, status, summary):
		order = frappe.get_doc("YRP Retail Order", name)
		self.assertEqual(order.docstatus, status)
		self.assertEqual(order.summary or None, summary)
		self.assertTrue(order.items)
		self.assertTrue(all(row.docstatus == status for row in order.get_all_children()))

	def test_submit_closes_all_sources_and_blocks_direct_changes(self):
		orders = [self.fixture.order(3), self.fixture.order(7)]
		summary = self.summary(orders)
		frappe.set_user("Administrator")
		for name in orders:
			with self.assertRaisesRegex(frappe.ValidationError, "included in a summary"):
				frappe.get_doc("YRP Retail Order", name).submit()
		frappe.set_user(self.fixture.user.name)
		api.submit_summary(summary.name)
		for name in orders:
			self.assert_order(name, 1, summary.name)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			api.update_retail_order(orders[0], self.fixture.items(8))

	def test_failing_second_order_rolls_back_submission(self):
		orders = sorted([self.fixture.order(3), self.fixture.order(7)])
		summary = self.summary(orders)
		# A real stale Visit header passes aggregation but fails order validation.
		visit = frappe.db.get_value("YRP Retail Order", orders[1], "visit")
		frappe.db.set_value("YRP Visit", visit, "visit_type", "Primary")
		with self.assertRaisesRegex(frappe.ValidationError, "header conflicts"):
			api.submit_summary(summary.name)
		self.assertEqual(summary.reload().docstatus, 0)
		for name in orders:
			self.assert_order(name, 0, summary.name)
		frappe.db.set_value("YRP Visit", visit, "visit_type", "Secondary")
		api.submit_summary(summary.name)
		for name in orders:
			self.assert_order(name, 1, summary.name)

	def test_cancel_reopens_sources_for_editing_and_resummarizing(self):
		orders = [self.fixture.order(3), self.fixture.order(7)]
		summary = self.summary(orders)
		api.submit_summary(summary.name)
		frappe.set_user("Administrator")
		summary.reload().cancel()
		for name in orders:
			self.assert_order(name, 0, None)
		frappe.set_user(self.fixture.user.name)
		api.update_retail_order(orders[0], self.fixture.items(8))
		replacement = self.summary(orders)
		self.assertEqual(replacement.total_requested_qty, 15)
		api.submit_summary(replacement.name)
		for name in orders:
			self.assert_order(name, 1, replacement.name)

	def test_cancel_does_not_reopen_or_release_another_summary_claim(self):
		order = self.fixture.order(3)
		summary = self.summary([order])
		other = self.summary([self.fixture.order(7)])
		api.submit_summary(summary.name)
		frappe.db.set_value("YRP Retail Order", order, "summary", other.name)
		frappe.set_user("Administrator")
		summary.reload().cancel()
		self.assert_order(order, 1, other.name)

	def test_draft_delete_only_releases_sources(self):
		orders = [self.fixture.order(3), self.fixture.order(7)]
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order", orders[1]).submit()
		frappe.set_user(self.fixture.user.name)
		summary = self.summary(orders)
		frappe.set_user("Administrator")
		summary.delete()
		self.assert_order(orders[0], 0, None)
		self.assert_order(orders[1], 1, None)

	def test_draft_discard_only_releases_sources(self):
		order = self.fixture.order(3)
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order", order).submit()
		frappe.set_user(self.fixture.user.name)
		summary = self.summary([order])
		frappe.set_user("Administrator")
		summary.discard()
		self.assert_order(order, 1, None)
