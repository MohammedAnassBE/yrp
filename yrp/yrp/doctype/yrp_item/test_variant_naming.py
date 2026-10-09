"""Variant naming and lookup match production_api; fictional fixtures, rolled back."""
import unittest

import frappe

from yrp.yrp.doctype.yrp_item.yrp_item import (
	_create_dependent_attribute_mapping,
	get_or_create_variant,
	get_variant,
)


def _hash():
	return frappe.generate_hash(length=10)


class TestVariantNaming(unittest.TestCase):
	def setUp(self):
		self.savepoint = "variant_naming_" + _hash()
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": "Test Unit " + _hash()}).insert().name
		self.size = self.attribute("Test Size", ["S", "M"])
		self.stage = self.attribute("Test Stage", ["Pack"])

	def attribute(self, prefix, values):
		return frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": f"{prefix} {_hash()}",
			"item_attribute_values": [{"attribute_value": value, "abbr": value} for value in values],
		}).insert().name

	def attribute_mapping(self, attribute, values):
		return frappe.get_doc({
			"doctype": "YRP Item Item Attribute Mapping",
			"attribute_name": attribute,
			"values": [{"attribute_value": value} for value in values],
		}).insert().name

	def template_item(self):
		group = frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Test Group " + _hash(),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name"),
		}).insert().name
		item = {
			"doctype": "Item",
			"item_code": "Test Variant Template " + _hash(),
			"item_group": group,
			"stock_uom": self.uom,
			"has_variants": 1,
			"variant_based_on": "Item Attribute",
			"attributes": [
				{"attribute": self.size, "mapping": self.attribute_mapping(self.size, ["S", "M"])},
				{"attribute": self.stage, "mapping": self.attribute_mapping(self.stage, ["Pack"])},
			],
		}
		if frappe.get_meta("Item").has_field("gst_hsn_code"):
			item["gst_hsn_code"] = frappe.get_doc({
				"doctype": "GST HSN Code",
				"hsn_code": str(10_000_000 + int(_hash()[:6], 16) % 89_999_999),
				"description": "Fictional test classification",
			}).insert().name
		template = frappe.get_doc(item).insert()
		# As in production, the dependent setup is added once mappings exist.
		template.update({"primary_attribute": self.size, "dependent_attribute": self.stage})
		return template.save()

	def test_master_template_dependent_mapping(self):
		template = frappe.get_doc({
			"doctype": "YRP Item Master Template",
			"name": "Test Master " + _hash(),
			"default_unit_of_measure": self.uom,
			"primary_attribute": self.size,
			"dependent_attribute": self.stage,
		})
		mapping = frappe.get_doc(
			"YRP Item Dependent Attribute Mapping",
			_create_dependent_attribute_mapping(template, ["Pack"]),
		)
		self.assertFalse(mapping.item)
		self.assertEqual([row.uom for row in mapping.details], [self.uom])

	def test_item_dependent_mapping_links_item(self):
		template = self.template_item()
		mapping = frappe.get_doc("YRP Item Dependent Attribute Mapping", template.dependent_attribute_mapping)
		self.assertEqual(mapping.item, template.name)
		self.assertEqual([row.uom for row in mapping.details], [self.uom])

	def test_attribute_fallback_heals_tuple(self):
		template = self.template_item()
		args = {self.stage: "Pack", self.size: "S"}
		variant = get_or_create_variant(template.name, args)
		self.assertEqual(variant, f"{template.name}-S")
		expected = frappe.db.get_value("Item", variant, "item_tuple_attribute")
		frappe.db.set_value("Item", variant, "item_tuple_attribute", None, update_modified=False)
		self.assertEqual(get_variant(template.name, args), variant)
		self.assertEqual(frappe.db.get_value("Item", variant, "item_tuple_attribute"), expected)

	def test_extra_args_reuse_existing_variant(self):
		template = self.template_item()
		variant = get_or_create_variant(template.name, {self.stage: "Pack", self.size: "M"})
		again = get_or_create_variant(
			template.name, {self.stage: "Pack", self.size: "M", "Unknown Attribute": "X"}
		)
		self.assertEqual(again, variant)
