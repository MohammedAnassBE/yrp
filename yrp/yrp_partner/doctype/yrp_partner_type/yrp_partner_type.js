// Copyright (c) 2026, Mohammed Anas and contributors
// For license information, please see license.txt

// Bulk generation status is scoped by the server to the configured Partner Type.
function show_partner_backfill(frm, status) {
	if (!status || status.partner_type !== frm.doc.name) return;
	if (status.revision !== frm.doc.modified) return;
	const messages = {
		Queued: __("Partner generation queued. Keep the background worker running."),
		Running: __("Generating partners: {0} of approximately {1} records processed.", [status.processed, status.total]),
		Completed: __("Partner generation completed: {0} records processed.", [status.processed]),
		Failed: __("Partner generation failed. Check the Error Log, correct the issue and use Restart Partner Generation."),
	};
	frm.dashboard.set_headline_alert(messages[status.state] || "", status.state === "Failed" ? "red" : "blue");
}

frappe.ui.form.on("YRP Partner Type", {
	setup(frm) {
		frappe.realtime.on("yrp_partner_backfill", (status) => show_partner_backfill(frm, status));
	},
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.perm?.[0]?.write) {
			frm.add_custom_button(__("Restart Partner Generation"), () => {
				frappe.call({
					method: "yrp.yrp_partner.backfill.restart_backfill",
					args: { partner_type: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}
		frappe.call({
			method: "yrp.yrp_partner.backfill.get_backfill_status",
			args: { partner_type: frm.doc.name },
			callback: (r) => show_partner_backfill(frm, r.message),
		});
	},
});
