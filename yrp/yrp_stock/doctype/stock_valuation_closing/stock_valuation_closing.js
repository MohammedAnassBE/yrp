frappe.ui.form.on("Stock Valuation Closing", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0 || frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Check Readiness"), () => {
			frappe.call({
				method: "yrp.yrp_stock.doctype.stock_valuation_closing.stock_valuation_closing.check_readiness",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Checking stock valuation readiness..."),
				callback() {
					frm.reload_doc();
				},
			});
		}, __("Actions"));
	},
});
