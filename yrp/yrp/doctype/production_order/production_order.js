// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("Production Order", {
	setup(frm) {
		frm.set_query("production_term", function () {
			return { filters: { docstatus: 1 } };
		});
	},

	refresh(frm) {
		// Mount Vue component on details_html
		let wrapper = frm.fields_dict["details_html"].wrapper;
		$(wrapper).html("");

		frm.productionTable = new frappe.production.ui.ProductionOrderTable(wrapper);

		// Pass settings
		if (frm.doc.__onload && frm.doc.__onload.production_settings) {
			frm.productionTable.set_settings(frm.doc.__onload.production_settings);
		}

		// Load existing data
		if (frm.doc.__onload && frm.doc.__onload.item_details) {
			frm.productionTable.load_data(frm.doc.__onload.item_details);
		}

		// Disable editing after submit
		if (frm.doc.docstatus !== 0) {
			frm.productionTable.set_edit(false);
		}
	},

	validate(frm) {
		if (frm.productionTable) {
			let items = frm.productionTable.get_final_output();
			if (items && items.length > 0) {
				frm.doc.item_details = JSON.stringify(items);
			} else {
				frappe.throw(__("Add items to continue."));
			}
		} else {
			frappe.throw(__("Please refresh and try again."));
		}
	},
});
