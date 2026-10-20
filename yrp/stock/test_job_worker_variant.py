from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.stock.job_worker_variant import get_job_worker_stock_variant


class TestJobWorkerStockVariant(TestCase):
	def test_without_handlers_the_variant_is_unchanged(self):
		with patch.object(frappe, "get_hooks", return_value=[]):
			self.assertEqual(get_job_worker_stock_variant(frappe._dict(), "ITEM-A"), "ITEM-A")

	def test_handlers_resolve_in_order(self):
		handlers = {
			"first": lambda _doc, variant: variant + "-1",
			"second": lambda _doc, variant: variant + "-2",
		}
		with (
			patch.object(frappe, "get_hooks", return_value=["first", "second"]),
			patch.object(frappe, "get_attr", side_effect=handlers.get),
		):
			self.assertEqual(get_job_worker_stock_variant(frappe._dict(), "ITEM-A"), "ITEM-A-1-2")
