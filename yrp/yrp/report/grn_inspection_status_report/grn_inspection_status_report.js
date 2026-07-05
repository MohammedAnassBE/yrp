frappe.query_reports["GRN Inspection Status Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "against",
			label: __("Against"),
			fieldtype: "Select",
			options: "\nPurchase Order\nWork Order",
		},
		{
			fieldname: "against_id",
			label: __("Against ID"),
			fieldtype: "Dynamic Link",
			options: "against",
			depends_on: "eval:doc.against",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "process_name",
			label: __("Process"),
			fieldtype: "Link",
			options: "Process",
		},
		{
			fieldname: "item_variant",
			label: __("Item Variant"),
			fieldtype: "Link",
			options: "Item Variant",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "source_received_type",
			label: __("Source Received Type"),
			fieldtype: "Link",
			options: "Received Type",
		},
		{
			fieldname: "target_received_type",
			label: __("Target Received Type"),
			fieldtype: "Link",
			options: "Received Type",
		},
		{
			fieldname: "inspection_status",
			label: __("Inspection Status"),
			fieldtype: "Select",
			options: "\nNot Inspected\nPartially Inspected\nInspected",
		},
		{
			fieldname: "conversion_status",
			label: __("Conversion Status"),
			fieldtype: "Select",
			options: "\nNo Inspection\nNot Converted\nPartially Converted\nConverted",
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "pending_inspection_qty" && data.pending_inspection_qty > 0) {
			return "<span style='color: var(--text-warning); font-weight: 600'>" + value + "</span>";
		}
		if (column.fieldname === "inspection_status" && data.inspection_status === "Not Inspected") {
			return "<span style='color: var(--text-danger); font-weight: 600'>" + value + "</span>";
		}
		if (column.fieldname === "conversion_status" && data.conversion_status === "Not Converted") {
			return "<span style='color: var(--text-warning); font-weight: 600'>" + value + "</span>";
		}
		return value;
	},
};
