"""Item — the base product template.

Items have attributes (Size, Colour), a primary attribute (the one used for
qty grids), and an optional dependent attribute (e.g., Stage) that changes
which attributes apply per stage.

Item Variants are specific combinations (e.g., T-Shirt Red S) created from
the Item template + attribute values.
"""

import json

import frappe
from frappe import _
from frappe.desk.search import search_widget
from frappe.model.document import Document
from frappe.model.naming import append_number_if_name_exists, make_autoname
from frappe.utils import cstr, flt

from yrp.yrp.doctype.item_dependent_attribute_mapping.item_dependent_attribute_mapping import (
	get_dependent_attribute_details,
)


class Item(Document):

	def before_validate(self):
		if self.is_new():
			self.item_hash_value = make_autoname(key="hash")

	def autoname(self):
		"""Name the Item from its descriptive `name1` (no `Item-.####` series).

		Uses the SAME `get_name(brand, name1)` the `rename_item` flow uses, so the
		create-path and rename-path agree: with no brand the name is just `name1`
		(matches the existing data, where name == name1); with a brand set it is
		`<brand> <name1>`. `name1` is not unique, so we append `-1`/`-2` on a
		collision (Frappe's standard pattern) rather than hard-failing creation.
		"""
		name = self.get_name(self.brand, cstr(self.name1).strip())
		if not name:
			frappe.throw(_("Name is required"))
		self.name = append_number_if_name_exists("Item", name)

	def get_name(self, brand, name):
		"""Build the Item name: prepend brand if not already present."""
		name = name.strip()
		if brand:
			first_word = name.split(" ")[0]
			if first_word.lower() == brand.lower():
				return name  # Brand already in name
			return brand + " " + name
		return name

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

			# Get the attribute's mapped values (or empty list if no mapping)
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
		if not self.flags.get("last_updated_by_sd_yrp_sync"):
			# Skipped during SD-YRP sync: the attribute / dependent-attribute
			# mappings (IIAM / IDAM) are replicated as their own doctypes and the
			# Item's links are managed by the consumer. Running these here would
			# duplicate/regenerate those mappings and fail on links not yet applied
			# during the Item's own insert (e.g. an IDAM that points back at this
			# not-yet-committed Item).
			self._duplicate_mappings_on_create()
			self._ensure_attribute_mappings_exist()
			self._validate_dependent_attribute()
		self._validate_allow_negative_stock_toggle()

	def _validate_allow_negative_stock_toggle(self):
		"""Block unchecking allow_negative_stock while any variant has negative actual qty.

		If a user disables negative stock without first replenishing, future
		issues would unexpectedly throw — and the existing negative balance
		would never settle through the two-phase logic. Force them to settle
		first.
		"""
		if self.is_new():
			return
		if self.allow_negative_stock:
			return
		previous = frappe.db.get_value(
			"Item", self.name, "allow_negative_stock"
		)
		if not previous:
			# Was already off; nothing to do.
			return

		negative_exists = frappe.db.sql(
			"""
			SELECT 1 FROM `tabBin` b
			INNER JOIN `tabItem Variant` iv ON iv.name = b.item_code
			WHERE iv.item = %s AND b.actual_qty < 0
			LIMIT 1
			""",
			self.name,
		)
		if negative_exists:
			frappe.throw(
				_(
					"Cannot disable Allow Negative Stock — one or more variants "
					"of {0} currently have negative balance. Replenish first."
				).format(self.name)
			)

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
		"""On new Item creation, duplicate shared mappings so each Item has its own copy."""
		if not self.is_new():
			return

		# Duplicate attribute mappings
		for attribute in self.get("attributes"):
			if attribute.mapping:
				original = frappe.get_doc("Item Item Attribute Mapping", attribute.mapping)
				copy = frappe.copy_doc(original)
				copy.save()
				attribute.mapping = copy.name

		# Duplicate dependent attribute mapping
		if self.dependent_attribute and self.dependent_attribute_mapping:
			original = frappe.get_doc("Item Dependent Attribute Mapping", self.dependent_attribute_mapping)
			copy = frappe.copy_doc(original)
			copy.save()
			self.dependent_attribute_mapping = copy.name
		elif not self.dependent_attribute and self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = None

	def _ensure_attribute_mappings_exist(self):
		"""Create empty mapping docs for attributes that don't have one yet.

		Treat empty-string (sent by clients that round-trip the form via
		JSON, e.g. /web's REST PUT) the same as None — otherwise downstream
		validators throw an opaque "Item Item Attribute Mapping not found"
		from a get_doc on the empty name.
		"""
		for attribute in self.get("attributes"):
			if not attribute.mapping:
				mapping = frappe.new_doc("Item Item Attribute Mapping")
				mapping.attribute_name = attribute.attribute
				mapping.save()
				attribute.mapping = mapping.name

	def _validate_dependent_attribute(self):
		"""Validate dependent attribute setup: must be in attribute list,
		must have values, must have primary attribute set."""
		if not self.dependent_attribute:
			# No dependent attribute — clean up mapping if it exists
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

		# Create dependent attribute mapping if it doesn't exist
		if not self.dependent_attribute_mapping:
			self.dependent_attribute_mapping = _create_dependent_attribute_mapping(
				self, dependent_attr_values
			)


