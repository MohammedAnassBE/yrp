// Copyright (c) 2021, Essdee and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Item Item Attribute Mapping", {
	setup(frm) {
		// The Link points to the YRP projection of an ERPNext attribute value.
		// Scope the picker to this mapping's Item Attribute.
		frm.set_query("attribute_value", "values", () => ({
			filters: { attribute_name: frm.doc.attribute_name || "__unselected__" },
		}));
	},
	attribute_name(frm) {
		// Values selected for the previous attribute must not cross into another.
		frm.clear_table("values");
		frm.refresh_field("values");
	},
});
