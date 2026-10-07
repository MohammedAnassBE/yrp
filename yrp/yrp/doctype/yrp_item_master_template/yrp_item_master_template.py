"""Item Master Template — reusable preset that prefills a new Item once.

Shares the same attribute/mapping/dependent-attribute structure as Item. Once
any Item links the template it is locked, so later edits never reach Items.
"""
from yrp.attribute_links import value as _attribute_value

import frappe
from yrp.attribute_values import get_mapping_document
from frappe import _
from frappe.model.document import Document

from yrp.yrp.doctype.yrp_item.yrp_item import _create_dependent_attribute_mapping
from yrp.yrp.doctype.yrp_item_dependent_attribute_mapping.yrp_item_dependent_attribute_mapping import (
	get_dependent_attribute_details,
)

PREFILL_FIELDS = {
	"stock_uom": "default_unit_of_measure",
	"secondary_unit_of_measure": "secondary_unit_of_measure",
	"primary_attribute": "primary_attribute",
	"dependent_attribute": "dependent_attribute",
	"dependent_attribute_mapping": "dependent_attribute_mapping",
	"yrp_item_type": "item_type",
}
PREFILL_TABLES = {
	"additional_parameters": "additional_parameters",
	"yrp_categories": "categories",
	"taxes": "taxes",
	"item_defaults": "item_defaults",
}


class YRPItemMasterTemplate(Document):

	def _save(self, *args, **kwargs):
		from yrp.yrp_retail.pricing import lock_pricing_policy
		lock_pricing_policy()
		return super()._save(*args, **kwargs)

	def onload(self):
		"""Load attribute list and dependent attribute details into __onload."""
		self._load_attribute_list()
		self._load_dependent_attribute()
		self.set_onload("has_linked_items", self.has_linked_items)

	def _load_attribute_list(self):
		"""Load each attribute's mapping values into __onload.attr_list."""
		attribute_list = []
		for attribute in self.attributes:
			attribute_doc = frappe.get_doc('Item Attribute', attribute.attribute)
			if attribute_doc.numeric_values:
				continue

			mapped_values = []
			if attribute.mapping:
				mapping_doc = get_mapping_document(attribute.mapping)
				mapped_values = mapping_doc.values

			attribute_list.append({
				"name": attribute.name,
				"attr_name": attribute.attribute,
				"attr_values_link": attribute.mapping,
				"attr_values": mapped_values,
				"doctype": 'YRP Item Item Attribute Mapping',
			})

		self.set_onload("attr_list", attribute_list)

	def _load_dependent_attribute(self):
		"""Load dependent attribute details into __onload.dependent_attribute."""
		dependent_attribute = {}
		if self.dependent_attribute and self.dependent_attribute_mapping:
			dependent_attribute = get_dependent_attribute_details(self.dependent_attribute_mapping)
		self.set_onload("dependent_attribute", dependent_attribute)

	@property
	def has_linked_items(self):
		return bool(frappe.db.exists("Item", {"yrp_item_master_template": self.name}))

	def prefill_item(self, item):
		"""Copy this template's values onto a new Item where the template has them."""
		from yrp.yrp_retail.item_template import rows_of

		for item_field, template_field in PREFILL_FIELDS.items():
			if self.get(template_field):
				item.set(item_field, self.get(template_field))
		item.yrp_is_free_item = self.is_free_item
		for item_table, template_table in PREFILL_TABLES.items():
			if self.get(template_table):
				item.set(item_table, rows_of(self.get(template_table)))
		if self.uom_conversion_details:
			item.set("uoms", [{"uom": row.uom, "conversion_factor": row.conversion_factor}
				for row in self.uom_conversion_details])
		if self.attributes:
			item.has_variants = 1
			item.set("attributes", [
				{"attribute": row.attribute, "mapping": row.mapping} for row in self.attributes
			])

	def validate(self):
		from yrp.yrp_retail.item_template import validate_template
		if not self.is_new() and self.has_linked_items:
			frappe.throw(_("YRP Item Master Template {0} is linked to Items and can no longer be edited.")
				.format(self.name))
		validate_template(self)
		self._validate_default_uom()
		self._validate_primary_attribute()
		self._duplicate_mappings_on_create()
		self._ensure_attribute_mappings_exist()
		self._validate_dependent_attribute()

	def on_update(self):
		from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import cleanup_owner_mappings

		cleanup_owner_mappings(self)

	def after_delete(self):
		from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import cleanup_owner_mappings

		cleanup_owner_mappings(self, deleted=True)

	def _validate_default_uom(self):
		"""Ensure default UOM is not a secondary-only UOM."""
		secondary_only = frappe.get_value('UOM', self.default_unit_of_measure, "secondary_only")
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
		"""Retain the independent lifecycle of the dependent-attribute mapping.

		Attribute mappings are cloned by ensure_owned_mappings instead.
		"""
		if not self.get("__islocal"):
			return

		if self.dependent_attribute and self.dependent_attribute_mapping:
			original = frappe.get_doc('YRP Item Dependent Attribute Mapping', self.dependent_attribute_mapping)
			copy = frappe.copy_doc(original)
			copy.save()
			self.dependent_attribute_mapping = copy.name
		elif not self.dependent_attribute and self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = None

	def _ensure_attribute_mappings_exist(self):
		"""Allocate missing maps and separate snapshots from every other owner."""
		from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import ensure_owned_mappings

		ensure_owned_mappings(self)

	def _validate_dependent_attribute(self):
		"""Validate dependent attribute setup."""
		if not self.dependent_attribute:
			if self.dependent_attribute_mapping:
				frappe.delete_doc('YRP Item Dependent Attribute Mapping', self.dependent_attribute_mapping)
				self.dependent_attribute_mapping = None
			return

		# Check dependent attribute is in the list and has values
		dependent_attr_values = []
		found = False
		for attribute in self.get("attributes"):
			if attribute.attribute == self.dependent_attribute:
				found = True
				mapping = get_mapping_document(attribute.mapping)
				if not mapping.values:
					frappe.throw(
						f"Please set {self.dependent_attribute} values before setting it as Dependent Attribute"
					)
				dependent_attr_values = [_attribute_value(v.attribute_value) for v in mapping.values]
				break

		if not found:
			frappe.throw(f"{self.dependent_attribute} is not in the attribute list")
		if not self.primary_attribute:
			frappe.throw("Please set Primary Attribute for this Item")

		if not self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = _create_dependent_attribute_mapping(
				self, dependent_attr_values
			)


