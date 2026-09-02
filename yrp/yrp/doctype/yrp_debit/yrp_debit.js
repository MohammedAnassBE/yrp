frappe.ui.form.on("YRP Debit", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Debit Requested") {
			frm.add_custom_button(__("Approve Debit"), () => {
				frappe.call({
					method: "yrp.yrp.doctype.yrp_debit.yrp_debit.approve_debit",
					args: { name: frm.doc.name },
					callback() {
						frm.reload_doc();
					},
				});
			});
		}
	},
});
