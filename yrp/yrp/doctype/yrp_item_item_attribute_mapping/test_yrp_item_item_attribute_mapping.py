"""Synthetic attribute selection fixtures; every test rolls back its records."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.yrp.doctype.yrp_item_item_attribute_mapping.yrp_item_item_attribute_mapping import (
	search_attribute_values,
)


class TestYRPItemItemAttributeMapping(TestCase):
	def setUp(self):
		super().setUp()
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		self.savepoint = "mapping_picker_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.savepoint)
		self.attribute = frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": "_Test Mapping Colour " + frappe.generate_hash(length=8),
			"item_attribute_values": [
				{"attribute_value": "Blue", "abbr": "BL"},
				{"attribute_value": "Green", "abbr": "GR"},
			],
		}).insert()

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)
		frappe.set_user(self.previous_user)
		super().tearDown()

	def mapping(self, values, attribute=None):
		return frappe.get_doc({
			"doctype": "YRP Item Item Attribute Mapping",
			"attribute_name": attribute or self.attribute.name,
			"values": [{"attribute_value": value} for value in values],
		})

	def numeric_attribute(self):
		return frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": "_Test Mapping Gauge " + frappe.generate_hash(length=8),
			"numeric_values": 1, "from_range": 0, "to_range": 1000, "increment": 0.5,
		}).insert()

	def test_picker_returns_value_text_from_selected_attribute(self):
		self.assertEqual(search_attribute_values(self.attribute.name), ["Blue", "Green"])
		self.assertEqual(search_attribute_values(self.attribute.name, "blu"), ["Blue"])
		self.assertNotIn(self.attribute.item_attribute_values[0].name, search_attribute_values(self.attribute.name))
		self.assertEqual(search_attribute_values(), [])

	def test_picker_checks_attribute_document_permission(self):
		with patch.object(type(self.attribute), "check_permission", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				search_attribute_values(self.attribute.name)

	def test_valid_mapping_preserves_values_and_order(self):
		doc = self.mapping(["Green", "Blue"]).insert()
		doc.reload()
		self.assertEqual([row.attribute_value for row in doc.get("values")], ["Green", "Blue"])

	def test_invalid_value_rejects_insert_and_update(self):
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["Not configured"]).insert()
		doc = self.mapping(["Blue"]).insert()
		doc.get("values")[0].attribute_value = "Not configured"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		doc.reload()
		self.assertEqual(doc.get("values")[0].attribute_value, "Blue")

	def test_value_from_another_attribute_is_rejected(self):
		other = frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": "_Test Mapping Finish " + frappe.generate_hash(length=8),
			"item_attribute_values": [{"attribute_value": "Polished", "abbr": "P"}],
		}).insert()
		self.assertEqual(search_attribute_values(other.name), ["Polished"])
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["Polished"]).insert()

	def test_blank_and_duplicate_rows_are_rejected_but_empty_mapping_is_allowed(self):
		for values in ([""], ["Blue", "Blue"]):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				self.mapping(values).insert()
		# Parent Item/Template controllers intentionally create the empty mapping
		# before their inline attribute editor supplies a selection.
		self.mapping([]).insert()

	def test_numeric_values_follow_native_range_and_increment(self):
		attribute = self.numeric_attribute()
		self.mapping(["0", "0.5", "500"], attribute.name).insert()
		self.assertEqual(search_attribute_values(attribute.name, "500"), ["500"])
		self.assertLessEqual(len(search_attribute_values(attribute.name)), 99)
		for value in ("0.25", "1001", "arbitrary text", "NaN", "Infinity"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				self.mapping([value], attribute.name).insert()
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["1", "1.0"], attribute.name).insert()
