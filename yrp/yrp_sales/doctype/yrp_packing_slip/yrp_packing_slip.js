// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

frappe.ui.form.on("YRP Packing Slip", {
	setup(frm) {
		frm.set_query("delivery_note", () => ({ filters: { docstatus: 1, delivered_at: ["is", "not set"] } }));
	},
	refresh(frm) {
		if (frm.is_new() && frm.doc.delivery_note && !(frm.doc.items || []).some((row) => row.dn_detail)) {
			frm.trigger("delivery_note");
		}
		if (frm.doc.docstatus === 1 && !frm.doc.delivered_at) {
			frm.add_custom_button(__("Mark Delivered"), () =>
				frm.call("mark_delivered").then(() => frm.reload_doc())
			);
		}
	},
	delivery_note(frm) {
		if (!frm.doc.delivery_note) return;
		frappe
			.xcall("yrp.yrp_sales.doctype.yrp_packing_slip.yrp_packing_slip.make_packing_slip", {
				delivery_note: frm.doc.delivery_note,
			})
			.then((slip) => frm.set_value("items", slip.items));
	},
});
