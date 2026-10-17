# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

"""Validate ERPNext attribute selections; store their YRP Link identities."""

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr

from yrp.attribute_links import value as actual_attribute_value
from yrp.attribute_values import normalize_mapping


class YRPItemItemAttributeMapping(Document):
	def save(self, *args, **kwargs):
		from yrp.yrp_retail.pricing import lock_pricing_policy

		owner_names = self._variant_owner_item_names()
		lock_pricing_policy()
		self._lock_variant_owner_items(owner_names)
		return super().save(*args, **kwargs)

	def validate(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import validate_attribute_value
		from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import (
			validate_mapping_unlocked,
		)

		self._validate_variant_owner_values_unchanged()
		validate_mapping_unlocked(self)
		if not self.attribute_name:
			frappe.throw(_("Select an Item Attribute before adding values."))
		attribute = frappe.get_cached_doc("Item Attribute", self.attribute_name)
		seen = set()
		for row in self.get("values") or []:
			stored = row.attribute_value
			if not stored:
				frappe.throw(_("Row {0}: Attribute Value is required.").format(row.idx))
			# Internal callers may provide ERPNext value text. Persisted rows use
			# the YRP Link; both validate against the same native attribute.
			value = actual_attribute_value(stored)
			key = value
			if attribute.numeric_values:
				key = _number(value)
				if key is None:
					frappe.throw(_("Row {0}: Attribute Value must be a finite number.").format(row.idx))
			validate_attribute_value(self.attribute_name, value)
			if key in seen:
				frappe.throw(_("Row {0}: Attribute Value {1} is repeated.").format(row.idx, value))
			seen.add(key)

	def _validate_variant_owner_values_unchanged(self):
		if (
			self.is_new()
			or self.flags.get("last_updated_by_sd_yrp_sync")
			or frappe.flags.get("in_migrate")
			or frappe.flags.get("in_patch")
		):
			return
		before = self.get_doc_before_save()
		if not before:
			return
		before_values = [row.attribute_value for row in before.get("values") or []]
		current_values = [row.attribute_value for row in self.get("values") or []]
		if before_values == current_values:
			return
		owners = self.flags.get("yrp_locked_variant_owner_items")
		if owners is None:
			from yrp.yrp_retail.pricing import lock_pricing_policy

			owner_names = self._variant_owner_item_names()
			lock_pricing_policy()
			owners = self._lock_variant_owner_items(owner_names)
		from yrp.yrp.doctype.yrp_item.yrp_item import template_has_variants

		for owner in owners:
			if owner.has_variants and template_has_variants(owner.name):
				frappe.throw(
					_("Attribute mapping values cannot be changed after variants exist for Item {0}.").format(
						owner.name
					)
				)

	def _variant_owner_item_names(self):
		if (
			self.is_new()
			or self.flags.get("last_updated_by_sd_yrp_sync")
			or frappe.flags.get("in_migrate")
			or frappe.flags.get("in_patch")
		):
			return []
		attribute = frappe.qb.DocType("Item Variant Attribute")
		return (
			frappe.qb.from_(attribute)
			.select(attribute.parent)
			.where(attribute.mapping == self.name)
			.where(attribute.parenttype == "Item")
			.where(attribute.parentfield == "attributes")
			.orderby(attribute.parent)
			.run(pluck=True)
		)

	def _lock_variant_owner_items(self, owner_names=None):
		if owner_names is None:
			owner_names = self._variant_owner_item_names()
		owners = [
			frappe.get_doc("Item", name, for_update=True)
			for name in sorted(set(owner_names))
		]
		owners = [
			owner
			for owner in owners
			if any(row.mapping == self.name for row in owner.get("attributes") or [])
		]
		self.flags.yrp_locked_variant_owner_items = owners
		return owners

	def _validate_links(self):
		normalize_mapping(self)
		super()._validate_links()


def _number(value):
	try:
		number = Decimal(cstr(value))
		return number if number.is_finite() else None
	except InvalidOperation:
		return None


ItemItemAttributeMapping = YRPItemItemAttributeMapping
