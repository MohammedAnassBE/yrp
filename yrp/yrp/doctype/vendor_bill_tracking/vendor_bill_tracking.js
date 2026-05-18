frappe.ui.form.on("Vendor Bill Tracking", {
	async refresh(frm) {
		if (frm.is_new()) {
			clear_carryover_fields(frm);
			return;
		}
		if (frm.doc.docstatus !== 1) return;

		const status_terminal = ["Closed", "Cancelled"].includes(frm.doc.form_status);
		if (!status_terminal) {
			frm.add_custom_button(__("Assign"), () => show_assign_dialog(frm));
		}
		frm.page.btn_secondary.hide();

		if (!frm.doc.purchase_invoice) {
			if (frappe.user.has_role("HR User") || frappe.user.has_role("System Manager")) {
				frm.add_custom_button(__("Cancel"), () => show_cancel_dialog(frm));
			}
			if (frappe.user.has_role("Accounts Manager") || frappe.user.has_role("Accounts User")) {
				frm.add_custom_button(__("Create Purchase Invoice"), () => {
					const pi = frappe.model.get_new_doc("Purchase Invoice");
					pi.supplier = frm.doc.supplier;
					pi.bill_date = frm.doc.bill_date;
					pi.bill_no = frm.doc.bill_no;
					pi.vendor_bill_tracking = frm.doc.name;
					frappe.set_route("Form", pi.doctype, pi.name);
				});
			}
		}

		if (frm.doc.purchase_invoice) {
			frm.add_custom_button(__("Show Purchase Invoice"), () => {
				frappe.set_route("Form", "Purchase Invoice", frm.doc.purchase_invoice);
			});
		}

		const can_receive = await can_show_bill_received(frm);
		if (can_receive && frm.doc.form_status !== "Closed") {
			frm.add_custom_button(__("Bill Received"), () => {
				frappe.call({
					method: "yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.make_bill_received_acknowledgement",
					args: { doc_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			});
		}
	},
});

function clear_carryover_fields(frm) {
	frm.set_value("purchase_invoice", null);
	frm.set_value("form_status", null);
	frm.set_value("assigned_to", null);
	frm.set_value("vendor_bill_tracking_history", []);
	frm.refresh_fields();
}

async function can_show_bill_received(frm) {
	if (!frm.doc.assigned_to) return false;
	let last_item = null;
	for (const row of frm.doc.vendor_bill_tracking_history || []) {
		if (row.assigned_to === frm.doc.assigned_to) last_item = row;
	}
	if (!last_item || last_item.received) return false;
	return await new Promise((resolve) => {
		frappe.call({
			method: "yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.check_for_can_show_receive_btn",
			args: { name: frm.doc.name },
			callback: (r) => resolve(r.message),
		});
	});
}

function show_assign_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Assign Vendor Bill"),
		fields: [
			{ label: "Assigned To", fieldname: "assigned_to", fieldtype: "Link", options: "Department", reqd: 1 },
			{ label: "Remarks", fieldname: "remarks", fieldtype: "Small Text" },
		],
		primary_action_label: __("Submit"),
		primary_action: (values) => {
			frappe.call({
				method: "yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.assign_vendor_bill",
				args: { name: frm.doc.name, assigned_to: values.assigned_to, remarks: values.remarks },
				freeze: true,
				freeze_message: __("Assigning..."),
				callback: () => {
					dialog.hide();
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}

function show_cancel_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Cancel Vendor Bill"),
		fields: [
			{ label: "Cancel Reason", fieldname: "cancel_reason", fieldtype: "Small Text", reqd: 1 },
		],
		primary_action_label: __("Confirm"),
		primary_action: (values) => {
			frappe.call({
				method: "yrp.yrp.doctype.vendor_bill_tracking.vendor_bill_tracking.cancel_vendor_bill",
				args: { name: frm.doc.name, cancel_reason: values.cancel_reason },
				freeze: true,
				freeze_message: __("Cancelling..."),
				callback: () => {
					dialog.hide();
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}
