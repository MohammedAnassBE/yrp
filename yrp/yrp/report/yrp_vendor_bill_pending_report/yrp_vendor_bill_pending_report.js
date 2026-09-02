frappe.query_reports["YRP Vendor Bill Pending Report"] = {
	filters: [
		{ fieldname: "department", fieldtype: "Link", label: "Department", options: "YRP Department" },
		{ fieldname: "bill_start_date", fieldtype: "Date", label: "Start Date" },
		{ fieldname: "bill_end_date", fieldtype: "Date", label: "End Date" },
	],
};
