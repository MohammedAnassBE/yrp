frappe.ui.form.on("YRP Goods Received Note", {
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
				work_order: frm.doc.against === "YRP Work Order" ? frm.doc.against_id || "" : "",
			},
		}));
		frm.set_query("from_warehouse", () => {
			const filters = {};
			if (frm.doc.supplier) filters.supplier = frm.doc.supplier;
			return { filters };
		});
		frm.set_query("to_warehouse", () => {
			const filters = {};
			if (frm.doc.delivery_location) filters.supplier = frm.doc.delivery_location;
			return { filters };
		});
	},

	refresh(frm) {
		configure_return_form(frm);
		mount_grn_editor(frm);
		add_complete_transfer_button(frm);
		add_create_inspection_button(frm);
	},

	against(frm) {
		if (frm.doc.docstatus !== 0) {
			return;
		}
		frm.set_value("against_id", "");
		frm.set_value("delivery_challan", "");
		clear_source_values(frm);
		frm.clear_table("items");
		frm.refresh_field("items");
		frm.doc.item_details = "[]";
		frm.clear_table("correction_items");
		frm.refresh_field("correction_items");
		frm.doc.correction_item_details = "[]";
		mount_grn_editor(frm);
	},

	against_id(frm) {
		if (!frm.doc.against_id || frm.doc.docstatus !== 0 || frm.doc.is_return) {
			return;
		}
		load_source_defaults(frm);
	},

	delivery_challan(frm) {
		if (
			frm.doc.docstatus !== 0
			|| frm.doc.against !== "YRP Work Order"
			|| !frm.doc.against_id
			|| frm.doc.is_return
		) {
			return;
		}
		load_source_defaults(frm);
	},

	validate(frm) {
		if (!frm.itemEditor) {
			return;
		}
		const items = frm.itemEditor.get_items();
		const correction_items = frm.correctionEditor ? frm.correctionEditor.get_items() : [];
		if (!has_received_qty(items) && !has_correction_received_qty(correction_items)) {
			frappe.throw(__("Enter Received Qty to continue"));
		}
		frm.doc.item_details = JSON.stringify(items);
		if (frm.correctionEditor) {
			frm.doc.correction_item_details = JSON.stringify(correction_items);
		}
	},
});

function load_source_defaults(frm) {
	const source = get_source_defaults_method(frm);
	if (!source) {
		return;
	}
	frappe.call({
		method: source.method,
		args: source.args,
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
			if (frm.doc.against !== "YRP Work Order") {
				frm.set_value("delivery_challan", "");
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

			frm.clear_table("correction_items");
			for (const row of r.message.correction_items || []) {
				const child = frm.add_child("correction_items");
				Object.assign(child, row);
			}
			frm.refresh_field("correction_items");
			frm.doc.correction_item_details = JSON.stringify(r.message.correction_item_details || []);
			if (frm.correctionEditor) {
				frm.correctionEditor.load_data(r.message.correction_item_details || []);
			}
		},
	});
}

function get_source_defaults_method(frm) {
	if (frm.doc.is_return) {
		return null;
	}
	if (frm.doc.against === "YRP Work Order") {
		return {
			method: "yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.get_work_order_defaults",
			args: { work_order: frm.doc.against_id, delivery_challan: frm.doc.delivery_challan || "" },
		};
	}
	if (frm.doc.against === "YRP Purchase Order") {
		return {
			method: "yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.get_purchase_order_defaults",
			args: { purchase_order: frm.doc.against_id },
		};
	}
	return null;
}

function apply_response_values(frm, message, base_fields) {
	const ignore = new Set(["items", "item_details", "correction_items", "correction_item_details"]);
	const fields = new Set(base_fields);
	for (const field of Object.keys(message || {})) {
		if (!ignore.has(field) && frm.fields_dict[field]) {
			fields.add(field);
		}
	}
	for (const field of fields) {
		if (Object.prototype.hasOwnProperty.call(message, field)) {
			frm.set_value(field, message[field]);
		}
	}
}

