frappe.provide("frappe.yrp.stock");

frappe.ui.form.on("Stock Reconciliation", {
	refresh(frm) {
		$(frm.fields_dict["item_html"].wrapper).html("");
		frm.itemEditor = new frappe.yrp.stock.StockReconciliationItem(frm.fields_dict["item_html"].wrapper);

		const onload = frm.doc.__onload && frm.doc.__onload.item_details;
		if (onload) {
			frm.doc.item_details = JSON.stringify(onload);
			frm.itemEditor.load_data(onload);
		} else {
			frm.itemEditor.load_data([]);
		}
		frm.itemEditor.update_status();

		frappe.yrp.eventBus.$on("stock_updated", () => frm.dirty());
	},

	validate(frm) {
		if (!frm.itemEditor) {
			frappe.throw(__("Please refresh and try again."));
		}
		const items = frm.itemEditor.get_items();
		if (!items || items.length === 0) {
			frappe.throw(__("Add Items to continue"));
		}
		frm.doc.item_details = JSON.stringify(items);
	},
});
