// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Settings", {
	setup(frm) {
		frm.set_query("po_dependent_attribute_value", function () {
			return {
				filters: {
					attribute_name: frm.doc.po_dependent_attribute || "",
				},
			};
		});
	},
});
