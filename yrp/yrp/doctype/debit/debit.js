frappe.ui.form.on("Debit", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Debit Requested") {
			frm.add_custom_button(__("Approve Debit"), () => {
				frappe.call({
					method: "yrp.yrp.doctype.debit.debit.approve_debit",
					args: { name: frm.doc.name },
					callback() {
						frm.reload_doc();
					},
				});
			});
		}
	},
});
