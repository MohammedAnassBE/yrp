import frappe
from frappe.model.document import Document


class IPDProcessMatrix(Document):
	def validate(self):
		self.validate_reference_item_variant()
		self.validate_attributes_belong_to_ipd()
		self.validate_combination_consistency()

	def validate_reference_item_variant(self):
		if not (self.ipd and self.reference_item_variant):
			return
		ipd_item = frappe.db.get_value("Item Production Detail", self.ipd, "item")
		variant_item = frappe.db.get_value("Item Variant", self.reference_item_variant, "item")
		if variant_item != ipd_item:
			frappe.throw(
				f"Reference Item Variant {self.reference_item_variant} must belong to IPD item {ipd_item}."
			)

	def validate_attributes_belong_to_ipd(self):
		if not self.ipd:
			return
		ipd_item, dep_attr = frappe.db.get_value("Item Production Detail", self.ipd, ["item", "dependent_attribute"])
		# Dependent attribute (e.g. Stage) is not enumerated in matrix combos — it comes from IPD Process in/out_stage.
		for row in list(self.input_attributes) + list(self.output_attributes):
			if dep_attr and row.attribute == dep_attr:
				frappe.throw(
					f"Attribute {row.attribute} is the IPD's dependent attribute and cannot be used in the matrix; "
					f"the engine assigns it from the IPD Process in/out stage."
				)
		ipd_attrs = {
			row.attribute
			for row in frappe.get_all(
				"IPD Item Attribute",
				filters={"parent": self.ipd, "parenttype": "Item Production Detail"},
				fields=["attribute"],
			)
		}

		input_item = self.input_item or ipd_item
		if input_item == ipd_item:
			input_attrs = ipd_attrs
		else:
			input_attrs = {
				r.attribute
				for r in frappe.get_all(
					"Item Item Attribute",
					filters={"parent": input_item, "parenttype": "Item"},
					fields=["attribute"],
				)
			}

		for row in self.input_attributes:
			if row.attribute not in input_attrs:
				frappe.throw(
					f"Input attribute {row.attribute} is not part of input item {input_item}'s attributes."
				)
		for row in self.output_attributes:
			if row.attribute not in ipd_attrs:
				frappe.throw(
					f"Output attribute {row.attribute} is not part of IPD {self.ipd}'s Item Attributes."
				)

	def validate_combination_consistency(self):
		combo_keys = {(c.group_index, c.side, c.combo_index) for c in self.combinations}
		attr_keys = {(a.group_index, a.side, a.combo_index) for a in self.combination_attributes}
		orphan_attrs = attr_keys - combo_keys
		if orphan_attrs:
			frappe.throw(
				f"Combination attributes reference unknown combinations: {sorted(orphan_attrs)}"
			)

	def get_combinations_grouped(self):
		"""Return groups dict: {group_index: {"input": [combo_dict, ...], "output": [...]}}.
		Each combo_dict = {"combo_index": int, "qty": float, "uom": str,
		"wastage_pct": float, "attrs": {attr_name: attr_value, ...}}."""
		groups = {}
		attrs_by_key = {}
		for a in self.combination_attributes:
			attrs_by_key.setdefault((a.group_index, a.side, a.combo_index), {})[a.attribute] = a.attribute_value
		for c in self.combinations:
			g = groups.setdefault(c.group_index, {"input": [], "output": []})
			side_key = c.side.lower()
			g[side_key].append({
				"combo_index": c.combo_index,
				"qty": c.quantity,
				"uom": c.uom,
				"wastage_pct": c.wastage_pct or 0,
				"attrs": attrs_by_key.get((c.group_index, c.side, c.combo_index), {}),
			})
		return groups


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_reference_variant_query(doctype, txt, searchfield, start, page_len, filters):
	ipd = (filters or {}).get("ipd")
	if not ipd:
		return []
	item = frappe.db.get_value("Item Production Detail", ipd, "item")
	if not item:
		return []
	return frappe.db.sql(
		"""
		SELECT name
		FROM `tabItem Variant`
		WHERE item = %(item)s
		  AND name LIKE %(txt)s
		ORDER BY name
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"item": item,
			"txt": f"%{txt}%",
			"start": start,
			"page_len": page_len,
		},
	)
