"""Item Master Template — reusable template for creating Items with pre-filled attributes.

Shares the same attribute/mapping/dependent-attribute structure as Item,
so users can create multiple Items from the same template without
re-configuring attributes each time.
"""

import frappe
from frappe.model.document import Document

from yrp.yrp.doctype.item.item import _create_dependent_attribute_mapping
from yrp.yrp.doctype.item_dependent_attribute_mapping.item_dependent_attribute_mapping import (
	get_dependent_attribute_details,
)


class ItemMasterTemplate(Document):

	def onload(self):
		"""Load attribute list and dependent attribute details into __onload."""
		self._load_attribute_list()
		self._load_dependent_attribute()

	def _load_attribute_list(self):
		"""Load each attribute's mapping values into __onload.attr_list."""
		attribute_list = []
		for attribute in self.attributes:
			attribute_doc = frappe.get_doc("Item Attribute", attribute.attribute)
			if attribute_doc.numeric_values:
				continue

			mapped_values = []
			if attribute.mapping:
				mapping_doc = frappe.get_doc("Item Item Attribute Mapping", attribute.mapping)
				mapped_values = mapping_doc.values

			attribute_list.append({
				"name": attribute.name,
				"attr_name": attribute.attribute,
				"attr_values_link": attribute.mapping,
				"attr_values": mapped_values,
				"doctype": "Item Item Attribute Mapping",
			})

		self.set_onload("attr_list", attribute_list)

	def _load_dependent_attribute(self):
		"""Load dependent attribute details into __onload.dependent_attribute."""
		dependent_attribute = {}
		if self.dependent_attribute and self.dependent_attribute_mapping:
			dependent_attribute = get_dependent_attribute_details(self.dependent_attribute_mapping)
		self.set_onload("dependent_attribute", dependent_attribute)

	def validate(self):
		self._validate_default_uom()
		self._validate_primary_attribute()
		self._duplicate_mappings_on_create()
		self._ensure_attribute_mappings_exist()
		self._validate_dependent_attribute()

	def _validate_default_uom(self):
		"""Ensure default UOM is not a secondary-only UOM."""
		secondary_only = frappe.get_value("UOM", self.default_unit_of_measure, "secondary_only")
		if secondary_only:
			frappe.throw(f"{self.default_unit_of_measure} can only be used as Secondary UOM")

	def _validate_primary_attribute(self):
		"""Ensure primary attribute is in the attribute list."""
		if not self.primary_attribute:
			return
		attribute_names = [attr.attribute for attr in self.attributes]
		if self.primary_attribute not in attribute_names:
			frappe.throw("Default Attribute must be in Attribute List")

	def _duplicate_mappings_on_create(self):
		"""On new template creation, duplicate shared mappings so each template has its own copy."""
		if not self.get("__islocal"):
			return

		for attribute in self.get("attributes"):
			if attribute.mapping:
				original = frappe.get_doc("Item Item Attribute Mapping", attribute.mapping)
				copy = frappe.copy_doc(original)
				copy.save()
				attribute.mapping = copy.name

		if self.dependent_attribute and self.dependent_attribute_mapping:
			original = frappe.get_doc("Item Dependent Attribute Mapping", self.dependent_attribute_mapping)
			copy = frappe.copy_doc(original)
			copy.save()
			self.dependent_attribute_mapping = copy.name
		elif not self.dependent_attribute and self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = None

	def _ensure_attribute_mappings_exist(self):
		"""Create empty mapping docs for attributes that don't have one yet."""
		for attribute in self.get("attributes"):
			if attribute.mapping is None:
				mapping = frappe.new_doc("Item Item Attribute Mapping")
				mapping.attribute_name = attribute.attribute
				mapping.save()
				attribute.mapping = mapping.name

	def _validate_dependent_attribute(self):
		"""Validate dependent attribute setup."""
		if not self.dependent_attribute:
			if self.dependent_attribute_mapping:
				frappe.delete_doc("Item Dependent Attribute Mapping", self.dependent_attribute_mapping)
				self.dependent_attribute_mapping = None
			return

		# Check dependent attribute is in the list and has values
		dependent_attr_values = []
		found = False
		for attribute in self.get("attributes"):
			if attribute.attribute == self.dependent_attribute:
				found = True
				mapping = frappe.get_doc("Item Item Attribute Mapping", attribute.mapping)
				if not mapping.values:
					frappe.throw(
						f"Please set {self.dependent_attribute} values before setting it as Dependent Attribute"
					)
				dependent_attr_values = [v.attribute_value for v in mapping.values]
				break

		if not found:
			frappe.throw(f"{self.dependent_attribute} is not in the attribute list")
		if not self.primary_attribute:
			frappe.throw("Please set Primary Attribute for this Item")

		if not self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = _create_dependent_attribute_mapping(
				self, dependent_attr_values
			)


@frappe.whitelist()
def create_item_from_template(template_name, item_name, item_group):
	"""Create a new Item from a template, copying all attributes and mappings."""
	template = frappe.get_doc("Item Master Template", template_name)

	item = frappe.new_doc("Item")
	item.name1 = item_name
	item.item_group = item_group
	item.default_unit_of_measure = template.default_unit_of_measure
	item.secondary_unit_of_measure = template.secondary_unit_of_measure
	item.primary_attribute = template.primary_attribute
	item.dependent_attribute = template.dependent_attribute
	item.dependent_attribute_mapping = template.dependent_attribute_mapping

	for row in template.uom_conversion_details:
		item.append("uom_conversion_details", {
			"uom": row.uom,
			"conversion_factor": row.conversion_factor,
		})

	for row in template.attributes:
		item.append("attributes", {
			"attribute": row.attribute,
			"mapping": row.mapping,
		})

	for row in template.additional_parameters:
		item.append("additional_parameters", {
			"additional_parameter_key": row.additional_parameter_key,
			"additional_parameter_value": row.additional_parameter_value,
		})

	item.insert()
	return item.name