# ======================================================================
# Module-level functions
# ======================================================================

def _create_dependent_attribute_mapping(doc, attr_list):
	"""Create a new Item Dependent Attribute Mapping document."""
	mapping = frappe.new_doc("Item Dependent Attribute Mapping")

	details = [{"attribute_value": attr, "uom": doc.default_unit_of_measure} for attr in attr_list]
	mapping_rows = [
		{"dependent_attribute_value": attr, "depending_attribute": doc.primary_attribute}
		for attr in attr_list
	]

	if doc.doctype == "Item":
		mapping.item = doc.name

	mapping.dependent_attribute = doc.dependent_attribute
	mapping.set("mapping", mapping_rows)
	mapping.set("details", details)
	mapping.save()
	return mapping.name


@frappe.whitelist()
def update_dependent_attribute_details(dependent_attribute_mapping, detail):
	"""Update the dependent attribute mapping with new attribute/stage details.

	Called from the frontend when user edits the dependent attribute configuration.
	"""
	if isinstance(detail, str):
		detail = json.loads(detail)

	mapping = frappe.get_doc("Item Dependent Attribute Mapping", dependent_attribute_mapping)

	details = []
	mapping_rows = []
	for attr, value in detail["attr_list"].items():
		details.append({
			"attribute_value": attr,
			"uom": value["uom"],
			"display_name": value["name"],
		})
		for attr_value in value["attributes"]:
			mapping_rows.append({
				"dependent_attribute_value": attr,
				"depending_attribute": attr_value,
			})

	mapping.set("mapping", mapping_rows)
	mapping.set("details", details)
	mapping.save()
	return mapping


@frappe.whitelist()
def get_attribute_details(item_name, dependent_attr_mapping=None):
	"""Return the full attribute configuration for an Item.

	Used by the Vue stock editor to render the attribute grid.
	Returns: item name, primary/dependent attributes, attribute values,
	dependent attribute details with per-stage primary attribute info.
	"""
	item = frappe.get_cached_doc("Item", item_name)

	# Collect non-primary attributes
	attributes = [attr.attribute for attr in item.attributes]
	primary_attribute_values = []

	if item.primary_attribute:
		attributes.remove(item.primary_attribute)
		primary_attribute_values = _get_primary_attribute_values(item)

	# Additional parameters
	additional_parameters = [
		{
			"additional_parameter_key": a.additional_parameter_key,
			"additional_parameter_value": a.additional_parameter_value,
		}
		for a in item.additional_parameters
	]

	# Dependent attribute details
	dependent_attribute_details = _get_dependent_details(item, dependent_attr_mapping)

	# Enrich each dependent stage with per-stage primary attribute info
	if dependent_attribute_details and dependent_attribute_details.get("attr_list"):
		_enrich_stages_with_primary(item, dependent_attribute_details)

	return {
		"item": item_name,
		"primary_attribute": item.primary_attribute,
		"dependent_attribute": item.dependent_attribute,
		"dependent_attribute_details": dependent_attribute_details,
		"attributes": attributes,
		"primary_attribute_values": primary_attribute_values,
		"default_uom": item.default_unit_of_measure,
		"secondary_uom": item.secondary_unit_of_measure,
		"additional_parameters": additional_parameters,
	}


def _get_primary_attribute_values(item):
	"""Get the list of values for the primary attribute.

	First tries the Item's attribute mapping, falls back to all values
	defined on the Item Attribute master.
	"""
	for attribute in item.attributes:
		if attribute.attribute == item.primary_attribute:
			if attribute.mapping:
				mapping_doc = frappe.get_cached_doc("Item Item Attribute Mapping", attribute.mapping)
				values = [v.attribute_value for v in mapping_doc.values]
				if values:
					return values

			# Mapping empty — fall back to all values for this attribute
			return frappe.get_all(
				"Item Attribute Value",
				filters={"attribute_name": item.primary_attribute},
				pluck="attribute_value",
				order_by="idx asc",
			)
	return []


