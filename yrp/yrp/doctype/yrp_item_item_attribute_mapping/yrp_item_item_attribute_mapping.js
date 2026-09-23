// Copyright (c) 2021, Essdee and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Item Item Attribute Mapping", {
	setup(frm) {
		// Values are child-row text, not Link names. Native Autocomplete provides
		// the filtered picker; the controller rejects values outside this source.
		frm.set_query("attribute_value", "values", () => ({
			query: "yrp.yrp.doctype.yrp_item_item_attribute_mapping.yrp_item_item_attribute_mapping.search_attribute_values",
			params: { attribute: frm.doc.attribute_name },
		}));
	},
	attribute_name(frm) {
		// Values selected for the previous attribute must not cross into another.
		frm.clear_table("values");
		frm.refresh_field("values");
	},
});
