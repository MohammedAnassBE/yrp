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
				showDimensions: !!frm.doc.is_rework,
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
			add_close_button(frm);
			add_create_rework_button(frm);
		}
	},

	validate(frm) {
		sync_editor_payload(frm, "deliverableEditor", "deliverable_details");
		sync_editor_payload(frm, "receivableEditor", "receivable_details");
	},
});

function add_close_button(frm) {
	frappe.call({
		method: "yrp.yrp.doctype.work_order.work_order.get_close_permission",
		callback(r) {
			const permission = r.message || {};
			if (!permission.approver_role) {
				frm.dashboard.add_comment(__("Configure Work Order Closing Approver Role in YRP Settings."), "orange");
				return;
			}
			if (frm.doc.open_status === "Close Request" && !permission.is_close_manager) {
				frm.dashboard.add_comment(__("Close request is waiting for a user with role {0}.", [permission.approver_role]), "orange");
				return;
			}
			let label = permission.is_close_manager ? __("Close") : __("Request Close");
			if (frm.doc.open_status === "Close Request") {
				label = __("Approve Close");
			}
			frm.add_custom_button(label, () => open_close_dialog(frm));
		},
	});
}

function add_create_rework_button(frm) {
	if (frm.doc.is_rework) return;
	frm.add_custom_button(__("Create Rework"), () => {
		frappe.call({
			method: "yrp.yrp.doctype.work_order.work_order.get_rework_source_rows",
			args: { work_order: frm.doc.name },
			freeze: true,
			freeze_message: __("Loading rework sources..."),
			callback(r) {
				const sources = r.message || [];
				if (!sources.length) {
					frappe.msgprint(__("No rework-eligible stock (non-Accepted, non-Rejected) is available for this Work Order."));
					return;
				}
				open_create_rework_dialog(frm, sources);
			},
		});
	});
}

function open_create_rework_dialog(frm, sources) {
	const buckets = group_rework_sources(sources);
	const d = new frappe.ui.Dialog({
		title: __("Create Rework"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "Select",
				fieldname: "supplier_type",
				label: __("Supplier Type"),
				options: "Same Supplier\nDifferent Supplier",
				default: "Same Supplier",
				reqd: 1,
			},
			{
				fieldtype: "Link",
				fieldname: "supplier",
				label: __("Supplier"),
				options: "Supplier",
				depends_on: "eval: doc.supplier_type == 'Different Supplier'",
				mandatory_depends_on: "eval: doc.supplier_type == 'Different Supplier'",
			},
			{ fieldtype: "Column Break" },
			{ fieldtype: "Section Break", label: __("Rework Sources") },
			{ fieldtype: "HTML", fieldname: "pivot_html" },
		],
		primary_action_label: __("Create Work Order"),
		primary_action(values) {
			const payload = collect_rework_payload(d, buckets);
			if (!payload.length) {
				frappe.throw(__("Enter Rework Qty for at least one source row."));
			}
			frappe.call({
				method: "yrp.yrp.doctype.work_order.work_order.create_rework_work_order",
				args: {
					parent_wo: frm.doc.name,
					rows: JSON.stringify(payload),
					supplier_type: values.supplier_type,
					supplier: values.supplier || "",
				},
				freeze: true,
				freeze_message: __("Creating rework Work Order..."),
				callback(r) {
					if (r.message) {
						d.hide();
						frappe.set_route("Form", "Work Order", r.message);
					}
				},
			});
		},
	});
	d.show();
	render_rework_pivot(d, buckets);
}

function group_rework_sources(sources) {
	// Build map of bucket_key -> { meta, cells: { rt -> { primary_value -> [src_rows] } }, rts: Set, primary_values: Set }
	const map = new Map();
	for (const row of sources) {
		const np = row.non_primary_attrs || {};
		const np_key = Object.keys(np).sort().map((k) => `${k}=${np[k]}`).join("|");
		const bucket_key = [
			row.parent_item || "",
			row.lot || "",
			row.set_combination || "",
			np_key,
		].join("::");
		let bucket = map.get(bucket_key);
		if (!bucket) {
			bucket = {
				key: bucket_key,
				meta: {
					parent_item: row.parent_item,
					lot: row.lot,
					set_combination: row.set_combination,
					non_primary_attrs: np,
					primary_attribute: row.primary_attribute,
				},
				cells: {},
				rts: new Set(),
				primary_values: new Set(),
			};
			map.set(bucket_key, bucket);
		}
		const rt = row.received_type || "";
		const pv = row.primary_attribute_value || "";
		bucket.rts.add(rt);
		bucket.primary_values.add(pv);
		bucket.cells[rt] = bucket.cells[rt] || {};
		bucket.cells[rt][pv] = bucket.cells[rt][pv] || [];
		bucket.cells[rt][pv].push(row);
	}
	return Array.from(map.values()).sort((a, b) =>
		(a.meta.parent_item || "").localeCompare(b.meta.parent_item || "")
		|| a.key.localeCompare(b.key)
	);
}