def _get_dependent_details(item, dependent_attr_mapping=None):
	"""Get dependent attribute details from the mapping document."""
	if dependent_attr_mapping:
		return get_dependent_attribute_details(dependent_attr_mapping)
	elif item.dependent_attribute and item.dependent_attribute_mapping:
		return get_dependent_attribute_details(item.dependent_attribute_mapping)
	return {}


def _enrich_stages_with_primary(item, dependent_attribute_details):
	"""For each dependent attribute stage, determine which attribute is the
	primary (qty matrix driver) for that stage.

	Example: Item has Size and Colour attributes.
	Stage "Cut" uses Size as its primary (qty grid is per size).
	Stage "Pack" uses Colour as its primary (qty grid is per colour).

	Logic: for each stage, find the first attribute with multiple mapped values.
	Falls back to the item-level primary_attribute.
	"""
	# Build a map of attribute → list of values for this item
	attr_value_map = {}
	for attr_row in item.attributes:
		values = []
		if attr_row.mapping:
			mapping_doc = frappe.get_cached_doc("Item Item Attribute Mapping", attr_row.mapping)
			values = [v.attribute_value for v in mapping_doc.values]
		if not values:
			values = frappe.get_all(
				"Item Attribute Value",
				filters={"attribute_name": attr_row.attribute},
				pluck="attribute_value",
				order_by="idx asc",
			)
		if values:
			attr_value_map[attr_row.attribute] = values

	# Assign per-stage primary attribute
	for stage_key, stage_info in dependent_attribute_details["attr_list"].items():
		stage_attrs = stage_info.get("attributes") or []
		stage_primary = ""
		stage_primary_values = []

		# Check item-level primary first
		if (
			item.primary_attribute
			and item.primary_attribute in stage_attrs
			and item.primary_attribute in attr_value_map
		):
			stage_primary = item.primary_attribute
			stage_primary_values = attr_value_map[item.primary_attribute]
		else:
			# Pick the first stage attribute that has multiple mapped values
			for attr_name in stage_attrs:
				if attr_name in attr_value_map and len(attr_value_map[attr_name]) > 1:
					stage_primary = attr_name
					stage_primary_values = attr_value_map[attr_name]
					break

		stage_info["primary_attribute"] = stage_primary
		stage_info["primary_attribute_values"] = stage_primary_values


@frappe.whitelist()
def get_complete_item_details(item_name):
	"""Return the full Item document as a dict, with attribute default fields stripped."""
	item = frappe.get_doc("Item", item_name).as_dict()

	from frappe.model import default_fields
	for attribute in item["attributes"]:
		for fieldname in default_fields:
			if fieldname in attribute:
				del attribute[fieldname]

	return item


# ======================================================================
# Variant creation and lookup
# ======================================================================

def get_or_create_variant(template, args, dependent_attr=None):
	"""Find an existing variant or create a new one."""
	variant_name = get_variant(template, args)
	if not variant_name:
		variant = create_variant(template, args, dependent_attr=dependent_attr)
		variant.insert()
		variant_name = variant.name
	return variant_name


def create_variant(template, args, dependent_attr=None):
	"""Create a new Item Variant document from template + attribute values.

	Args:
		template: parent Item name
		args: dict of {attribute_name: attribute_value}
		dependent_attr: optional dependent attribute mapping name
	"""
	if isinstance(args, str):
		args = json.loads(args)

	template_doc = frappe.get_cached_doc("Item", template)
	variant = frappe.new_doc("Item Variant")
	variant.item = template_doc.name

	# Build the variant's attribute list
	variant_attributes = []
	all_attributes = [d.attribute for d in template_doc.attributes]
	dependent_attributes = None

	# Handle dependent attribute (e.g., Stage)
	if template_doc.dependent_attribute:
		variant_attributes, dependent_attributes = _build_dependent_variant_attrs(
			template_doc, args, dependent_attr
		)

	# Add remaining attributes
	for attr_name in all_attributes:
		# Skip attributes not in this dependent stage
		if dependent_attributes and attr_name not in dependent_attributes:
			continue
		if not args.get(attr_name):
			frappe.throw(f"Please mention {attr_name} attribute in {template_doc.name}")
		variant_attributes.append({
			"attribute": attr_name,
			"attribute_value": args.get(attr_name),
			"display_name": args.get(attr_name),
		})

	# Build tuple hash for fast variant lookup
	attr_dict = {a["attribute"]: a["attribute_value"] for a in variant_attributes}
	sorted_tuple = tuple(sorted(attr_dict.items()))
	if sorted_tuple:
		variant.item_tuple_attribute = str(sorted_tuple)

	variant.set("attributes", variant_attributes)
	return variant


