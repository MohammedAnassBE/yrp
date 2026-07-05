frappe.ui.form.on("Purchase Invoice", {
	setup(frm) {
		frm.set_query("supplier", () => ({ filters: { disabled: 0 } }));
		frm.set_query("grn", "grn", () => {
			if (!frm.doc.supplier) frappe.throw(__("Please set Supplier"));
			if (!frm.doc.against) frappe.throw(__("Please set Against"));
			return {
				filters: [
					["purchase_invoice_name", "is", "not set"],
					["docstatus", "=", 1],
					["supplier", "=", frm.doc.supplier],
					["against", "=", frm.doc.against],
				],
			};
		});
	},

	refresh(frm) {
		render_work_order_details(frm);
		render_debit_summary(frm);
		if (frm.doc.docstatus === 0 && frm.doc.against === "Work Order" && !frm.doc.approved_by) {
			frm.add_custom_button(__("Approve Invoice"), () => approve_invoice(frm));
		}
	},

	supplier(frm) {
		frm.set_value("grn", []);
		frm.set_value("billing_supplier", frm.doc.supplier);
	},

	fetch_grn(frm) {
		if (!frm.doc.grn.length) frappe.throw(__("Please set at least one GRN"));
		const grns = Array.from(new Set(frm.doc.grn.map((row) => row.grn).filter(Boolean)));
		frappe.call({
			method: "yrp.yrp.doctype.purchase_invoice.purchase_invoice.fetch_grn_details",
			args: {
				grns,
				against: frm.doc.against,
				supplier: frm.doc.supplier,
			},
			callback(r) {
				if (!r.message) return;
				frm.set_value("items", r.message.items || []);
				frm.set_value("grn_grand_total", r.message.total || 0);
				frm.set_value("total_quantity", r.message.total_quantity || 0);
				frm.set_value("pi_work_order_billed_details", r.message.wo_items || []);
				frm.set_value("allow_to_change_rate", r.message.allow_to_change_rate || 0);
			},
		});
	},
});

function approve_invoice(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Approve Invoice"),
		fields: [{ fieldtype: "Small Text", fieldname: "comments", label: __("Comments") }],
		primary_action_label: __("Approve"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "yrp.yrp.doctype.purchase_invoice.purchase_invoice.approve_invoice",
				args: { name: frm.doc.name, comments: values.comments },
				freeze: true,
				callback() {
					frm.reload_doc();
				},
			});
		},
	});
	d.show();
}

function render_work_order_details(frm) {
	const wrapper = frm.fields_dict.work_order_details_html && frm.fields_dict.work_order_details_html.wrapper;
	if (!wrapper) return;
	$(wrapper).empty();
	const details = frm.doc.__onload && frm.doc.__onload.item_details;
	if (!details || !details.length) return;

	frappe.call({
		method: "yrp.yrp.doctype.purchase_invoice.purchase_invoice.get_merch_roles",
		callback(role_res) {
			frappe.call({
				method: "yrp.yrp.doctype.work_order.work_order.get_close_permission",
				callback(permission_res) {
					frappe.call({
						method: "yrp.yrp.doctype.purchase_invoice.purchase_invoice.check_all_wo_closed",
						args: { purchase_invoice: frm.doc.name },
						callback(status_res) {
							const close_permission = permission_res.message || {};
							const status = status_res.message || {};
							let html = `<div class="yrp-pi-wo-details"><h4>${__("Work Order Details")}</h4>`;
							for (const item of details) {
								html += `<div style="margin-bottom: 12px;">
									<h5>${frappe.utils.escape_html(item.work_order || "")}</h5>
									<table class="table table-sm table-bordered">
										<thead><tr>
											<th>${__("Item Variant")}</th>
											<th>${__("Delivered")}</th>
											<th>${__("Received")}</th>
											<th>${__("Billed")}</th>
											<th>${__("Invoice Qty")}</th>
										</tr></thead><tbody>`;
								for (const row of item.rows || []) {
									html += `<tr>
										<td>${frappe.utils.escape_html(row.item_variant || "")}</td>
										<td>${flt(row.total_delivered)}</td>
										<td>${flt(row.total_received)}</td>
										<td>${flt(row.billed)}</td>
										<td>${flt(row.quantity)}</td>
									</tr>`;
								}
								html += `</tbody></table>`;
								if (item.bills && item.bills.length) {
									html += `<div class="text-muted">${__("Previous Invoices")}: ${item.bills.map((b) => frappe.utils.escape_html(b.pi_name)).join(", ")}</div>`;
								}
								html += `</div>`;
							}
							if (frm.doc.docstatus === 0 && !frm.doc.approved_by && frm.doc.against === "Work Order") {
								const open_wos = status.open_work_orders || [];
								const close_request_wos = status.close_request_wos || [];
								const needs_close = open_wos.length || close_request_wos.length;
								if (needs_close) {
									html += `<div class="text-warning" style="margin: 8px 0;">${__("Work Orders must be closed before manager approval.")}</div>`;
									if (!close_permission.approver_role) {
										html += `<div class="text-danger" style="margin: 8px 0;">${__("Configure Work Order Closing Approver Role in YRP Settings.")}</div>`;
									}
									for (const wo of open_wos) {
										if (close_permission.approver_role) {
											const label = close_permission.is_close_manager ? __("Close") : __("Request Close");
											html += `<button class="btn btn-xs btn-warning yrp-close-wo" data-wo="${frappe.utils.escape_html(wo)}">${label} ${frappe.utils.escape_html(wo)}</button> `;
										}
									}
									for (const wo of close_request_wos) {
										if (close_permission.is_close_manager) {
											html += `<button class="btn btn-xs btn-warning yrp-close-wo" data-wo="${frappe.utils.escape_html(wo)}">${__("Approve Close")} ${frappe.utils.escape_html(wo)}</button> `;
										} else if (close_permission.approver_role) {
											html += `<span class="text-muted" style="margin-right: 8px;">${__("Close requested")}: ${frappe.utils.escape_html(wo)}</span>`;
										}
									}
								}
							}
							html += `</div>`;
							$(wrapper).html(html);
							$(wrapper).find(".yrp-close-wo").on("click", function () {
								open_close_dialog(frm, $(this).data("wo"));
							});
						},
					});
				},
			});
		},
	});
}

