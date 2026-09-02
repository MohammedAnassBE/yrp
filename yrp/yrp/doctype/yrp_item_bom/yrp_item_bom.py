import frappe
from frappe import _
from frappe.model.document import Document


class YRPItemBOM(Document):
	def validate(self):
		if self.based_on_attribute_mapping and not self.attribute_mapping:
			frappe.throw("Attribute Mapping is required when Based on Attribute Mapping is checked.")
		if self.qty_of_product <= 0 or self.qty_of_bom_item <= 0:
			frappe.throw("Qty of Product and Qty of BOM Item must be positive.")


def validate_bom_item_variant_mapping(bom_row):
	"""Require an attribute mapping before a BOM row can mint Item Variants.

	Legacy databases can contain an empty Item Variant whose name is identical to
	the parent Item. Tuple lookup can resolve that record without ever reaching
	``create_variant`` (where missing attributes are normally rejected). BOM
	calculation therefore validates the authored mapping before any lookup.
	"""
	item = bom_row.get("item")
	if not item:
		return

	item_doc = frappe.get_cached_doc('YRP Item', item)
	item_attributes = {
		row.attribute for row in item_doc.get("attributes") or [] if row.attribute
	}
	if not item_attributes:
		return

	row_label = _("row {0}").format(bom_row.get("idx") or "?")
	mapping_name = bom_row.get("attribute_mapping")
	if not bom_row.get("based_on_attribute_mapping") or not mapping_name:
		frappe.throw(
			_(
				"BOM item {0} declares variant attributes ({1}), but Item BOM {2} "
				"has no attribute mapping. Enable Based on Attribute Mapping and "
				"select its mapping before calculating."
			).format(item, ", ".join(sorted(item_attributes)), row_label)
		)


ItemBOM = YRPItemBOM
