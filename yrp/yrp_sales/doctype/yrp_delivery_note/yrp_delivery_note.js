// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Delivery Note", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.delivered_at) return;
		frm.add_custom_button(
			__("Packing Slip"),
			() => frappe.new_doc("YRP Packing Slip", { delivery_note: frm.doc.name }),
			__("Create")
		);
		frm.add_custom_button(__("Mark Delivered"), () =>
			frm.call("mark_delivered").then(() => frm.reload_doc())
		);
	},
});