function clear_source_values(frm) {
	for (const field of [
		"process_name",
		"item",
		"production_detail",
		"supplier",
		"delivery_location",
		"from_warehouse",
		"to_warehouse",
		"is_rework",
	]) {
		if (frm.fields_dict[field]) {
			frm.set_value(field, "");
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
		title: frm.doc.is_return ? "Return Items" : "Receive Items",
		editorType: "goods_received_note",
		sourceType: frm.doc.against || "YRP Work Order",
		showDimensions: true,
		allowCreate: false,
		allowEdit: false,
		allowRemove: false,
		returnMode: Boolean(frm.doc.is_return),
	});
	const data = get_item_details(frm);
	frm.itemEditor.load_data(data);
	frm.itemEditor.update_status();
	if (!frm.doc.is_return) {
		mount_grn_correction_editor(frm);
	}
	bind_grn_dirty_handler(frm);
}

function mount_grn_correction_editor(frm) {
	if (!frappe.yrp.work_order.CorrectionItemEditor || !frm.fields_dict.correction_item_html) {
		return;
	}
	if (frm.correctionEditor) {
		frm.correctionEditor.app.unmount();
	}
	frm.set_df_property("correction_items", "hidden", 1);
	frm.set_df_property("correction_item_html", "hidden", 0);
	$(frm.fields_dict.correction_item_html.wrapper).html("");
	frm.correctionEditor = new frappe.yrp.work_order.CorrectionItemEditor(frm.fields_dict.correction_item_html.wrapper, {
		editorType: "goods_received_note",
		allowEdit: true,
		showDimensions: true,
		showSecondary: false,
	});
	frm.correctionEditor.load_data(get_correction_item_details(frm));
	frm.correctionEditor.update_status();
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

function get_correction_item_details(frm) {
	if (frm.doc.__onload && frm.doc.__onload.correction_item_details) {
		return frm.doc.__onload.correction_item_details;
	}
	if (!frm.doc.correction_item_details) {
		return [];
	}
	try {
		return typeof frm.doc.correction_item_details === "string"
			? JSON.parse(frm.doc.correction_item_details)
			: frm.doc.correction_item_details;
	} catch (e) {
		return [];
	}
}

function bind_grn_dirty_handler(frm) {
	if (!frappe.yrp.eventBus || frm._grn_editor_dirty_handler) return;
	frm._grn_editor_dirty_handler = () => frm.dirty();
	frappe.yrp.eventBus.$on("work_order_items_updated", frm._grn_editor_dirty_handler);
}

function add_complete_transfer_button(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.is_internal_unit || frm.doc.transfer_complete) {
		return;
	}
	frm.add_custom_button(__("Complete Transfer"), () => {
		frappe.call({
			method: "yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.make_grn_completion",
			args: { doc_name: frm.doc.name },
			freeze: true,
			freeze_message: __("Creating Stock Entry..."),
			callback(r) {
				if (r.message) {
					frappe.set_route("Form", "YRP Stock Entry", r.message);
				}
			},
		});
	});
}

function add_create_inspection_button(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.is_return) return;
	if (frm.doc.is_rework) return;
	frappe.db.count("YRP Inspection Entry", {
		filters: {
			against: "YRP Goods Received Note",
			against_id: frm.doc.name,
			docstatus: ["<", 2],
		},
	}).then((count) => {
		if (count > 0) {
			frm.add_custom_button(__("View Inspection Entries"), () => {
				frappe.set_route("List", "YRP Inspection Entry", {
					against: "YRP Goods Received Note",
					against_id: frm.doc.name,
				});
			});
		}
		frm.add_custom_button(__("Create Inspection Entry"), () => {
			const ie = frappe.model.get_new_doc("YRP Inspection Entry");
			ie.against = "YRP Goods Received Note";
			ie.against_id = frm.doc.name;
			frappe.set_route("Form", "YRP Inspection Entry", ie.name);
		});
	});
}

function configure_return_form(frm) {
	if (!frm.doc.is_return) return;
	for (const fieldname of [
		"against",
		"against_id",
		"delivery_challan",
		"supplier",
		"delivery_location",
		"from_warehouse",
		"to_warehouse",
		"freight_charges",
	]) {
		if (frm.fields_dict[fieldname]) {
			frm.set_df_property(fieldname, "read_only", 1);
		}
	}
	for (const fieldname of [
		"correction_items_section",
		"correction_items",
		"correction_item_html",
	]) {
		if (frm.fields_dict[fieldname]) {
			frm.set_df_property(fieldname, "hidden", 1);
		}
	}
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

function has_correction_received_qty(correction_blocks) {
	for (const block of correction_blocks || []) {
		if (has_received_qty(block.item_details)) {
			return true;
		}
	}
	return false;
}
