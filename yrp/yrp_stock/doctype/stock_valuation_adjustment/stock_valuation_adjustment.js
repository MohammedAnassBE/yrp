frappe.ui.form.on("Stock Valuation Adjustment", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			// Frappe's toolbar can install the standard Cancel action after the
			// form refresh handlers. Clear it on the next UI tick as well.
			const clear_standard_actions = () => {
				frm.page.clear_primary_action();
				frm.page.clear_secondary_action();
			};
			clear_standard_actions();
			setTimeout(clear_standard_actions, 0);
		}
		if (!["Calculation Failed", "Apply Failed"].includes(frm.doc.status)) {
			return;
		}
		frm.add_custom_button(__("Retry Valuation"), () => {
			frappe.call({
				method: "yrp.yrp_stock.doctype.stock_valuation_adjustment.stock_valuation_adjustment.retry_adjustment",
				args: { adjustment: frm.doc.name },
				freeze: true,
				freeze_message: __("Queueing valuation retry..."),
				callback() {
					frm.reload_doc();
				},
			});
		}, __("Actions"));
	},
});
