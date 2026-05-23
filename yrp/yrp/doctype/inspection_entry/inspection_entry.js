frappe.provide("frappe.yrp.inspection");

frappe.ui.form.on("Inspection Entry", {
	setup(frm) {
		frm.set_query("against_id", () => {
			const base = { docstatus: 1 };
			if (frm.doc.against === "Goods Received Note") {
				base.is_rework = 0;
			}
			if (frm.doc.against === "Stock Entry") {
				base.purpose = "Material Receipt";
			}
			return { filters: base };
		});
	},

	refresh(frm) {
		// Tear down any previous editor before re-mounting.
		if (frm.itemEditor) {
			frm.itemEditor.app.unmount();
			frm.itemEditor = null;
		}
		const $wrap = $(frm.fields_dict["item_html"].wrapper);
		$wrap.html("");

		frm.itemEditor = new frappe.yrp.inspection.InspectionEditor($wrap.get(0), {
			docstatus: frm.doc.docstatus,
		});

		const onload = frm.doc.__onload && frm.doc.__onload.item_details;
		if (onload && onload.length) {
			frm.doc.item_details = JSON.stringify(onload);
			frm.itemEditor.load_data(onload);
		} else if (frm.doc.item_details) {
			try {
				frm.itemEditor.load_data(JSON.parse(frm.doc.item_details));
			} catch (_) {
				frm.itemEditor.load_data([]);
			}
		} else {
			frm.itemEditor.load_data([]);
		}
		frm.itemEditor.update_status();

		_maybe_autoload_initial_payload(frm);
		_maybe_add_convert_stock_button(frm);
	},

	against(frm) {
		if (frm.doc.docstatus !== 0) return;
		frm.set_value("against_id", "");
		frm.doc.item_details = "";
		if (frm.itemEditor) frm.itemEditor.load_data([]);
	},

	against_id(frm) {
		if (!frm.doc.against || !frm.doc.against_id || frm.doc.docstatus !== 0) return;
		frappe.call({
			method: "yrp.yrp.doctype.inspection_entry.inspection_entry.get_initial_payload",
			args: { against: frm.doc.against, against_id: frm.doc.against_id },
			freeze: true,
			freeze_message: __("Loading source items..."),
			callback(r) {
				if (!r.message) return;
				frm.doc.item_details = JSON.stringify(r.message);
				if (frm.itemEditor) frm.itemEditor.load_data(r.message);
				frm.dirty();
			},
		});
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

function _maybe_autoload_initial_payload(frm) {
	// When a fresh IE is opened with against/against_id pre-filled (e.g. routed
	// from the GRN or Stock Entry "Create Inspection Entry" button), the
	// against_id `change` trigger doesn't fire on initial render. Auto-load
	// here so the top table is populated as soon as the form loads.
	if (!frm.doc.__islocal) return;
	if (!frm.doc.against || !frm.doc.against_id) return;
	if (frm.doc.item_details) return;
	frappe.call({
		method: "yrp.yrp.doctype.inspection_entry.inspection_entry.get_initial_payload",
		args: { against: frm.doc.against, against_id: frm.doc.against_id },
		freeze: true,
		freeze_message: __("Loading source items..."),
		callback(r) {
			if (!r.message) return;
			frm.doc.item_details = JSON.stringify(r.message);
			if (frm.itemEditor) frm.itemEditor.load_data(r.message);
			frm.dirty();
		},
	});
}

function _maybe_add_convert_stock_button(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.is_converted || frm.doc.status === "Cancelled") return;

	frappe.call({
		method: "yrp.yrp.doctype.inspection_entry.inspection_entry.can_convert_stock",
		args: { name: frm.doc.name },
		callback(r) {
			const data = r && r.message;
			if (!data || !data.can_convert) return;
			frm.add_custom_button(__("Convert Stock"), function () {
				_open_convert_stock_dialog(frm, data.siblings || []);
			}).addClass("btn-primary");
		},
	});
}

function _open_convert_stock_dialog(frm, siblings) {
	// Server already filters siblings down to converted IEs only.
	const escape = (s) => frappe.utils.escape_html(String(s == null ? "" : s));

	let html = "";
	html += `<p>${__("Are you sure want to convert the stock?")}</p>`;
	html += `<p style="margin-top:8px"><b>${escape(frm.doc.against)}:</b> ${escape(frm.doc.against_id)}</p>`;

	if (siblings.length) {
		html += `<div style="margin-top:12px"><b style="color:#16a34a">${__("Stock already converted by these Inspection Entries:")}</b></div>`;
		html += `<table class="table table-bordered table-sm" style="margin-top:6px;font-size:12px">`;
		html += `<thead><tr><th>${__("Inspection Entry")}</th><th>${__("Posting Date")}</th></tr></thead><tbody>`;
		for (const s of siblings) {
			const link = `<a href="/app/inspection-entry/${encodeURIComponent(s.name)}" target="_blank">${escape(s.name)}</a>`;
			html += `<tr><td>${link}</td><td>${escape(s.posting_date)}</td></tr>`;
		}
		html += `</tbody></table>`;
	}

	const d = new frappe.ui.Dialog({
		title: __("Convert Stock"),
		fields: [{ fieldtype: "HTML", fieldname: "info_html" }],
		primary_action_label: __("Convert"),
		primary_action() {
			d.hide();
			frappe.call({
				method: "yrp.yrp.doctype.inspection_entry.inspection_entry.convert_stock",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Converting stock…"),
				callback(rc) {
					if (rc && rc.message && rc.message.status === "Converted") {
						frappe.show_alert({
							message: __("Stock converted."),
							indicator: "green",
						});
						frm.reload_doc();
					}
				},
			});
		},
	});
	d.fields_dict.info_html.$wrapper.html(html);
	d.show();
}
