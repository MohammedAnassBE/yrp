frappe.ui.form.on("SMS Notification Log", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Resend"), () => {
			frappe.confirm(__("Resend this SMS to {0}?", [frappe.utils.escape_html(frm.doc.mobile_no)]), () => {
				frappe.call({
					method: "yrp.notification.resend_sms_notification_log",
					args: { log_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			});
		});
	},
});
