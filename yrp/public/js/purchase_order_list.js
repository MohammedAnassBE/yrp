(() => {
	const settings = frappe.listview_settings["Purchase Order"] || {};
	const nativeGetIndicator = settings.get_indicator;
	const requiredFields = ["is_yrp_managed", "yrp_fulfillment_status"];
	settings.add_fields = [...new Set([...(settings.add_fields || []), ...requiredFields])];
	settings.has_indicator_for_draft = true;
	settings.has_indicator_for_cancelled = true;

	settings.get_indicator = function (doc) {
		if (!doc.is_yrp_managed) {
			if (doc.docstatus === 0) return [__("Draft"), "red", "docstatus,=,0"];
			if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
			return nativeGetIndicator?.call(this, doc);
		}

		const status = doc.yrp_fulfillment_status || doc.status;
		const colors = {
			Draft: "orange",
			Ordered: "blue",
			"Partially Received": "yellow",
			Received: "green",
			"Partially Cancelled": "grey",
			Cancelled: "darkgrey",
			Closed: "light-blue",
		};
		return [
			__(status),
			colors[status] || "blue",
			`is_yrp_managed,=,1|yrp_fulfillment_status,=,${status}`,
		];
	};

	frappe.listview_settings["Purchase Order"] = settings;
})();