def validate_mapping_unlocked(mapping):
	"""A linked template's attribute and dependent mappings are locked with it."""
	if mapping.is_new():
		return
	if mapping.doctype == "YRP Item Dependent Attribute Mapping":
		templates = frappe.get_all("YRP Item Master Template",
			filters={"dependent_attribute_mapping": mapping.name}, pluck="name")
	else:
		templates = frappe.get_all("YRP Item Item Attribute",
			filters={"parenttype": "YRP Item Master Template", "mapping": mapping.name}, pluck="parent")
	for name in templates:
		if frappe.db.exists("Item", {"yrp_item_master_template": name}):
			frappe.throw(_("YRP Item Master Template {0} is linked to Items, so its mapping {1} can no longer be edited.")
				.format(name, mapping.name))


@frappe.whitelist(methods=["POST"])
def create_item_from_template(template_name, item_name, item_group, gst_hsn_code=None):
	"""Create an Item linked to the template; Item before_validate applies the prefill."""
	item = frappe.new_doc("Item")
	item.yrp_item_master_template = template_name
	item.item_code = item_name
	item.item_name = item_name
	item.item_group = item_group
	if item.meta.has_field("gst_hsn_code"):
		item.gst_hsn_code = gst_hsn_code
	item.insert()
	return item.name


ItemMasterTemplate = YRPItemMasterTemplate