function render_debit_summary(frm) {
	const wrapper = frm.fields_dict.debit_summary_html && frm.fields_dict.debit_summary_html.wrapper;
	if (!wrapper) return;
	$(wrapper).empty();
	const debits = frm.doc.__onload && frm.doc.__onload.debit_summary;
	if (!debits || !debits.length) return;
	let total = 0;
	let html = `<h4>${__("Debit Summary")}</h4><table class="table table-sm table-bordered">
		<thead><tr>
			<th>${__("Work Order")}</th><th>${__("Debit")}</th><th>${__("Debit No")}</th>
			<th>${__("Debit Value")}</th><th>${__("Status")}</th><th>${__("Reason")}</th>
		</tr></thead><tbody>`;
	for (const d of debits) {
		total += flt(d.debit_value);
		html += `<tr>
			<td>${frappe.utils.escape_html(d.work_order || "")}</td>
			<td><a href="/app/debit/${encodeURIComponent(d.name)}" target="_blank">${frappe.utils.escape_html(d.name)}</a></td>
			<td>${frappe.utils.escape_html(d.debit_no || "")}</td>
			<td>${format_currency(d.debit_value || 0)}</td>
			<td>${frappe.utils.escape_html(d.status || "")}</td>
			<td>${frappe.utils.escape_html(d.reason || "")}</td>
		</tr>`;
	}
	html += `<tr><th colspan="3" class="text-right">${__("Total")}</th><th>${format_currency(total)}</th><th></th><th></th></tr>`;
	html += `</tbody></table>`;
	$(wrapper).html(html);
}

function open_close_dialog(frm, work_order) {
	const d = new frappe.ui.Dialog({
		title: __("Close Work Order {0}", [work_order]),
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
			},
			{
				fieldtype: "Data",
				fieldname: "close_other_reason",
				label: __("Other Reason"),
				depends_on: "eval: doc.close_reason == 'Others'",
				mandatory_depends_on: "eval: doc.close_reason == 'Others'",
			},
			{ fieldtype: "Small Text", fieldname: "close_remarks", label: __("Close Remarks") },
		],
		primary_action_label: __("Close Work Order"),
		primary_action(values) {
			if (!values) return;
			const close_work_order = () => {
				frappe.call({
					method: "yrp.yrp.doctype.work_order.work_order.update_stock",
					args: {
						work_order,
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
					method: "yrp.yrp.doctype.debit.debit.create_debit",
					args: {
						work_order,
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
	render_close_debits(work_order, d);
}

function render_close_debits(work_order, dialog) {
	frappe.call({
		method: "yrp.yrp.doctype.work_order.work_order.get_debits",
		args: { work_order },
		callback(r) {
			const debits = r.message || [];
			if (!debits.length) return;
			let html = `<h4>${__("Debit List")}</h4><table class="table table-sm table-bordered">
				<thead><tr><th>${__("Name")}</th><th>${__("Debit No")}</th><th>${__("Value")}</th><th>${__("Status")}</th><th>${__("Action")}</th></tr></thead><tbody>`;
			for (const d of debits) {
				html += `<tr>
					<td>${frappe.utils.escape_html(d.name)}</td>
					<td>${frappe.utils.escape_html(d.debit_no || "")}</td>
					<td>${format_currency(d.debit_value || 0)}</td>
					<td>${frappe.utils.escape_html(d.status || "")}</td>
					<td>${d.status !== "Approved" ? `<button class="btn btn-xs btn-success yrp-approve-debit" data-name="${frappe.utils.escape_html(d.name)}">${__("Approve")}</button>` : ""}</td>
				</tr>`;
			}
			html += `</tbody></table>`;
			$(dialog.fields_dict.debit_list_html.wrapper).html(html);
			$(dialog.fields_dict.debit_list_html.wrapper).find(".yrp-approve-debit").on("click", function () {
				frappe.call({
					method: "yrp.yrp.doctype.debit.debit.approve_debit",
					args: { name: $(this).data("name") },
					callback() {
						render_close_debits(work_order, dialog);
					},
				});
			});
		},
	});
}
