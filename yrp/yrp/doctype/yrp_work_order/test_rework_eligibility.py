from unittest import TestCase
from unittest.mock import patch

from yrp.yrp.doctype.yrp_work_order import yrp_work_order as module

MODULE = "yrp.yrp.doctype.yrp_work_order.yrp_work_order"


class TestReworkEligibility(TestCase):
	"""Rework sources: processed GRNs only, Received Types classified by an app hook when it can."""

	def test_return_and_rework_grns_are_not_rework_sources(self):
		with patch(f"{MODULE}.frappe.get_all", return_value=[]) as get_all:
			module._submitted_work_order_grns("WO-1")
		filters = get_all.call_args.kwargs["filters"]
		self.assertEqual((filters["is_rework"], filters["is_return"]), (0, 0))

	def test_hook_classification_decides_and_none_defers_to_settings(self):
		decisions = {"Rejected": True, "Fabric Mistake": False, "Untyped": None}
		with (
			patch(f"{MODULE}.frappe.get_hooks", return_value=["app.classify"]),
			patch(f"{MODULE}.frappe.get_attr", return_value=decisions.get),
		):
			self.assertTrue(module._is_rework_eligible_rt("Rejected", "Accepted", "Rejected"))
			self.assertFalse(module._is_rework_eligible_rt("Fabric Mistake", "Accepted", "Rejected"))
			self.assertTrue(module._is_rework_eligible_rt("Untyped", "Accepted", "Rejected"))
			self.assertFalse(module._is_rework_eligible_rt("Accepted", "Accepted", "Rejected"))

	def test_without_hook_default_and_rejected_types_are_excluded(self):
		with patch(f"{MODULE}.frappe.get_hooks", return_value=[]):
			self.assertTrue(module._is_rework_eligible_rt("Oil Mark", "Accepted", "Rejected"))
			self.assertFalse(module._is_rework_eligible_rt("Rejected", "Accepted", "Rejected"))
