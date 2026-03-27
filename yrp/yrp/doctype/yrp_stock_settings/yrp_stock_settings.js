// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Stock Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Create Dimension Fields"), function () {
			frappe.call({
				method: "yrp.stock.dimensions.create_dimension_fields",
				freeze: true,
				freeze_message: __("Creating dimension fields on stock DocTypes..."),
				callback: function (r) {
					if (!r.exc) {
						frappe.msgprint(__("Dimension fields created successfully."));
					}
				},
			});
		});
	},
});
