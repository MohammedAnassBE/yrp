frappe.ui.form.on("Goods Received Note", {
	setup(frm) {
		frm.set_query("against_id", () => ({
			filters: {
				docstatus: 1,
				open_status: ["!=", "Close"],
			},
		}));
		frm.set_query("delivery_challan", () => ({
			filters: {
				docstatus: 1,
				work_order: frm.doc.against_id || "",
			},
		}));
	},

	refresh(frm) {
		mount_grn_editor(frm);
	},

	against_id(frm) {
		if (!frm.doc.against_id || frm.doc.docstatus !== 0 || frm.doc.against !== "Work Order") {
			return;
		}
		frappe.call({
			method: "yrp.yrp.doctype.goods_received_note.goods_received_note.get_work_order_defaults",
			args: {
				work_order: frm.doc.against_id,
			},
			callback(r) {
				if (!r.message) {
					return;
				}
				apply_response_values(frm, r.message, [
					"process_name",
					"item",
					"production_detail",
					"supplier",
					"delivery_location",
					"from_warehouse",
					"to_warehouse",
				]);
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
		if (!has_received_qty(items)) {
			frappe.throw(__("Enter Received Qty to continue"));
		}
		frm.doc.item_details = JSON.stringify(items);
	},
});

function apply_response_values(frm, message, base_fields) {
	const ignore = new Set(["items", "item_details"]);
	const fields = new Set(base_fields);
	for (const field of Object.keys(message || {})) {
		if (!ignore.has(field) && frm.fields_dict[field]) {
			fields.add(field);
		}
	}
	for (const field of fields) {
		if (message[field]) {
			frm.set_value(field, message[field]);
		}
	}
}

function mount_grn_editor(frm) {
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
		title: "Receive Items",
		editorType: "goods_received_note",
		showDimensions: true,
		allowCreate: false,
		allowEdit: false,
		allowRemove: false,
	});
	const data = get_item_details(frm);
	frm.itemEditor.load_data(data);
	frm.itemEditor.update_status();
	bind_grn_dirty_handler(frm);
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

function bind_grn_dirty_handler(frm) {
	if (!frappe.yrp.eventBus || frm._grn_editor_dirty_handler) return;
	frm._grn_editor_dirty_handler = () => frm.dirty();
	frappe.yrp.eventBus.$on("work_order_items_updated", frm._grn_editor_dirty_handler);
}

function has_received_qty(item_details) {
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
