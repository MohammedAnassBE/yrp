"""Synthetic attribute mappings; storage Links and business values stay distinct."""

from unittest import TestCase

import frappe

from yrp.attribute_values import MASTER, get_mapping_values


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

	def test_link_master_contains_only_selected_attribute_values(self):
		field = frappe.get_meta("YRP Item Item Attribute Mapping Value").get_field("attribute_value")
		self.assertEqual((field.fieldtype, field.options), ("Link", MASTER))
		values = set(frappe.get_list(MASTER, filters={"attribute_name": self.attribute.name},
			pluck="attribute_value"))
		self.assertEqual(values, {"Blue", "Green"})

	def test_valid_mapping_stores_links_and_preserves_business_order(self):
		doc = self.mapping(["Green", "Blue"]).insert()
		doc.reload()
		self.assertTrue(all(row.attribute_value.startswith("IAV-") for row in doc.values))
		self.assertEqual(get_mapping_values(doc.name), ["Green", "Blue"])

	def test_invalid_value_rejects_insert_and_update(self):
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["Not configured"]).insert()
		doc = self.mapping(["Blue"]).insert()
		doc.values[0].attribute_value = "Not configured"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		doc.reload()
		self.assertEqual(get_mapping_values(doc.name), ["Blue"])

	def test_value_from_another_attribute_is_rejected(self):
		frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": "_Test Mapping Finish " + frappe.generate_hash(length=8),
			"item_attribute_values": [{"attribute_value": "Polished", "abbr": "P"}],
		}).insert()
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["Polished"]).insert()

	def test_blank_and_duplicate_rows_are_rejected_but_empty_mapping_is_allowed(self):
		for values in ([""], ["Blue", "Blue"]):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				self.mapping(values).insert()
		self.mapping([]).insert()

	def test_numeric_values_follow_native_range_and_increment(self):
		attribute = self.numeric_attribute()
		doc = self.mapping(["0", "0.5", "500"], attribute.name).insert()
		self.assertEqual(get_mapping_values(doc.name), ["0", "0.5", "500"])
		for value in ("0.25", "1001", "arbitrary text", "NaN", "Infinity"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				self.mapping([value], attribute.name).insert()
		with self.assertRaises(frappe.ValidationError):
			self.mapping(["1", "1.0"], attribute.name).insert()
