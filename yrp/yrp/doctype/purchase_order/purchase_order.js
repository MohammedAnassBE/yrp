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
		sync_item_editor_payload(frm);
	},

	before_save(frm) {
		sync_item_editor_payload(frm);
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
	frm.set_df_property("items", "reqd", 0);
	frm.itemEditor = new frappe.yrp.work_order.ItemEditor(frm.fields_dict.item_html.wrapper, {
		title: "",
		editorType: "purchase_order",
		showDimensions: false,
		allowCreate: true,
		allowEdit: true,
		allowRemove: true,
		showSecondary: true,
	});
	const data = get_item_details(frm);
	frm.itemEditor.load_data(data);
	frm.itemEditor.update_status();
	bind_po_dirty_handler(frm);
}

function sync_item_editor_payload(frm) {
	remove_blank_standard_item_rows(frm);
	if (!frm.itemEditor) {
		return;
	}
	const items = frm.itemEditor.get_items();
	if (!has_order_qty(items)) {
		frappe.throw(__("Enter Qty to continue"));
	}
	frm.doc.item_details = JSON.stringify(items);
}

function remove_blank_standard_item_rows(frm) {
	frm.doc.items = (frm.doc.items || []).filter((row) => (
		row.item_variant || flt(row.qty) || row.uom
	));
	frm.refresh_field("items");
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
		frm.add_custom_button(__("Close"), () => close_purchase_order(frm));
	} else {
		frm.add_custom_button(__("Reopen"), () => reopen_purchase_order(frm));
	}
	frm.add_custom_button(__("Create Delivery Challan"), () => show_create_dc_dialog(frm));
	frm.page.add_menu_item(__("Refresh Status"), () => refresh_status(frm));
}

function show_create_dc_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Create Delivery Challan from {0}", [frm.doc.name]),
		fields: [
			{
				fieldname: "work_order",
				fieldtype: "Link",
				options: "Work Order",
				label: __("Work Order"),
				reqd: 1,
				description: __("The Work Order this Delivery Challan will be against. The Purchase Order is a traceability reference only."),
				get_query: () => ({
					filters: { docstatus: 1 },
				}),
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			d.hide();
			const po_name = frm.doc.name;
			const wo_name = values.work_order;
			// route_options is consumed by Frappe when it creates a new doc on route "new".
			// Set it BEFORE the navigation so it propagates through frm.set_value during form mount.
			frappe.route_options = {
				work_order: wo_name,
				purchase_order: po_name,
			};
			frappe.set_route("Form", "Delivery Challan", "new").then(() => {
				// Belt-and-suspenders: ensure the two fields are populated even if
				// route_options didn't propagate (which can happen depending on Frappe build).
				if (cur_frm && cur_frm.doctype === "Delivery Challan") {
					if (!cur_frm.doc.work_order) {
						cur_frm.set_value("work_order", wo_name);
					}
					if (!cur_frm.doc.purchase_order) {
						cur_frm.set_value("purchase_order", po_name);
					}
				}
			});
		},
	});
	d.show();
}

function close_purchase_order(frm) {
	if (frm.is_dirty()) {
		frappe.throw(__("Please save the document before closing"));
	}
	frappe.confirm(__("Are you sure you want to close this Purchase Order?"), () => {
		call_status_method(frm, "close_purchase_order");
	});
}

function reopen_purchase_order(frm) {
	call_status_method(frm, "reopen_purchase_order");
}

function refresh_status(frm) {
	call_status_method(frm, "refresh_status");
}

function call_status_method(frm, method) {
	frappe.call({
		method: `yrp.yrp.doctype.purchase_order.purchase_order.${method}`,
		args: {
			purchase_order: frm.doc.name,
		},
		callback() {
			frm.reload_doc();
		},
	});
}