def _build_dependent_variant_attrs(template_doc, args, dependent_attr=None):
	"""Build variant attributes for the dependent attribute (e.g., Stage).

	Returns:
		(variant_attributes_list, dependent_attributes_list)
	"""
	if dependent_attr:
		dep_mapping = get_dependent_attribute_details(dependent_attr)
	else:
		dep_mapping = get_dependent_attribute_details(template_doc.dependent_attribute_mapping)

	if template_doc.dependent_attribute != dep_mapping["attribute"]:
		frappe.throw("Dependent Attribute Mismatch Error.")

	dep_attr_value = args.get(template_doc.dependent_attribute)
	if not dep_attr_value:
		frappe.throw(f"Please mention {template_doc.dependent_attribute} attribute in {template_doc.name}")

	# Get which attributes apply for this dependent value (stage)
	dep_mapping["attr_list"].setdefault(dep_attr_value, {}).setdefault("attributes", [])
	dependent_attributes = dep_mapping["attr_list"][dep_attr_value]["attributes"]

	if not dependent_attributes:
		frappe.throw(f"Dependent Attribute Value {dep_attr_value} does not have proper mapping")

	display_name = dep_mapping["attr_list"][dep_attr_value].get("name") or ""
	variant_attributes = [{
		"attribute": template_doc.dependent_attribute,
		"attribute_value": dep_attr_value,
		"display_name": display_name,
		"display_name_is_empty": not bool(display_name),
	}]

	return variant_attributes, dependent_attributes


def get_variant(template, args):
	"""Find an existing Item Variant matching the given template + attributes.

	Uses tuple-based fast lookup if enabled in YRP Settings, otherwise
	falls back to attribute-by-attribute matching.
	"""
	try:
		enable_tuple = frappe.db.get_single_value("YRP Settings", "enable_tuple_attribute")
	except Exception:
		enable_tuple = False

	if isinstance(args, str):
		args = json.loads(args)

	if enable_tuple:
		return _get_variant_by_tuple(template, args)
	else:
		if not args:
			template_doc = frappe.get_cached_doc("Item", template)
			if template_doc.attributes:
				frappe.throw("Please specify at least one attribute in the Attributes table")
		return _find_variant_by_attributes(template, args)


def _get_variant_by_tuple(template, args):
	"""Fast variant lookup using the pre-computed tuple hash."""
	if not args:
		# No attributes — check if template itself is a variant
		variants = frappe.get_all("Item Variant", filters={"name": template}, pluck="name")
		return variants[0] if variants else None

	sorted_tuple = str(tuple(sorted(args.items())))
	variants = frappe.db.sql(
		"SELECT name FROM `tabItem Variant` WHERE item = %s AND item_tuple_attribute = %s",
		(template, sorted_tuple),
		as_dict=True,
	)
	return variants[0]["name"] if variants else None


def _find_variant_by_attributes(template, args):
	"""Find a variant by matching each attribute value individually.

	Slower than tuple lookup but works without the tuple hash field.
	"""
	possible_variants = _get_variants_by_attributes(args, template)

	for variant_name in possible_variants:
		variant = frappe.get_cached_doc("Item Variant", variant_name)

		# Must have exact same number of attributes
		if len(args) != len(variant.get("attributes")):
			continue

		# Check every attribute matches
		match_count = 0
		for attribute, value in args.items():
			for row in variant.attributes:
				if row.attribute == attribute and row.attribute_value == cstr(value):
					match_count += 1
					break

		if match_count == len(args):
			return variant.name

	return None


def _get_variants_by_attributes(args, template=None):
	"""Find Item Variants that have the given attribute values.

	Returns a list of variant names that match ALL specified attributes.
	Uses SQL for performance on large variant sets.
	"""
	if not args:
		# No attributes specified — return all variants of the template
		if not template:
			return []
		return frappe.db.sql(
			"SELECT name FROM `tabItem Variant` WHERE item = %s",
			(template,),
			pluck="name",
		)

	# For each attribute, find variants that have that attribute value
	matching_sets = []
	for attribute, values in args.items():
		if not isinstance(values, list):
			values = [values]
		if not values:
			continue

		# Build WHERE clause for this attribute's values
		conditions = []
		query_values = []
		for value in values:
			conditions.append("(attribute = %s AND attribute_value = %s)")
			query_values.extend([attribute, value])

		attribute_filter = " OR ".join(conditions)

		# Add template filter if specified
		variant_filter = ""
		if template:
			variant_filter = "AND t2.item = %s"
			query_values.append(template)

		query = f"""
			SELECT t1.parent
			FROM `tabItem Variant Attribute` t1
			WHERE ({attribute_filter})
			AND EXISTS (
				SELECT 1 FROM `tabItem Variant` t2
				WHERE t2.name = t1.parent {variant_filter}
			)
			GROUP BY t1.parent
			ORDER BY NULL
		"""
		variant_names = {r[0] for r in frappe.db.sql(query, query_values)}
		matching_sets.append(variant_names)

	if not matching_sets:
		return []

	# Return variants that appear in ALL attribute sets (intersection)
	return list(set.intersection(*matching_sets))


