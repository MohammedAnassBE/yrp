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
		if (frm.doc.docstatus === 1 && frm.doc.open_status !== "Close") {
			frm.add_custom_button(
				frm.doc.open_status === "Close Request" ? __("Approve Close") : __("Close"),
				() => open_close_dialog(frm),
			);
		}
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

function open_close_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Close Work Order"),
		fields: [
			{ fieldtype: "HTML", fieldname: "debit_list_html" },
			{
				fieldtype: "Select",
				fieldname: "with_debit",
				label: __("Debit"),
				options: "Without Debit\nWith Debit",
				default: "Without Debit",
			},
			{
				fieldtype: "Data",
				fieldname: "debit_no",
				label: __("Debit No"),
				depends_on: "eval: doc.with_debit == 'With Debit'",
				mandatory_depends_on: "eval: doc.with_debit == 'With Debit'",
			},
			{
				fieldtype: "Currency",
				fieldname: "debit_value",
				label: __("Debit Value"),
				depends_on: "eval: doc.with_debit == 'With Debit'",
				mandatory_depends_on: "eval: doc.with_debit == 'With Debit'",
			},
			{
				fieldtype: "Small Text",
				fieldname: "debit_reason",
				label: __("Debit Reason"),
				depends_on: "eval: doc.with_debit == 'With Debit'",
				mandatory_depends_on: "eval: doc.with_debit == 'With Debit'",
			},
			{ fieldtype: "Section Break", label: __("Close Details") },
			{
				fieldtype: "Select",
				fieldname: "close_reason",
				label: __("Close Reason"),
				options: "\nCutting Shortage\nPrinting Shortage\nSewing Shortage\nSewing Missing\nOthers",
				reqd: 1,
				default: frm.doc.close_reason || "",
			},
			{
				fieldtype: "Data",
				fieldname: "close_other_reason",
				label: __("Other Reason"),
				depends_on: "eval: doc.close_reason == 'Others'",
				mandatory_depends_on: "eval: doc.close_reason == 'Others'",
				default: frm.doc.close_other_reason || "",
			},
			{
				fieldtype: "Small Text",
				fieldname: "close_remarks",
				label: __("Close Remarks"),
				default: frm.doc.close_remarks || "",
			},
		],
		primary_action_label: __("Close Work Order"),
		primary_action(values) {
			if (!values) return;
			const close_work_order = () => {
				frappe.call({
					method: "yrp.yrp.doctype.work_order.work_order.update_stock",
					args: {
						work_order: frm.doc.name,
						close_reason: values.close_reason,
						close_other_reason: values.close_other_reason || "",
						close_remarks: values.close_remarks || "",
					},
					freeze: true,
					callback() {
						d.hide();
						frm.reload_doc();
					},
				});
			};
			if (values.with_debit === "With Debit") {
				frappe.call({
					method: "yrp.yrp.doctype.essdee_debit.essdee_debit.create_debit",
					args: {
						work_order: frm.doc.name,
						debit_no: values.debit_no,
						debit_value: values.debit_value,
						reason: values.debit_reason,
						on_close: 1,
					},
					freeze: true,
					callback() {
						close_work_order();
					},
				});
			} else {
				close_work_order();
			}
		},
	});
	d.show();
	render_debit_list(frm.doc.name, d);
}

function render_debit_list(work_order, dialog) {
	frappe.call({
		method: "yrp.yrp.doctype.work_order.work_order.get_debits",
		args: { work_order },
		callback(r) {
			const debits = r.message || [];
			if (!debits.length) return;
			let html = `<h4>${__("Debit List")}</h4><table class="table table-sm table-bordered">
				<thead><tr><th>${__("Name")}</th><th>${__("Debit No")}</th><th>${__("Value")}</th><th>${__("Status")}</th><th>${__("Action")}</th></tr></thead><tbody>`;
			for (const debit of debits) {
				html += `<tr>
					<td><a href="/app/debit/${encodeURIComponent(debit.name)}" target="_blank">${frappe.utils.escape_html(debit.name)}</a></td>
					<td>${frappe.utils.escape_html(debit.debit_no || "")}</td>
					<td>${format_currency(debit.debit_value || 0)}</td>
					<td>${frappe.utils.escape_html(debit.status || "")}</td>
					<td>${debit.status !== "Approved" ? `<button class="btn btn-xs btn-success yrp-approve-debit" data-name="${frappe.utils.escape_html(debit.name)}">${__("Approve")}</button>` : ""}</td>
				</tr>`;
			}
			html += "</tbody></table>";
			$(dialog.fields_dict.debit_list_html.wrapper).html(html);
			$(dialog.fields_dict.debit_list_html.wrapper).find(".yrp-approve-debit").on("click", function () {
				frappe.call({
					method: "yrp.yrp.doctype.essdee_debit.essdee_debit.approve_debit",
					args: { name: $(this).data("name") },
					callback() {
						render_debit_list(work_order, dialog);
					},
				});
			});
		},
	});
}
