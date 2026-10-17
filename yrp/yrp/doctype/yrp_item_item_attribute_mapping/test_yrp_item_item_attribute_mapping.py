"""Synthetic attribute mappings; storage Links and business values stay distinct."""

from unittest import TestCase

import frappe

from yrp.attribute_value_identity import attribute_value_name
from yrp.attribute_values import MASTER, get_mapping_values
from yrp.yrp.doctype.yrp_item.test_yrp_item import _item
from yrp.yrp.doctype.yrp_item.yrp_item import get_or_create_variant


class TestYRPItemItemAttributeMapping(TestCase):
	def setUp(self):
		super().setUp()
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		self.savepoint = "mapping_picker_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.savepoint)
		self.attribute = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": "_Test Mapping Colour " + frappe.generate_hash(length=8),
				"item_attribute_values": [
					{"attribute_value": "Blue", "abbr": "BL"},
					{"attribute_value": "Green", "abbr": "GR"},
				],
			}
		).insert()

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)
		frappe.set_user(self.previous_user)
		super().tearDown()

	def mapping(self, values, attribute=None):
		return frappe.get_doc(
			{
				"doctype": "YRP Item Item Attribute Mapping",
				"attribute_name": attribute or self.attribute.name,
				"values": [{"attribute_value": value} for value in values],
			}
		)

	def numeric_attribute(self):
		return frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": "_Test Mapping Gauge " + frappe.generate_hash(length=8),
				"numeric_values": 1,
				"from_range": 0,
				"to_range": 1000,
				"increment": 0.5,
			}
		).insert()

	def test_link_master_contains_only_selected_attribute_values(self):
		field = frappe.get_meta("YRP Item Item Attribute Mapping Value").get_field("attribute_value")
		self.assertEqual((field.fieldtype, field.options), ("Link", MASTER))
		values = set(
			frappe.get_list(MASTER, filters={"attribute_name": self.attribute.name}, pluck="attribute_value")
		)
		self.assertEqual(values, {"Blue", "Green"})

	def test_valid_mapping_stores_links_and_preserves_business_order(self):
		doc = self.mapping(["Green", "Blue"]).insert()
		doc.reload()
		self.assertEqual(
			[row.attribute_value for row in doc.values],
			[attribute_value_name(self.attribute.name, value) for value in ("Green", "Blue")],
		)
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
		frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": "_Test Mapping Finish " + frappe.generate_hash(length=8),
				"item_attribute_values": [{"attribute_value": "Polished", "abbr": "P"}],
			}
		).insert()
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

	def variant_mapping(self, values):
		mapping = self.mapping(values).insert()
		template = _item(
			"_Test Mapping Template " + frappe.generate_hash(length=8),
			has_variants=1,
			attributes=[{"attribute": self.attribute.name, "mapping": mapping.name}],
		)
		variant = get_or_create_variant(template, {self.attribute.name: "Blue"})
		owner = frappe.get_doc("Item", template)
		return frappe.get_doc(mapping.doctype, owner.attributes[0].mapping), variant

	def test_append_preserves_existing_variant_and_allows_new_variant(self):
		mapping, variant = self.variant_mapping(["Blue"])
		original = frappe.get_doc("Item", variant)
		mapping.append("values", {"attribute_value": "Green"})
		mapping.save()
		self.assertEqual(get_mapping_values(mapping.name), ["Blue", "Green"])
		self.assertEqual(get_or_create_variant(original.variant_of, {self.attribute.name: "Blue"}), variant)
		new_variant = get_or_create_variant(original.variant_of, {self.attribute.name: "Green"})
		self.assertNotEqual(new_variant, variant)
		self.assertEqual(
			[row.attribute_value for row in frappe.get_doc("Item", variant).attributes],
			[row.attribute_value for row in original.attributes],
		)

	def test_existing_values_cannot_be_removed_replaced_or_reordered(self):
		mapping, _variant = self.variant_mapping(["Blue", "Green"])
		for values in (["Green"], ["Blue"], [], ["Green", "Blue"]):
			with self.subTest(values=values):
				mapping.reload()
				mapping.set("values", [{"attribute_value": value} for value in values])
				with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
					mapping.save()
		self.assertEqual(get_mapping_values(mapping.name), ["Blue", "Green"])

	def test_empty_mapping_cannot_be_restricted_after_variants_exist(self):
		mapping, _variant = self.variant_mapping([])
		mapping.append("values", {"attribute_value": "Green"})
		with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
			mapping.save()

	def test_unchanged_plain_values_normalize_to_existing_links(self):
		mapping, _variant = self.variant_mapping(["Blue"])
		mapping.values[0].attribute_value = "Blue"
		mapping.save()
		self.assertEqual(mapping.values[0].attribute_value, attribute_value_name(self.attribute.name, "Blue"))
