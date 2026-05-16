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
		add_complete_transfer_button(frm);
	},

	work_order(frm) {
		if (!frm.doc.work_order || frm.doc.docstatus !== 0) {
			return;
		}
		frappe.call({
			method: "yrp.yrp.doctype.delivery_challan.delivery_challan.get_work_order_defaults",
			args: {
				work_order: frm.doc.work_order,
				posting_date: frm.doc.posting_date,
				posting_time: frm.doc.posting_time,
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
					"from_location",
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
		if (!has_delivery_qty(items)) {
			frappe.throw(__("Enter Delivery Qty to continue"));
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
		allowEdit: false,
		allowRemove: false,
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

function add_complete_transfer_button(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.is_internal_unit || frm.doc.transfer_complete) {
		return;
	}
	frm.add_custom_button(__("Complete Transfer"), () => {
		frappe.call({
			method: "yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion",
			args: { doc_name: frm.doc.name },
			freeze: true,
			freeze_message: __("Creating Stock Entry..."),
			callback(r) {
				if (r.message) {
					frappe.set_route("Form", "Stock Entry", r.message);
				}
			},
		});
	});
}

function has_delivery_qty(item_details) {
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
