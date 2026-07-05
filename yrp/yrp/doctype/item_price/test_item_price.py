import frappe
from frappe.tests.utils import FrappeTestCase


class TestItemPrice(FrappeTestCase):
	def test_non_attribute_price_matches_blank_child_attribute(self):
		doc = frappe.get_doc({"doctype": "Item Price"})

		rate = doc.get_price_value(
			[[0, 5, 3, ""]],
			qty=5,
			attribute_value=None,
		)

		self.assertEqual(rate, 5)

	def test_non_attribute_lead_time_matches_blank_child_attribute(self):
		doc = frappe.get_doc({"doctype": "Item Price"})

		lead_time = doc.get_price_value(
			[[0, 5, 3, ""]],
			qty=5,
			attribute_value=None,
			get_lead_time=True,
		)

		self.assertEqual(lead_time, 3)
