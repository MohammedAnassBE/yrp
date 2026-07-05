from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


def _rework_work_order(**row_values):
	doc = frappe.new_doc("Work Order")
	doc.is_rework = 1
	doc.append("deliverables", row_values)
	return doc


class TestWorkOrderReworkSourceRefs(FrappeTestCase):
	def test_rework_deliverable_requires_one_source_ref(self):
		doc = _rework_work_order()
		with self.assertRaisesRegex(frappe.ValidationError, "must reference exactly one source"):
			doc.validate_rework_source_refs()

	def test_rework_deliverable_rejects_ambiguous_source_refs(self):
		doc = _rework_work_order(
			source_grn_item="dummy-grn-item",
			source_inspection_entry_item="dummy-inspection-item",
		)
		with self.assertRaisesRegex(frappe.ValidationError, "must reference exactly one source"):
			doc.validate_rework_source_refs()

	def test_rework_deliverable_accepts_single_source_ref(self):
		doc = _rework_work_order(source_grn_item="dummy-grn-item", received_type="dummy-received-type")
		doc.validate_rework_source_refs()

	def test_nested_rework_is_blocked(self):
		doc = _rework_work_order(source_grn_item="dummy-grn-item")
		doc.parent_wo = "dummy-parent-wo"
		with patch.object(frappe.db, "get_value", return_value=1):
			with self.assertRaisesRegex(frappe.ValidationError, "Nested rework is not allowed"):
				doc.validate_rework_source_refs()
