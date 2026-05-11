frappe.ui.form.on("Purchase Order", {
	setup(frm) {
		frm.set_query("supplier", () => ({
			filters: { disabled: 0 },
		}));
		frm.set_query("delivery_warehouse", () => ({
			filters: { disabled: 0 },
		}));
	},

	refresh(frm) {
		mount_po_editor(frm);
		add_status_actions(frm);
	},

	validate(frm) {
		if (!frm.itemEditor) {
			return;
		}
		const items = frm.itemEditor.get_items();
		if (!has_order_qty(items)) {
			frappe.throw(__("Enter Qty to continue"));
		}
		frm.doc.item_details = JSON.stringify(items);
	},
});

function mount_po_editor(frm) {
	if (!frappe.yrp.work_order || !frappe.yrp.work_order.ItemEditor || !frm.fields_dict.item_html) {
		return;
	}
	if (frm.itemEditor) {
		frm.itemEditor.app.unmount();
	}
	frm.set_df_property("items", "hidden", 1);
	frm.set_df_property("item_html", "hidden", 0);
	$(frm.fields_dict.item_html.wrapper).html("");
	frm.itemEditor = new frappe.yrp.work_order.ItemEditor(frm.fields_dict.item_html.wrapper, {
		title: "",
		editorType: "purchase_order",
		showDimensions: false,
		allowCreate: true,
		allowEdit: true,
		allowRemove: true,
	});
	const data = get_item_details(frm);
	frm.itemEditor.load_data(data);
	frm.itemEditor.update_status();
	bind_po_dirty_handler(frm);
}

function get_item_details(frm) {
	if (frm.doc.__onload && frm.doc.__onload.item_details) {
		return frm.doc.__onload.item_details;
	}
	if (!frm.doc.item_details) {
		return [];
	}
	try {
		return typeof frm.doc.item_details === "string" ? JSON.parse(frm.doc.item_details) : frm.doc.item_details;
	} catch (e) {
		return [];
	}
}

function bind_po_dirty_handler(frm) {
	if (!frappe.yrp.eventBus || frm._po_editor_dirty_handler) return;
	frm._po_editor_dirty_handler = () => frm.dirty();
	frappe.yrp.eventBus.$on("work_order_items_updated", frm._po_editor_dirty_handler);
}

function has_order_qty(item_details) {
	for (const group of item_details || []) {
		for (const item of group.items || []) {
			for (const value of Object.values(item.values || {})) {
				if (flt(value.qty) > 0) {
					return true;
				}
			}
		}
	}
	return false;
}

function add_status_actions(frm) {
	if (frm.doc.docstatus !== 1) {
		return;
	}
	if (frm.doc.open_status === "Open") {
		frm.add_custom_button(__("Close"), () => set_open_status(frm, "Close"));
	} else {
		frm.add_custom_button(__("Reopen"), () => set_open_status(frm, "Open"));
	}
}

function set_open_status(frm, open_status) {
	frappe.call({
		method: "yrp.yrp.doctype.purchase_order.purchase_order.set_open_status",
		args: {
			purchase_order: frm.doc.name,
			open_status,
		},
		callback() {
			frm.reload_doc();
		},
	});
}
