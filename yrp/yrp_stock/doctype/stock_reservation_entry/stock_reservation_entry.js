frappe.ui.form.on("Stock Reservation Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		const reserved = flt(frm.doc.reserved_qty);
		const delivered = flt(frm.doc.delivered_qty);
		const closed = flt(frm.doc.closed_qty);
		const remaining = reserved - delivered - closed;
		if (frm.doc.status === "Closed" || frm.doc.status === "Delivered" || frm.doc.status === "Cancelled") {
			return;
		}
		if (remaining <= 0) return;

		frm.add_custom_button(__("Close at Delivered"), () => {
			frappe.confirm(
				__(
					"Close this reservation at the currently delivered qty ({0})? " +
					"The remaining {1} will be released — reserved_qty stays at {2} for audit, " +
					"closed_qty becomes {1}, status flips to 'Closed'.",
					[delivered, remaining, reserved],
				),
				() => {
					frm.call({
						doc: frm.doc,
						method: "close_at_delivered",
						freeze: true,
						freeze_message: __("Closing reservation..."),
					}).then(() => frm.reload_doc());
				},
			);
		});
	},
});
