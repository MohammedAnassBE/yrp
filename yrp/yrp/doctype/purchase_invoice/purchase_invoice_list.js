frappe.listview_settings["Purchase Invoice"] = {
	add_fields: ["status"],
	has_indicator_for_draft: 1,
	get_indicator(doc) {
		const status_color = {
			"Draft": "yellow",
			"Approval Initiated": "red",
			"Approval Pending": "orange",
			"Approved": "green",
			"Submitted": "blue",
			"Cancelled": "black",
		};
		const status = doc.status || "Draft";
		return [__(status), status_color[status] || "gray", "status,=," + status];
	},
};
