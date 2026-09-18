"""YRP behavior layered onto ERPNext's standard Item master.

The module path is retained because it is a stable public API used by the YRP
and Essdee frontends. The old YRP Item/Variant DocTypes are no longer storage
authorities: templates, variants and standalone stock items are standard Items.
"""

import copy
import json

import frappe
from frappe import _
from frappe.utils import cstr

from yrp.yrp.doctype.yrp_item_dependent_attribute_mapping.yrp_item_dependent_attribute_mapping import (
	get_dependent_attribute_details,
)


_YRP_VARIANT_IDENTITY_FIELDS = (
	"attributes",
	"primary_attribute",
	"dependent_attribute",
	"dependent_attribute_mapping",
	"item_hash_value",
	"item_tuple_attribute",
)


def _call_super(instance, method_name, *args, **kwargs):
	method = getattr(super(YRPItemMixin, instance), method_name, None)
	if method:
		return method(*args, **kwargs)
	return None


def get_parent_item(item_code):
	"""Resolve a native variant through variant_of; standalone Items resolve to themselves."""
	if not item_code:
		return None
	variant_of = frappe.get_cached_value("Item", item_code, "variant_of")
	return variant_of or (item_code if frappe.db.exists("Item", item_code) else None)


def get_global_attribute_values(attribute):
	if not attribute:
		return []
	return frappe.get_all(
		"Item Attribute Value",
		filters={
			"parent": attribute,
			"parenttype": "Item Attribute",
			"parentfield": "item_attribute_values",
		},
		pluck="attribute_value",
		order_by="idx asc",
	)


def has_attribute_value(attribute, value):
	if not attribute or value in (None, ""):
		return False
	return bool(
		frappe.db.exists(
			"Item Attribute Value",
			{
				"parent": attribute,
				"parenttype": "Item Attribute",
				"parentfield": "item_attribute_values",
				"attribute_value": value,
			},
		)
	)


def ensure_global_attribute_values(attribute, values, *, check_permission=True):
	"""Append missing values to ERPNext's canonical Item Attribute child table."""
	if not frappe.db.exists("Item Attribute", attribute):
		frappe.throw(_("Item Attribute {0} does not exist.").format(attribute))
	# Serialize concurrent inline editors before re-reading the child rows.
	frappe.db.get_value("Item Attribute", attribute, "name", for_update=True)
	doc = frappe.get_doc("Item Attribute", attribute)
	if check_permission:
		doc.check_permission("write")
	existing = {row.attribute_value for row in doc.get("item_attribute_values") or []}
	used_abbrs = {
		cstr(row.abbr).strip().casefold()
		for row in doc.get("item_attribute_values") or []
		if row.abbr
	}
	changed = False
	for value in values or []:
		value = cstr(value).strip()
		if not value or value in existing:
			continue
		abbr = value
		suffix = 2
		while abbr.casefold() in used_abbrs:
			abbr = f"{value}-{suffix}"
			suffix += 1
		doc.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
		existing.add(value)
		used_abbrs.add(abbr.casefold())
		changed = True
	if changed:
		doc.save(ignore_permissions=not check_permission)
	return [row.attribute_value for row in doc.get("item_attribute_values") or []]


def validate_attribute_value(attribute, value):
	if not attribute or value in (None, ""):
		return
	attribute_doc = frappe.get_cached_doc("Item Attribute", attribute)
	if attribute_doc.numeric_values:
		from erpnext.controllers.item_variant import validate_is_incremental

		validate_is_incremental(attribute_doc, attribute, value, attribute)
		return
	if value not in get_global_attribute_values(attribute):
		frappe.throw(
			_("Attribute Value {0} is not valid for Item Attribute {1}.").format(
				frappe.bold(value), frappe.bold(attribute)
			)
		)


