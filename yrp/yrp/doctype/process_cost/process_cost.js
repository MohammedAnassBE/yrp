// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("Process Cost", {
	setup(frm) {
		frm.set_query("attribute", function () {
			return {
				query: "yrp.yrp.doctype.process_cost.process_cost.get_item_attributes",
				filters: {
					item: frm.doc.item,
				},
			};
		});

		frm.set_query("attribute_value", "process_cost_values", function (doc) {
			return {
				query: "yrp.yrp.doctype.item.item.get_item_attribute_values",
				filters: {
					item: doc.item,
					attribute: doc.attribute,
				},
			};
		});

		frm.set_query("tax_slab", function () {
			return { filters: { enabled: 1 } };
		});
	},

	refresh(frm) {
		showOrHideColumns(frm);
	},

	depends_on_attribute(frm) {
		if (!frm.doc.depends_on_attribute) {
			frm.set_value("attribute", null);
			for (let row of frm.doc.process_cost_values || []) {
				frappe.model.set_value(row.doctype, row.name, "attribute_value", null);
			}
		}
		showOrHideColumns(frm);
	},

	attribute(frm) {
		if (frm.doc.attribute && frm.doc.item) {
			frappe.call({
				method: "yrp.yrp.doctype.process_cost.process_cost.get_pc_attribute_values",
				args: {
					item: frm.doc.item,
					attribute: frm.doc.attribute,
				},
				callback: function (r) {
					if (r.message) {
						frm.clear_table("process_cost_values");
						for (let val of r.message) {
							let row = frm.add_child("process_cost_values");
							row.attribute_value = val.attribute_value;
							row.price = val.price;
							row.min_order_qty = val.min_order_qty;
						}
						frm.refresh_field("process_cost_values");
					}
				},
			});
		}
	},
});

function showOrHideColumns(frm) {
	let hidden = !frm.doc.depends_on_attribute;
	let grid = frm.fields_dict.process_cost_values.grid;

	if (grid.grid_rows && grid.grid_rows.length > 0) {
		grid.update_docfield_property("attribute_value", "hidden", hidden ? 1 : 0);
		grid.update_docfield_property("attribute_value", "reqd", hidden ? 0 : 1);
		grid.refresh();
	}
}
