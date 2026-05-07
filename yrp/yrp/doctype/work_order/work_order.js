frappe.provide("frappe.yrp.work_order");

frappe.ui.form.on("Work Order", {
	refresh(frm) {
		mount_work_order_editor(frm, {
			fieldname: "deliverable_items",
			editor_key: "deliverableEditor",
			payload_field: "deliverable_details",
			onload_key: "deliverable_details",
			source_table: "deliverables",
			options: {
				title: "Deliverables",
				editorType: "work_order_deliverables",
				showDimensions: false,
				allowCreate: true,
				allowEdit: true,
				allowRemove: true,
			},
		});
		mount_work_order_editor(frm, {
			fieldname: "receivable_items",
			editor_key: "receivableEditor",
			payload_field: "receivable_details",
			onload_key: "receivable_details",
			source_table: "receivables",
			options: {
				title: "Receivables",
				editorType: "work_order_receivables",
				showDimensions: false,
				allowCreate: true,
				allowEdit: true,
				allowRemove: true,
			},
		});
	},

	validate(frm) {
		sync_editor_payload(frm, "deliverableEditor", "deliverable_details");
		sync_editor_payload(frm, "receivableEditor", "receivable_details");
	},
});

function mount_work_order_editor(frm, config) {
	if (!frappe.yrp.work_order.ItemEditor || !frm.fields_dict[config.fieldname]) {
		return;
	}
	if (frm[config.editor_key]) {
		frm[config.editor_key].app.unmount();
	}
	frm.set_df_property(config.fieldname, "hidden", 0);
	frm.set_df_property(config.source_table, "hidden", 1);
	$(frm.fields_dict[config.fieldname].wrapper).html("");
	frm[config.editor_key] = new frappe.yrp.work_order.ItemEditor(
		frm.fields_dict[config.fieldname].wrapper,
		config.options,
	);
	const data = get_editor_data(frm, config.payload_field, config.onload_key);
	frm[config.editor_key].load_data(data);
	frm[config.editor_key].update_status();
	bind_editor_dirty_handler(frm);
}

function get_editor_data(frm, payload_field, onload_key) {
	const onload = frm.doc.__onload && frm.doc.__onload[onload_key];
	if (onload) return onload;
	const raw = frm.doc[payload_field];
	if (!raw) return [];
	try {
		return typeof raw === "string" ? JSON.parse(raw) : raw;
	} catch (e) {
		return [];
	}
}

function sync_editor_payload(frm, editor_key, payload_field) {
	if (!frm[editor_key]) return;
	const items = frm[editor_key].get_items();
	if (!items || !items.length) return;
	frm.doc[payload_field] = JSON.stringify(items);
}

function bind_editor_dirty_handler(frm) {
	if (!frappe.yrp.eventBus || frm._work_order_editor_dirty_handler) return;
	frm._work_order_editor_dirty_handler = () => frm.dirty();
	frappe.yrp.eventBus.$on("work_order_items_updated", frm._work_order_editor_dirty_handler);
}
