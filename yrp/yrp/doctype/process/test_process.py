# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def _ensure_item_attribute(name):
	"""Return an Item Attribute name, creating it if absent (rolled back per test)."""
	if not frappe.db.exists("Item Attribute", name):
		frappe.get_doc({"doctype": "Item Attribute", "attribute_name": name}).insert()
	return name


class IntegrationTestProcess(IntegrationTestCase):
	def test_value_change_attribute_round_trips(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Smoke Process",
				"value_change_attributes": [{"attribute": attr}],
			}
		)
		proc.insert()
		self.assertEqual(len(proc.value_change_attributes), 1)
		self.assertEqual(proc.value_change_attributes[0].attribute, attr)

		# True round-trip: re-fetch from the DB and confirm the child row persisted.
		reloaded = frappe.get_doc("Process", proc.name)
		self.assertEqual(len(reloaded.value_change_attributes), 1)
		self.assertEqual(reloaded.value_change_attributes[0].attribute, attr)

	def test_duplicate_value_change_attribute_rejected(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Dup Process",
				"value_change_attributes": [{"attribute": attr}, {"attribute": attr}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			proc.insert()

	def test_group_process_cannot_have_value_change_attributes(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Group Process",
				"is_group": 1,
				"value_change_attributes": [{"attribute": attr}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			proc.insert()
