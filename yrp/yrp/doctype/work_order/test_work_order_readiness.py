from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.work_order.work_order import WorkOrder


class TestWorkOrderReadiness(FrappeTestCase):
	def test_base_policy_allows_draft_without_process_cost(self):
		work_order = WorkOrder({"doctype": "Work Order"})
		self.assertTrue(work_order.allow_draft_without_process_cost())

	def test_permissive_draft_clears_a_stale_process_cost(self):
		work_order = WorkOrder(
			{
				"doctype": "Work Order",
				"process_name": "Changed Process",
				"process_cost": "Old Process Cost",
				"receivables": [
					{
						"item_variant": "Changed Variant",
						"qty": 10,
						"process_cost": "Old Process Cost",
						"cost": 25,
						"total_cost": 250,
					}
				],
			}
		)
		with patch.object(WorkOrder, "get_receivable_process_cost", return_value=None):
			work_order.set_receivable_process_costs(require_approved=False)

		self.assertIsNone(work_order.process_cost)
		self.assertIsNone(work_order.receivables[0].process_cost)
		self.assertEqual(work_order.receivables[0].cost, 0)
		self.assertEqual(work_order.receivables[0].total_cost, 0)

	def test_customer_app_can_require_process_cost_on_draft(self):
		class StrictWorkOrder(WorkOrder):
			def allow_draft_without_process_cost(self):
				return False

		work_order = StrictWorkOrder(
			{
				"doctype": "Work Order",
				"process_name": "Strict Process",
				"receivables": [
					{"item_variant": "Strict Variant", "qty": 10}
				],
			}
		)
		with (
			patch.object(StrictWorkOrder, "get_receivable_process_cost", return_value=None),
			self.assertRaises(frappe.ValidationError),
		):
			work_order.set_receivable_process_costs(require_approved=False)

	def test_submit_readiness_returns_all_shared_issues(self):
		work_order = WorkOrder(
			{
				"doctype": "Work Order",
				"process_name": "Unconfigured Process",
				"supplier": "Unconfigured Supplier",
				"deliverables": [],
				"receivables": [
					{"item_variant": "Unconfigured Variant", "qty": 10}
				],
			}
		)
		with patch.object(WorkOrder, "get_receivable_process_cost", return_value=None):
			issues = work_order.get_submit_readiness_issues()

		self.assertIn("There are no deliverables on the Work Order.", issues)
		self.assertTrue(any("No approved Process Cost" in issue for issue in issues))

	def test_submit_reports_combined_readiness_error(self):
		work_order = WorkOrder({"doctype": "Work Order"})
		with self.assertRaises(frappe.ValidationError) as raised:
			work_order.before_submit()

		message = str(raised.exception)
		self.assertIn("There are no deliverables", message)
		self.assertIn("There are no receivables", message)