function render_rework_pivot(dialog, buckets) {
	const $wrapper = dialog.get_field("pivot_html").$wrapper;
	$wrapper.empty();
	if (!buckets.length) {
		$wrapper.append(`<div class="text-muted">${__("No rework sources available.")}</div>`);
		return;
	}
	for (const bucket of buckets) {
		const np = bucket.meta.non_primary_attrs || {};
		const np_label = Object.keys(np).sort().map((k) => `${k}: ${np[k]}`).join(" / ");
		const header_bits = [bucket.meta.parent_item];
		if (np_label) header_bits.push(np_label);
		if (bucket.meta.lot) header_bits.push(`Lot ${bucket.meta.lot}`);
		const header = header_bits.filter(Boolean).join(" • ");
		const primary_values = Array.from(bucket.primary_values).sort(compare_primary_values);
		const rts = Array.from(bucket.rts).sort();
		const $block = $(`
			<div class="rework-pivot-block" style="margin-bottom: 1.25rem;">
				<div class="rework-pivot-header" style="font-weight: 600; margin-bottom: 0.35rem;">${frappe.utils.escape_html(header)}</div>
				<table class="table table-bordered table-sm" style="margin-bottom: 0;">
					<thead>
						<tr>
							<th style="width: 12rem;">${frappe.utils.escape_html(bucket.meta.primary_attribute || "Attribute")}</th>
							${primary_values.map((pv) => `<th class="text-center">${frappe.utils.escape_html(pv || "—")}</th>`).join("")}
						</tr>
					</thead>
					<tbody></tbody>
				</table>
			</div>
		`);
		const $tbody = $block.find("tbody");
		for (const rt of rts) {
			const $tr = $(`<tr><th>${frappe.utils.escape_html(rt)}</th></tr>`);
			for (const pv of primary_values) {
				const cell_rows = (bucket.cells[rt] || {})[pv] || [];
				const avail = cell_rows.reduce((s, r) => s + (r.available_qty || 0), 0);
				if (!cell_rows.length || avail <= 0) {
					$tr.append('<td class="text-center text-muted">—</td>');
					continue;
				}
				const $td = $(`
					<td class="text-center">
						<span class="text-muted rework-pivot-avail">${avail}</span>
						<input type="number" class="form-control input-sm rework-pivot-input" min="0" max="${avail}" step="1" value="0"
							data-bucket="${frappe.utils.escape_html(bucket.key)}"
							data-rt="${frappe.utils.escape_html(rt)}"
							data-pv="${frappe.utils.escape_html(pv)}"
							style="display:inline-block; width: 5rem; margin-left: 0.4rem;" />
					</td>
				`);
				$tr.append($td);
			}
			$tbody.append($tr);
		}
		$wrapper.append($block);
	}
}

function collect_rework_payload(dialog, buckets) {
	const bucket_by_key = new Map(buckets.map((b) => [b.key, b]));
	const payload = [];
	dialog.get_field("pivot_html").$wrapper.find("input.rework-pivot-input").each(function () {
		const qty = flt($(this).val());
		if (qty <= 0) return;
		const bucket = bucket_by_key.get($(this).data("bucket"));
		if (!bucket) return;
		const rt = $(this).data("rt");
		const pv = $(this).data("pv");
		const cell_rows = (bucket.cells[rt] || {})[pv] || [];
		let remaining = qty;
		for (const src of cell_rows) {
			if (remaining <= 0) break;
			const take = Math.min(remaining, src.available_qty);
			if (take > 0) {
				payload.push({ source_key: src.source_key, qty: take });
				remaining -= take;
			}
		}
		if (remaining > 0.0001) {
			frappe.throw(__("Requested qty {0} exceeds available {1} for {2} / {3}.", [
				qty, qty - remaining, rt, pv,
			]));
		}
	});
	return payload;
}

const SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"];
function compare_primary_values(a, b) {
	const ai = SIZE_ORDER.indexOf((a || "").toUpperCase());
	const bi = SIZE_ORDER.indexOf((b || "").toUpperCase());
	if (ai !== -1 && bi !== -1) return ai - bi;
	if (ai !== -1) return -1;
	if (bi !== -1) return 1;
	return (a || "").localeCompare(b || "");
}

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
					method: "yrp.yrp.doctype.debit.debit.create_debit",
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
					method: "yrp.yrp.doctype.debit.debit.approve_debit",
					args: { name: $(this).data("name") },
					callback() {
						render_debit_list(work_order, dialog);
					},
				});
			});
		},
	});
}
