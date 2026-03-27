// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item Price", {
	setup(frm) {
		frm.set_query("attribute", function () {
			return {
				query: "yrp.yrp.doctype.item.item.get_item_attributes",
				filters: {
					item: frm.doc.item_name,
				},
			};
		});

		frm.set_query("attribute_value", "item_price_values", function (doc, cdt, cdn) {
			return {
				query: "yrp.yrp.doctype.item.item.get_item_attribute_values",
				filters: {
					item: doc.item_name,
					attribute: doc.attribute,
				},
			};
		});

		frm.set_query("tax", function () {
			return {
				filters: {
					enabled: 1,
				},
			};
		});
	},

	refresh(frm) {
		if (frm.doc.from_date) {
			frm.fields_dict.to_date.datepicker.update({
				minDate: new Date(frm.doc.from_date),
			});
		}
		showOrHideColumns(frm);
	},

	from_date(frm) {
		if (frm.doc.from_date) {
			frm.fields_dict.to_date.datepicker.update({
				minDate: new Date(frm.doc.from_date),
			});
		}
	},

	to_date(frm) {
		if (frm.doc.to_date) {
			frm.fields_dict.from_date.datepicker.update({
				maxDate: new Date(frm.doc.to_date),
			});
		}
	},

	depends_on_attribute(frm) {
		if (!frm.doc.depends_on_attribute) {
			removeAttributes(frm);
		}
		showOrHideColumns(frm);
	},

	validate(frm) {
		if (!frm.doc.depends_on_attribute) {
			frm.set_value("attribute", null);
		}
	},
});

function removeAttributes(frm) {
	frm.set_value("attribute", null);
	for (let row of frm.doc.item_price_values || []) {
		frappe.model.set_value(row.doctype, row.name, "attribute_value", null);
	}
}

function showOrHideColumns(frm) {
	let hidden = !frm.doc.depends_on_attribute;
	let grid = frm.fields_dict.item_price_values.grid;

	if (grid.grid_rows && grid.grid_rows.length > 0) {
		grid.update_docfield_property("attribute_value", "hidden", hidden ? 1 : 0);
		grid.update_docfield_property("attribute_value", "reqd", hidden ? 0 : 1);
		grid.refresh();
	}
}
