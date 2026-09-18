// YRP matrix workflow layered onto ERPNext's Purchase Order. Ordinary ERPNext
// Purchase Orders retain the native form and controller behavior.

frappe.ui.form.on("Purchase Order", {
	setup(frm) {
		frm.set_query("default_delivery_location", () => ({
			filters: { is_company_location: frm.doc.deliver_to_supplier ? 0 : 1 },
		}));
	},

	refresh(frm) {
		if (!frm.doc.is_yrp_managed) {
			frm.set_df_property("items", "hidden", 0);
			return;
		}
		mount_yrp_po_editor(frm);
		add_yrp_po_status_actions(frm);
	},

	is_yrp_managed(frm) {
		if (frm.doc.is_yrp_managed) {
			frm.set_value("naming_series", "YRP-PO-.YYYY.-");
		}
		frm.trigger("refresh");
	},

	validate(frm) {
		if (frm.doc.is_yrp_managed) sync_yrp_po_editor(frm);
	},

	before_save(frm) {
		if (frm.doc.is_yrp_managed) sync_yrp_po_editor(frm);
	},
});

function mount_yrp_po_editor(frm) {
	if (!frappe.yrp?.work_order?.ItemEditor || !frm.fields_dict.item_html) return;
	if (frm.itemEditor) frm.itemEditor.app.unmount();
	frm.set_df_property("items", "hidden", 1);
	frm.set_df_property("items", "reqd", 0);
	frm.set_df_property("item_html", "hidden", 0);
	$(frm.fields_dict.item_html.wrapper).empty();
	frm.itemEditor = new frappe.yrp.work_order.ItemEditor(frm.fields_dict.item_html.wrapper, {
		title: "",
		editorType: "purchase_order",
		showDimensions: true,
		allowCreate: true,
		allowEdit: true,
		allowRemove: true,
		showSecondary: true,
	});
	frm.itemEditor.load_data(get_yrp_po_item_details(frm));
	frm.itemEditor.update_status();
	if (frappe.yrp.eventBus && !frm._po_editor_dirty_handler) {
		frm._po_editor_dirty_handler = () => frm.dirty();
		frappe.yrp.eventBus.$on("work_order_items_updated", frm._po_editor_dirty_handler);
	}
}

function sync_yrp_po_editor(frm) {
	frm.doc.items = (frm.doc.items || []).filter((row) => row.item_code || flt(row.qty) || row.uom);
	if (!frm.itemEditor) return;
	const items = frm.itemEditor.get_items();
	const hasQty = (items || []).some((group) =>
		(group.items || []).some((item) =>
			Object.values(item.values || {}).some((value) => flt(value.qty) > 0)
		)
	);
	if (!hasQty) frappe.throw(__("Enter Qty to continue"));
	frm.doc.item_details = JSON.stringify(items);
}

function get_yrp_po_item_details(frm) {
	if (frm.doc.__onload?.item_details) return frm.doc.__onload.item_details;
	if (!frm.doc.item_details) return [];
	try {
		return typeof frm.doc.item_details === "string"
			? JSON.parse(frm.doc.item_details)
			: frm.doc.item_details;
	} catch (error) {
		return [];
	}
}

function add_yrp_po_status_actions(frm) {
	if (frm.doc.docstatus !== 1) return;
	const method = frm.doc.open_status === "Open" ? "close_purchase_order" : "reopen_purchase_order";
	const label = frm.doc.open_status === "Open" ? __("Close") : __("Reopen");
	frm.add_custom_button(label, () => {
		if (frm.is_dirty()) frappe.throw(__("Please save the document first"));
		frappe.call({
			method: `yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order.${method}`,
			args: { purchase_order: frm.doc.name },
			callback: () => frm.reload_doc(),
		});
	});
	frm.page.add_menu_item(__("Refresh YRP Fulfilment Status"), () => {
		frappe.call({
			method: "yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order.refresh_status",
			args: { purchase_order: frm.doc.name },
			callback: () => frm.reload_doc(),
		});
	});
}