# ======================================================================
# Search queries — used by Link field typeaheads
# ======================================================================

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_item_attribute_values(doctype, txt, searchfield, start, page_len, filters):
	"""Typeahead for Item Attribute Value — filtered by item's attribute mapping."""
	if doctype != "Item Attribute Value" or not filters.get("item") or not filters.get("attribute"):
		return []

	item_name = filters["item"]
	attribute = filters["attribute"]

	# Numeric attributes — return all values
	attr_doc = frappe.get_doc("Item Attribute", attribute)
	if attr_doc.numeric_values:
		return search_widget(
			doctype=doctype, txt=txt, page_length=page_len,
			searchfield=searchfield,
			filters=[["Item Attribute Value", "attribute_name", "=", attribute]],
		)

	# Check attribute belongs to this item
	item = frappe.get_doc("Item", item_name)
	for attr_obj in item.attributes:
		if attr_obj.attribute != attribute:
			continue

		# If mapping has values, filter by them; otherwise return all
		mapping_doc = frappe.get_doc("Item Item Attribute Mapping", attr_obj.mapping)
		if not mapping_doc.values:
			return search_widget(
				doctype=doctype, txt=txt, page_length=page_len,
				searchfield=searchfield,
				filters=[["Item Attribute Value", "attribute_name", "=", attribute]],
			)
		else:
			attribute_values = [v.attribute_value for v in mapping_doc.values]
			return [[v] for v in attribute_values if txt.lower() in v.lower()]

	return []


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_item_attributes(doctype, txt, searchfield, start, page_len, filters):
	"""Typeahead for Item Attribute — filtered to this item's attributes only."""
	if doctype != "Item Attribute" or not filters.get("item"):
		return []

	item = frappe.get_doc("Item", filters["item"])
	attributes = [attr.attribute for attr in item.attributes]
	return [[v] for v in attributes if txt.lower() in v.lower()]


@frappe.whitelist()
def get_attribute_values(item, attributes=None):
	"""Return {attribute_name: [values]} for the given item's mapped attributes."""
	item_doc = frappe.get_doc("Item", item)
	result = {}

	if not attributes:
		attributes = [attr.attribute for attr in item_doc.attributes]

	for attribute in item_doc.attributes:
		if attribute.attribute in attributes and attribute.mapping is not None:
			mapping_doc = frappe.get_doc("Item Item Attribute Mapping", attribute.mapping)
			result[attribute.attribute] = [d.attribute_value for d in mapping_doc.values]

	return result


@frappe.whitelist()
def get_attributes(item):
	"""Return list of attribute names for an item."""
	item = frappe.get_doc("Item", item)
	return [attr.attribute for attr in item.attributes]


# ======================================================================
# Validation helpers — used by stock controllers
# ======================================================================

def validate_is_stock_item(item, is_stock_item=None):
	if not is_stock_item:
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


# ======================================================================
# Rename / Update utilities
# ======================================================================

@frappe.whitelist()
def rename_item(docname, name, brand=None):
	"""Rename an Item — updates name1, brand, and the document name if needed."""
	doc = frappe.get_doc("Item", docname)
	doc.check_permission(permtype="write")

	transformed_name = doc.get_name(brand, name)
	name_updated = transformed_name and (transformed_name != doc.name)

	doc.name1 = name
	doc.brand = brand
	doc.save()

	if name_updated:
		doc.rename(transformed_name, force=True)

	return doc.name


def update_variants(variants):
	"""Rename variants if their computed name has changed."""
	from frappe.model.rename_doc import rename_doc
	for variant_name in variants:
		doc = frappe.get_doc("Item Variant", variant_name)
		new_name = doc.get_name()
		if variant_name != new_name:
			rename_doc(doc=doc, new=new_name, force=True, rebuild_search=False)