class YRPItemMixin:
	"""YRP template rules and YRP-ledger integrity for standard Item."""

	def before_validate(self):
		_call_super(self, "before_validate")
		if self.meta.has_field("item_tuple_attribute") and not self.item_tuple_attribute:
			self.item_tuple_attribute = None
		if not self.get("item_hash_value"):
			from frappe.model.naming import make_autoname

			self.item_hash_value = make_autoname(key="hash")

	def onload(self):
		_call_super(self, "onload")
		if self.has_variants and not self.variant_of:
			self._load_yrp_attribute_list()
			self._load_yrp_dependent_attribute()

	def validate(self):
		_call_super(self, "validate")
		if self.has_variants and not self.variant_of:
			self._validate_primary_attribute()
			if not self.flags.get("last_updated_by_sd_yrp_sync"):
				self._duplicate_mappings_on_create()
				self._ensure_attribute_mappings_exist()
				self._validate_dependent_attribute()
		self._validate_secondary_uom()
		self._validate_yrp_stock_mutation()
		self._validate_tuple_uniqueness()

	def on_trash(self):
		if frappe.db.exists("YRP Stock Ledger Entry", {"item": self.name}):
			frappe.throw(_("Cannot delete Item {0} because YRP stock ledger entries exist.").format(self.name))
		if frappe.db.exists("YRP Bin", {"item_code": self.name}):
			frappe.throw(_("Cannot delete Item {0} because YRP stock balances exist.").format(self.name))
		return _call_super(self, "on_trash")

	def update_variants(self):
		"""Propagate template fields without overwriting YRP variant identity.

		ERPNext copies every field configured in ``Variant Field``. Historical
		sites can contain YRP identity fields in that allowlist, but those fields
		are template-only or independently generated for each physical variant.
		"""
		if self.flags.dont_update_variants or frappe.db.get_single_value(
			"Item Variant Settings", "do_not_update_variants"
		):
			return
		if not self.has_variants:
			return
		variants = frappe.db.get_all("Item", fields=["item_code"], filters={"variant_of": self.name})
		if not variants:
			return
		if len(variants) <= 30:
			update_yrp_variants(variants, self, publish_progress=False)
			frappe.msgprint(_("Item Variants updated"))
		else:
			frappe.enqueue(
				"yrp.yrp.doctype.yrp_item.yrp_item.update_yrp_variants",
				variants=variants,
				template=self,
				now=frappe.in_test,
				timeout=600,
				enqueue_after_commit=True,
			)

	def _load_yrp_attribute_list(self):
		attribute_list = []
		for attribute in self.get("attributes") or []:
			attribute_doc = frappe.get_cached_doc("Item Attribute", attribute.attribute)
			if attribute_doc.numeric_values:
				continue
			mapped_values = []
			if attribute.get("mapping"):
				mapping_doc = frappe.get_cached_doc("YRP Item Item Attribute Mapping", attribute.mapping)
				mapped_values = mapping_doc.values
			attribute_list.append(
				{
					"name": attribute.name,
					"attr_name": attribute.attribute,
					"attr_values_link": attribute.get("mapping"),
					"attr_values": mapped_values,
					"doctype": "YRP Item Item Attribute Mapping",
				}
			)
		self.set_onload("attr_list", attribute_list)

	def _load_yrp_dependent_attribute(self):
		details = {}
		if self.dependent_attribute and self.dependent_attribute_mapping:
			details = get_dependent_attribute_details(self.dependent_attribute_mapping)
		self.set_onload("dependent_attribute", details)

	def _validate_secondary_uom(self):
		if self.stock_uom and frappe.get_cached_value("UOM", self.stock_uom, "secondary_only"):
			frappe.throw(_("{0} can only be used as Secondary UOM").format(self.stock_uom))

	def _validate_primary_attribute(self):
		if not self.primary_attribute:
			return
		attribute_names = [row.attribute for row in self.get("attributes") or []]
		if self.primary_attribute not in attribute_names:
			frappe.throw(_("Primary Attribute must be in the standard Variant Attributes table."))

	def _duplicate_mappings_on_create(self):
		if not self.is_new():
			return
		for attribute in self.get("attributes") or []:
			if attribute.get("mapping"):
				clone = frappe.copy_doc(frappe.get_doc("YRP Item Item Attribute Mapping", attribute.mapping))
				clone.save()
				attribute.mapping = clone.name
		if self.dependent_attribute and self.dependent_attribute_mapping:
			clone = frappe.copy_doc(
				frappe.get_doc("YRP Item Dependent Attribute Mapping", self.dependent_attribute_mapping)
			)
			clone.save()
			self.dependent_attribute_mapping = clone.name
		elif not self.dependent_attribute:
			self.dependent_attribute_mapping = None

	def _ensure_attribute_mappings_exist(self):
		for attribute in self.get("attributes") or []:
			if not attribute.get("mapping"):
				mapping = frappe.new_doc("YRP Item Item Attribute Mapping")
				mapping.attribute_name = attribute.attribute
				mapping.save()
				attribute.mapping = mapping.name
			mapping = frappe.get_cached_doc("YRP Item Item Attribute Mapping", attribute.mapping)
			for row in mapping.get("values") or []:
				validate_attribute_value(attribute.attribute, row.attribute_value)

	def _validate_dependent_attribute(self):
		if not self.dependent_attribute:
			if self.dependent_attribute_mapping:
				frappe.delete_doc("YRP Item Dependent Attribute Mapping", self.dependent_attribute_mapping)
				self.dependent_attribute_mapping = None
			return

		dependent_values = []
		for attribute in self.get("attributes") or []:
			if attribute.attribute != self.dependent_attribute:
				continue
			mapping = frappe.get_doc("YRP Item Item Attribute Mapping", attribute.mapping)
			dependent_values = [row.attribute_value for row in mapping.get("values") or []]
			break
		else:
			frappe.throw(_("Dependent Attribute must be in the standard Variant Attributes table."))

		if not dependent_values:
			frappe.throw(_("Please configure values for Dependent Attribute {0}.").format(self.dependent_attribute))
		if not self.primary_attribute:
			frappe.throw(_("Please set Primary Attribute for this Item."))
		if not self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = _create_dependent_attribute_mapping(self, dependent_values)

	def _validate_yrp_stock_mutation(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if before.get("allow_negative_stock") and not self.get("allow_negative_stock"):
			negative_exists = frappe.db.sql(
				"""
				SELECT 1
				FROM `tabYRP Bin` b
				WHERE b.actual_qty < 0
					AND b.item_code IN (
						SELECT i.name
						FROM `tabItem` i
						WHERE i.name = %(item)s OR i.variant_of = %(item)s
					)
				LIMIT 1
				""",
				{"item": self.name},
			)
			if negative_exists:
				frappe.throw(
					_(
						"Cannot disable Allow Negative Stock because Item {0} or one of "
						"its variants has a negative YRP stock balance. Replenish it first."
					).format(self.name)
				)
		if not _item_family_has_yrp_stock(self.name):
			return
		restricted = (
			"stock_uom",
			"is_stock_item",
			"variant_of",
			"has_variants",
			"primary_attribute",
			"dependent_attribute",
			"dependent_attribute_mapping",
			"item_hash_value",
			"item_tuple_attribute",
		)
		changed = [field for field in restricted if before.get(field) != self.get(field)]
		before_attrs = [
			(r.attribute, r.attribute_value, r.get("mapping"))
			for r in before.get("attributes") or []
		]
		current_attrs = [
			(r.attribute, r.attribute_value, r.get("mapping"))
			for r in self.get("attributes") or []
		]
		if before_attrs != current_attrs:
			changed.append("attributes")
		if changed:
			frappe.throw(
				_("Cannot change {0} after YRP stock exists for Item {1}.").format(
					", ".join(changed), self.name
				)
			)

	def _validate_tuple_uniqueness(self):
		if not self.variant_of or not self.item_tuple_attribute:
			return
		duplicate = frappe.db.exists(
			"Item",
			{
				"variant_of": self.variant_of,
				"item_tuple_attribute": self.item_tuple_attribute,
				"name": ["!=", self.name or ""],
			},
		)
		if duplicate:
			frappe.throw(_("Item {0} already has the same YRP attribute tuple.").format(duplicate))


def _item_family_has_yrp_stock(item):
	"""Return whether an Item or one of its physical variants has YRP stock state."""
	return bool(
		frappe.db.sql(
			"""
			select 1
			from `tabItem` item
			where (item.name = %(item)s or item.variant_of = %(item)s)
			  and (
				exists (select 1 from `tabYRP Bin` bin where bin.item_code = item.name)
				or exists (
					select 1 from `tabYRP Stock Ledger Entry` sle
					where sle.item = item.name
				)
				or exists (
					select 1 from `tabYRP Stock Reservation Entry` reservation
					where reservation.item_code = item.name
				)
			  )
			limit 1
			""",
			{"item": item},
		)
	)


def update_yrp_variants(variants, template, publish_progress=True):
	"""ERPNext variant propagation with YRP identity fields kept intact."""
	from erpnext.controllers.item_variant import copy_attributes_to_variant

	total = len(variants)
	for count, row in enumerate(variants, start=1):
		variant = frappe.get_doc("Item", row)
		identity = {
			fieldname: copy.deepcopy(variant.get(fieldname))
			for fieldname in _YRP_VARIANT_IDENTITY_FIELDS
		}
		copy_attributes_to_variant(template, variant)
		for fieldname, value in identity.items():
			variant.set(fieldname, value)
		variant.save()
		if publish_progress:
			frappe.publish_progress(count / total * 100, title=_("Updating Variants..."))


def _create_dependent_attribute_mapping(doc, attr_list):
	mapping = frappe.new_doc("YRP Item Dependent Attribute Mapping")
	mapping.item = doc.name
	mapping.dependent_attribute = doc.dependent_attribute
	mapping.set("details", [{"attribute_value": value, "uom": doc.stock_uom} for value in attr_list])
	mapping.set(
		"mapping",
		[
			{"dependent_attribute_value": value, "depending_attribute": doc.primary_attribute}
			for value in attr_list
		],
	)
	mapping.save()
	return mapping.name


@frappe.whitelist()
def update_dependent_attribute_details(dependent_attribute_mapping, detail):
	if isinstance(detail, str):
		detail = json.loads(detail)
	mapping = frappe.get_doc("YRP Item Dependent Attribute Mapping", dependent_attribute_mapping)
	details = []
	mapping_rows = []
	for value, config in detail["attr_list"].items():
		validate_attribute_value(mapping.dependent_attribute, value)
		details.append(
			{"attribute_value": value, "uom": config["uom"], "display_name": config["name"]}
		)
		mapping_rows.extend(
			{"dependent_attribute_value": value, "depending_attribute": attribute}
			for attribute in config["attributes"]
		)
	mapping.set("mapping", mapping_rows)
	mapping.set("details", details)
	mapping.save()
	return mapping


def _mapping_values(attribute_row):
	if attribute_row.get("mapping"):
		mapping = frappe.get_cached_doc("YRP Item Item Attribute Mapping", attribute_row.mapping)
		values = [row.attribute_value for row in mapping.get("values") or []]
		if values:
			return values
	return get_global_attribute_values(attribute_row.attribute)


@frappe.whitelist()
def get_attribute_details(item_name, dependent_attr_mapping=None):
	item = frappe.get_cached_doc("Item", item_name)
	item.check_permission("read")
	parent = frappe.get_cached_doc("Item", item.variant_of) if item.variant_of else item
	attributes = [row.attribute for row in parent.get("attributes") or []]
	primary_values = []
	if parent.primary_attribute:
		attributes = [name for name in attributes if name != parent.primary_attribute]
		for row in parent.get("attributes") or []:
			if row.attribute == parent.primary_attribute:
				primary_values = _mapping_values(row)
				break
	additional_parameters = [
		{
			"additional_parameter_key": row.additional_parameter_key,
			"additional_parameter_value": row.additional_parameter_value,
		}
		for row in parent.get("additional_parameters") or []
	]
	dependent_details = _get_dependent_details(parent, dependent_attr_mapping)
	if dependent_details and dependent_details.get("attr_list"):
		_enrich_stages_with_primary(parent, dependent_details)
	return {
		"item": parent.name,
		"primary_attribute": parent.primary_attribute,
		"dependent_attribute": parent.dependent_attribute,
		"dependent_attribute_details": dependent_details,
		"attributes": attributes,
		"primary_attribute_values": primary_values,
		"default_uom": parent.stock_uom,
		"secondary_uom": parent.secondary_unit_of_measure,
		"additional_parameters": additional_parameters,
	}


def _get_dependent_details(item, mapping_name=None):
	mapping_name = mapping_name or item.dependent_attribute_mapping
	return get_dependent_attribute_details(mapping_name) if mapping_name else {}


def _enrich_stages_with_primary(item, dependent_details):
	values_by_attribute = {row.attribute: _mapping_values(row) for row in item.get("attributes") or []}
	for stage in dependent_details["attr_list"].values():
		stage_attributes = stage.get("attributes") or []
		primary = ""
		if item.primary_attribute in stage_attributes:
			primary = item.primary_attribute
		else:
			primary = next(
				(name for name in stage_attributes if len(values_by_attribute.get(name, [])) > 1),
				"",
			)
		stage["primary_attribute"] = primary
		stage["primary_attribute_values"] = values_by_attribute.get(primary, [])


@frappe.whitelist()
def get_complete_item_details(item_name):
	item_doc = frappe.get_doc("Item", item_name)
	item_doc.check_permission("read")
	item = item_doc.as_dict()
	from frappe.model import default_fields

	for attribute in item.get("attributes") or []:
		for fieldname in default_fields:
			attribute.pop(fieldname, None)
	return item


def build_variant_attributes(my_attributes, stage, item_or_ipd):
	if hasattr(item_or_ipd, "dependent_attribute_mapping"):
		mapping_name = item_or_ipd.dependent_attribute_mapping
		source = getattr(item_or_ipd, "name", item_or_ipd)
	elif frappe.db.exists("YRP Item Production Detail", item_or_ipd):
		mapping_name = frappe.get_cached_value(
			"YRP Item Production Detail", item_or_ipd, "dependent_attribute_mapping"
		)
		source = item_or_ipd
	else:
		mapping_name = frappe.get_cached_value("Item", item_or_ipd, "dependent_attribute_mapping")
		source = item_or_ipd
	if not mapping_name:
		frappe.throw(_("Dependent Attribute Mapping is not configured for {0}").format(source))
	details = get_dependent_attribute_details(mapping_name)
	stage_details = details.get("attr_list", {}).get(stage)
	if not stage_details:
		frappe.throw(_("Stage {0} is not configured for {1}.").format(stage, source))
	args = {details["attribute"]: stage}
	for attribute in stage_details.get("attributes") or []:
		if attribute in my_attributes:
			args[attribute] = my_attributes[attribute]
	return args


def _build_dependent_variant_attrs(template, args, mapping_name=None):
	mapping = get_dependent_attribute_details(mapping_name or template.dependent_attribute_mapping)
	if template.dependent_attribute != mapping["attribute"]:
		frappe.throw(_("Dependent Attribute Mismatch Error."))
	value = args.get(template.dependent_attribute)
	if not value:
		frappe.throw(
			_("Please mention {0} attribute in {1}.").format(template.dependent_attribute, template.name)
		)
	stage = mapping.get("attr_list", {}).get(value) or {}
	applicable = stage.get("attributes") or []
	if not applicable:
		frappe.throw(_("Dependent Attribute Value {0} has no mapping.").format(value))
	display_name = stage.get("name") or ""
	return [
		{
			"attribute": template.dependent_attribute,
			"attribute_value": value,
			"display_name": display_name,
			"display_name_is_empty": not bool(display_name),
		}
	], applicable


def _variant_code(template, rows):
	code = template.name
	for row in rows:
		if not row.get("display_name_is_empty"):
			part = row.get("display_name") or row.get("attribute_value")
			if not part:
				frappe.throw(_("The Item attribute set is incomplete."))
			code += "-" + cstr(part)
	if len(code) > 140:
		frappe.throw(
			_("Generated Item Code is {0} characters; the maximum is 140. Shorten the template or attribute display names.").format(
				len(code)
			)
		)
	return code


def _copy_template_fields(template, variant):
	fieldnames = (
		"item_group", "stock_uom", "brand", "description", "is_stock_item",
		"allow_negative_stock", "is_purchase_item", "is_sales_item", "purchase_uom",
		"sales_uom", "weight_per_unit", "weight_uom", "secondary_unit_of_measure",
		"hsn_code", "gst_hsn_code", "tax_code", "is_exempt", "is_zero_rated",
		"is_ineligible_for_itc", "po_excess_allowed_percentage",
	)
	for fieldname in fieldnames:
		if variant.meta.get_field(fieldname):
			variant.set(fieldname, template.get(fieldname))
	for table_field in ("uoms", "item_defaults"):
		if variant.meta.get_field(table_field):
			variant.set(table_field, [])
			for row in template.get(table_field) or []:
				data = copy.deepcopy(row.as_dict())
				for key in ("name", "parent", "parenttype", "parentfield", "idx"):
					data.pop(key, None)
				variant.append(table_field, data)


def create_variant(template, args, dependent_attr=None):
	if isinstance(args, str):
		args = json.loads(args)
	template_doc = frappe.get_cached_doc("Item", template)
	if not args and not template_doc.has_variants:
		return template_doc
	if not template_doc.has_variants or template_doc.variant_of:
		frappe.throw(_("Item {0} is not a variant template.").format(template))

	rows = []
	applicable = None
	if template_doc.dependent_attribute:
		rows, applicable = _build_dependent_variant_attrs(template_doc, args, dependent_attr)
	for template_attribute in template_doc.get("attributes") or []:
		attribute = template_attribute.attribute
		if applicable and attribute not in applicable:
			continue
		if any(row["attribute"] == attribute for row in rows):
			continue
		value = args.get(attribute)
		if not value:
			frappe.throw(_("Please mention {0} attribute in {1}.").format(attribute, template))
		if value not in _mapping_values(template_attribute):
			frappe.throw(_("{0} is not allowed for {1} on Item {2}.").format(value, attribute, template))
		validate_attribute_value(attribute, value)
		rows.append({"attribute": attribute, "attribute_value": cstr(value), "display_name": cstr(value)})

	variant = frappe.new_doc("Item")
	_copy_template_fields(template_doc, variant)
	variant.variant_of = template_doc.name
	variant.has_variants = 0
	variant.variant_based_on = "Item Attribute"
	variant.set("attributes", rows)
	variant.item_tuple_attribute = str(
		tuple(sorted((row["attribute"], row["attribute_value"]) for row in rows))
	)
	variant.item_code = _variant_code(template_doc, rows)
	variant.item_name = variant.item_code
	return variant


def get_or_create_variant(template, args, dependent_attr=None):
	if isinstance(args, str):
		args = json.loads(args)
	if not args:
		template_doc = frappe.get_cached_doc("Item", template)
		if not template_doc.has_variants and not template_doc.variant_of:
			return template_doc.name
	existing = get_variant(template, args)
	if existing:
		return existing
	variant = create_variant(template, args, dependent_attr=dependent_attr)
	try:
		variant.insert()
	except frappe.DuplicateEntryError:
		existing = get_variant(template, args)
		if not existing:
			raise
		return existing
	return variant.name


def get_variant(template, args):
	if isinstance(args, str):
		args = json.loads(args)
	if not args:
		template_doc = frappe.get_cached_doc("Item", template)
		if not template_doc.has_variants and not template_doc.variant_of:
			return template_doc.name
		frappe.throw(_("Please specify at least one attribute."))
	tuple_value = str(tuple(sorted((key, cstr(value)) for key, value in args.items())))
	name = frappe.db.get_value("Item", {"variant_of": template, "item_tuple_attribute": tuple_value}, "name")
	return name or _find_variant_by_attributes(template, args)


def _find_variant_by_attributes(template, args):
	for variant_name in _get_variants_by_attributes(args, template):
		variant = frappe.get_cached_doc("Item", variant_name)
		actual = {row.attribute: cstr(row.attribute_value) for row in variant.attributes}
		if actual == {key: cstr(value) for key, value in args.items()}:
			return variant.name
	return None


def _get_variants_by_attributes(args, template=None):
	if not args:
		return frappe.get_all("Item", {"variant_of": template}, pluck="name") if template else []
	matching_sets = []
	for attribute, values in args.items():
		values = values if isinstance(values, list) else [values]
		parents = frappe.get_all(
			"Item Variant Attribute",
			filters={"attribute": attribute, "attribute_value": ["in", values]},
			pluck="parent",
		)
		if template and parents:
			parents = frappe.get_all("Item", {"name": ["in", parents], "variant_of": template}, pluck="name")
		matching_sets.append(set(parents))
	return list(set.intersection(*matching_sets)) if matching_sets else []


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_item_attribute_values(
	doctype=None, txt="", searchfield=None, start=0, page_len=20, filters=None
):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("attribute"):
		return []
	attribute = filters["attribute"]
	values = []
	if filters.get("item"):
		item = frappe.get_cached_doc("Item", get_parent_item(filters["item"]))
		for row in item.get("attributes") or []:
			if row.attribute == attribute:
				values = _mapping_values(row)
				break
	if not values:
		values = get_global_attribute_values(attribute)
	needle = (txt or "").lower()
	return [[value] for value in values if needle in cstr(value).lower()][start : start + page_len]


@frappe.whitelist()
def search_item_attribute_values(txt="", attribute=None, item=None, page_len=99):
	"""Autocomplete endpoint for Data fields that hold an attribute value.

	ERPNext's Item Attribute Value is a child table and therefore cannot be the
	target of a Link control. This endpoint preserves the old searchable UX while
	returning plain strings suitable for an Autocomplete control.
	"""
	if not frappe.has_permission("Item Attribute", ptype="read"):
		frappe.throw(_("Not permitted to read Item Attribute values"), frappe.PermissionError)
	if not attribute:
		return []
	values = []
	if item:
		parent = get_parent_item(item)
		if parent:
			item_doc = frappe.get_cached_doc("Item", parent)
			for row in item_doc.get("attributes") or []:
				if row.attribute == attribute:
					values = _mapping_values(row)
					break
	if not values:
		values = get_global_attribute_values(attribute)
	needle = cstr(txt).lower()
	return [value for value in values if needle in cstr(value).lower()][: int(page_len or 99)]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_item_attributes(doctype, txt, searchfield, start, page_len, filters):
	if not filters.get("item"):
		return []
	item = frappe.get_cached_doc("Item", get_parent_item(filters["item"]))
	values = [row.attribute for row in item.get("attributes") or []]
	needle = (txt or "").lower()
	return [[value] for value in values if needle in value.lower()][start : start + page_len]


@frappe.whitelist()
def get_attribute_values(item, attributes=None):
	item_doc = frappe.get_cached_doc("Item", get_parent_item(item))
	item_doc.check_permission("read")
	requested = set(attributes or [row.attribute for row in item_doc.get("attributes") or []])
	return {
		row.attribute: _mapping_values(row)
		for row in item_doc.get("attributes") or []
		if row.attribute in requested
	}


@frappe.whitelist()
def get_attributes(item):
	item_doc = frappe.get_cached_doc("Item", get_parent_item(item))
	item_doc.check_permission("read")
	return [row.attribute for row in item_doc.get("attributes") or []]


def validate_is_stock_item(item, is_stock_item=None):
	if is_stock_item is None:
		is_stock_item = frappe.get_cached_value("Item", item, "is_stock_item")
	if is_stock_item != 1:
		frappe.throw(_("Item {0} is not a stock Item").format(item))


def validate_cancelled_item(item, docstatus=None):
	if docstatus is None:
		docstatus = frappe.get_cached_value("Item", item, "docstatus")
	if docstatus == 2:
		frappe.throw(_("Item {0} is cancelled").format(item))


def validate_disabled(item, disabled=None):
	if disabled is None:
		disabled = frappe.get_cached_value("Item", item, "disabled")
	if disabled:
		frappe.throw(_("Item {0} is disabled").format(item))


@frappe.whitelist()
def rename_item(docname, name, brand=None):
	doc = frappe.get_doc("Item", docname)
	doc.check_permission("write")
	new_name = cstr(name).strip()
	if brand and not new_name.lower().startswith(cstr(brand).lower() + " "):
		new_name = f"{brand} {new_name}"
	doc.item_name = cstr(name).strip()
	doc.brand = brand
	doc.save()
	if new_name and new_name != doc.name:
		from frappe.model.rename_doc import rename_doc

		return rename_doc("Item", doc.name, new_name, force=True)
	return doc.name


def update_variants(variants):
	"""Physical Item codes are stable and never regenerated from abbreviations."""
	return [row.get("name") if isinstance(row, dict) else row for row in variants or []]


def ensure_variant_tuple_unique_index():
	"""Enforce one non-empty YRP tuple per standard Item template."""
	if not frappe.db.table_exists("Item") or not frappe.get_meta("Item").has_field(
		"item_tuple_attribute"
	):
		return
	frappe.db.sql(
		"update `tabItem` set item_tuple_attribute = null where item_tuple_attribute = ''"
	)
	duplicate = frappe.db.sql(
		"""
		select variant_of, item_tuple_attribute, count(*)
		from `tabItem`
		where coalesce(variant_of, '') != '' and item_tuple_attribute is not null
		group by variant_of, item_tuple_attribute
		having count(*) > 1
		limit 1
		"""
	)
	if duplicate:
		frappe.throw(
			_("Cannot create the YRP Item tuple index: duplicate tuple exists for template {0}.").format(
				duplicate[0][0]
			)
		)
	frappe.db.add_unique(
		"Item",
		["variant_of", "item_tuple_attribute"],
		constraint_name="unique_yrp_item_variant_tuple",
	)


def prevent_yrp_variant_code_change(doc, method=None):
	"""Block abbreviation edits that would rename an established YRP variant."""
	before = doc.get_doc_before_save()
	if not before or doc.numeric_values:
		return
	old_abbr = {row.name: row.abbr for row in before.get("item_attribute_values") or []}
	changed_values = [
		row.attribute_value
		for row in doc.get("item_attribute_values") or []
		if row.name in old_abbr and old_abbr[row.name] != row.abbr
	]
	if not changed_values:
		return
	variant = frappe.db.sql(
		"""
		select iva.parent
		from `tabItem Variant Attribute` iva
		inner join `tabItem` item on item.name = iva.parent
		where iva.attribute = %s
		  and iva.attribute_value in %(values)s
		  and (
			coalesce(item.item_tuple_attribute, '') != ''
			or exists (select 1 from `tabYRP Bin` bin where bin.item_code = item.name)
			or exists (select 1 from `tabYRP Stock Ledger Entry` sle where sle.item = item.name)
		  )
		limit 1
		""",
		{"values": changed_values, "attribute": doc.name},
	)
	if variant:
		frappe.throw(
			_("Cannot change the abbreviation because it would rename established YRP Item {0}.").format(
				variant[0][0]
			)
		)


YRPItem = YRPItemMixin
