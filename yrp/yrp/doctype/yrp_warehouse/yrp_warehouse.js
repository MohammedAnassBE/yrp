// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("Warehouse", {
	refresh(frm) {
		frappe.dynamic_link = {
			doc: frm.doc,
			fieldname: "name",
			doctype: "Warehouse",
		};

		if (frm.doc.__islocal) {
			hide_field(["address_html", "contact_html"]);
			frappe.contacts.clear_address_and_contact(frm);
		} else {
			unhide_field(["address_html", "contact_html"]);
			frappe.contacts.render_address_and_contact(frm);
		}
	},
});
