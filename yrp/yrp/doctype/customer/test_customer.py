import frappe
from frappe.tests.utils import FrappeTestCase


class TestCustomer(FrappeTestCase):
	def test_customer_can_be_created(self):
		doc = frappe.get_doc({
			"doctype": "Customer",
			"customer_name": f"_Test Customer {frappe.generate_hash(length=6)}",
		})
		doc.insert(ignore_permissions=True)
		self.assertTrue(doc.name)
