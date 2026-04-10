frappe.provide("frappe.yrp.stock");

frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		// Mount Vue editor
		$(frm.fields_dict["item_html"].wrapper).html("");
		frm.itemEditor = new frappe.yrp.stock.StockEntryItem(frm.fields_dict["item_html"].wrapper);

		const onload = frm.doc.__onload && frm.doc.__onload.item_details;
		if (onload) {
			frm.doc.item_details = JSON.stringify(onload);
			frm.itemEditor.load_data(onload);
		} else {
			frm.itemEditor.load_data([]);
		}
		frm.itemEditor.update_status();

		frappe.yrp.eventBus.$on("stock_updated", () => frm.dirty());

		// Purpose-based field toggles
		toggle_related_fields(frm);
		set_mandatory_fields(frm);

		// Send to Warehouse buttons
		if (frm.doc.docstatus === 1 && frm.doc.purpose === "Send to Warehouse" && !frm.doc.skip_transit) {
			if (frm.doc.per_transferred < 100) {
				frm.add_custom_button(__('End Transit'), function () {
					frappe.model.open_mapped_doc({
						method: "yrp.yrp_stock.doctype.stock_entry.stock_entry.make_stock_in_entry",
						frm: frm,
					});
				});
			}
			if (frm.doc.per_transferred > 0) {
				frm.add_custom_button(__('Received Stock Entries'), function () {
					frappe.route_options = {
						'outgoing_stock_entry': frm.doc.name,
						'docstatus': ['!=', 2],
					};
					frappe.set_route('List', 'Stock Entry');
				}, __("View"));
			}
		}
	},

	purpose(frm) {
		toggle_related_fields(frm);
		set_mandatory_fields(frm);
		if (frm.doc.purpose && frappe.yrp.eventBus) {
			frappe.yrp.eventBus.$emit("purpose_updated", frm.doc.purpose);
		}
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

function toggle_related_fields(frm) {
	const p = frm.doc.purpose;
	frm.toggle_enable("from_warehouse", p !== "Material Receipt");
	frm.toggle_enable("to_warehouse", p !== "Material Issue" && p !== "Material Consumed");
}

function set_mandatory_fields(frm) {
	const p = frm.doc.purpose;
	frm.toggle_reqd("from_warehouse", p !== "Material Receipt");
	frm.toggle_reqd("to_warehouse", p !== "Material Issue" && p !== "Material Consumed");
}
