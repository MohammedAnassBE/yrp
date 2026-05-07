frappe.ui.form.on("Delivery Challan", {
	setup(frm) {
		frm.set_query("work_order", () => ({
			filters: {
				docstatus: 1,
				open_status: ["!=", "Close"],
			},
		}));
	},

	refresh(frm) {
		mount_dc_editor(frm);
	},

	work_order(frm) {
		if (!frm.doc.work_order || frm.doc.docstatus !== 0) {
			return;
		}
		frappe.call({
			method: "yrp.yrp.doctype.delivery_challan.delivery_challan.get_work_order_defaults",
			args: {
				work_order: frm.doc.work_order,
			},
			callback(r) {
				if (!r.message) {
					return;
				}
				for (const field of [
					"process_name",
					"item",
					"production_detail",
					"supplier",
					"from_location",
					"from_warehouse",
					"to_warehouse",
				]) {
					if (r.message[field]) {
						frm.set_value(field, r.message[field]);
					}
				}
				frm.clear_table("items");
				for (const row of r.message.items || []) {
					const child = frm.add_child("items");
					Object.assign(child, row);
				}
				frm.refresh_field("items");
				frm.doc.item_details = JSON.stringify(r.message.item_details || []);
				if (frm.itemEditor) {
					frm.itemEditor.load_data(r.message.item_details || []);
				}
			},
		});
	},

	validate(frm) {
		if (!frm.itemEditor) {
			return;
		}
		const items = frm.itemEditor.get_items();
		if (!items || !items.length) {
			frappe.throw(__("Add Items to continue"));
		}
		frm.doc.item_details = JSON.stringify(items);
	},
});

function mount_dc_editor(frm) {
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
		title: "Deliverables",
		editorType: "delivery_challan",
		showDimensions: true,
		allowCreate: false,
		allowEdit: true,
		allowRemove: true,
	});
	const data = get_item_details(frm);
	frm.itemEditor.load_data(data);
	frm.itemEditor.update_status();
	bind_dc_dirty_handler(frm);
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

function bind_dc_dirty_handler(frm) {
	if (!frappe.yrp.eventBus || frm._dc_editor_dirty_handler) return;
	frm._dc_editor_dirty_handler = () => frm.dirty();
	frappe.yrp.eventBus.$on("work_order_items_updated", frm._dc_editor_dirty_handler);
}
