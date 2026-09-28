"""A linkable projection of an ERPNext Item Attribute's actual value."""

import frappe
from frappe import _
from frappe.model.document import Document


class YRPItemAttributeValue(Document):
	def before_insert(self):
		self.attribute_name = frappe.db.get_value("Item Attribute", self.attribute_name, "name") or self.attribute_name

	def autoname(self):
		from yrp.attribute_values import attribute_value_name
		self.name = attribute_value_name(self.attribute_name, self.attribute_value)

	def validate(self):
		old = self.get_doc_before_save()
		if old and (old.attribute_name, old.attribute_value) != (self.attribute_name, self.attribute_value):
			frappe.throw(_("Attribute/value identities cannot be edited. Create a new value instead."))
		if not self.flags.from_item_attribute:
			if self.attribute_value != self.attribute_value.strip():
				frappe.throw(_("Attribute values cannot start or end with whitespace."))
			frappe.get_doc("Item Attribute", self.attribute_name).check_permission("write")

	def after_insert(self):
		if not self.flags.from_item_attribute:
			from yrp.yrp.doctype.yrp_item.yrp_item import ensure_global_attribute_values
			if frappe.get_cached_value("Item Attribute", self.attribute_name, "numeric_values"):
				from erpnext.controllers.item_variant import validate_is_incremental
				validate_is_incremental(frappe.get_cached_doc("Item Attribute", self.attribute_name), self.attribute_name, self.attribute_value, self.name)
			else:
				ensure_global_attribute_values(self.attribute_name, [self.attribute_value])

	def on_trash(self):
		# ERPNext remains the domain authority. Removing its child value triggers
		# deletion here; a direct delete would leave an incomplete Link catalogue.
		if frappe.db.exists("Item Attribute Value", {"parent": self.attribute_name, "attribute_value": self.attribute_value}):
			frappe.throw(_("Remove this value from Item Attribute {0}; used values cannot be removed.").format(self.attribute_name))


ItemAttributeValue = YRPItemAttributeValue
