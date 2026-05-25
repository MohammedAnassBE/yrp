frappe.provide("frappe.yrp.stock");

frappe.ui.form.on("Stock Entry", {
	setup(frm) {
		frm.set_query("from_warehouse", () => {
			const filters = {};
			if (frm.doc.from_supplier) filters.supplier = frm.doc.from_supplier;
			return { filters };
		});
		frm.set_query("to_warehouse", () => {
			const filters = {};
			if (frm.doc.to_supplier) filters.supplier = frm.doc.to_supplier;
			return { filters };
		});
	},

	refresh(frm) {
		// Clean up previous Vue app and event listener before re-mounting
		if (frm.itemEditor) {
			frm.itemEditor.app.unmount();
		}
		if (frm._stock_updated_handler && frappe.yrp.eventBus) {
			frappe.yrp.eventBus.$off("stock_updated", frm._stock_updated_handler);
		}

		// Mount fresh Vue editor
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

		// Register event listener (store reference for cleanup)
		if (frappe.yrp.eventBus) {
			frm._stock_updated_handler = () => frm.dirty();
			frappe.yrp.eventBus.$on("stock_updated", frm._stock_updated_handler);
		}

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

		add_create_inspection_button(frm);
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

function add_create_inspection_button(frm) {
	// Only Material Receipt SEs can feed an Inspection Entry.
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.purpose !== "Material Receipt") return;
	frappe.db.count("Inspection Entry", {
		filters: {
			against: "Stock Entry",
			against_id: frm.doc.name,
			docstatus: ["<", 2],
		},
	}).then((count) => {
		if (count > 0) {
			frm.add_custom_button(__("View Inspection Entries"), () => {
				frappe.set_route("List", "Inspection Entry", {
					against: "Stock Entry",
					against_id: frm.doc.name,
				});
			});
		}
		frm.add_custom_button(__("Create Inspection Entry"), () => {
			const ie = frappe.model.get_new_doc("Inspection Entry");
			ie.against = "Stock Entry";
			ie.against_id = frm.doc.name;
			frappe.set_route("Form", "Inspection Entry", ie.name);
		});
	});
}
