// Copyright (c) 2026, Essdee and contributors
// For license information, please see license.txt

frappe.ui.form.on("WhatsApp Notification Log", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Resend"), () => {
			frappe.confirm(
				__("Resend this WhatsApp message to {0}?", [frappe.utils.escape_html(frm.doc.mobile_no)]),
				() => {
					frappe.call({
						method: "yrp.whatsapp_notification.resend_whatsapp_notification_log",
						args: { log_name: frm.doc.name },
						callback: () => frm.reload_doc(),
					});
				},
			);
		});
	},
});
