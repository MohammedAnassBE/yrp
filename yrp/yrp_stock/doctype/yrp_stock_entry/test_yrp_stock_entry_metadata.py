import frappe
from frappe.tests import IntegrationTestCase


class TestStockEntryMetadata(IntegrationTestCase):
	def test_send_to_warehouse_shows_source_and_target_pairs(self):
		meta = frappe.get_meta('YRP Stock Entry')
		for fieldname in (
			"from_supplier",
			"from_warehouse",
			"to_supplier",
			"to_warehouse",
		):
			with self.subTest(fieldname=fieldname):
				self.assertIn(
					'"Send to Warehouse"',
					meta.get_field(fieldname).depends_on,
				)
