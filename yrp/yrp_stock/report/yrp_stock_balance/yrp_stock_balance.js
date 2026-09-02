frappe.query_reports["YRP Stock Balance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "item",
			label: __("Item Variant"),
			fieldtype: "Link",
			options: "YRP Item Variant",
		},
		{
			fieldname: "parent_item",
			label: __("Item"),
			fieldtype: "Link",
			options: "YRP Item",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "YRP Warehouse",
		},
		{
			fieldname: "show_stock_ageing_data",
			label: __("Show Stock Ageing Data"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_variant_attributes",
			label: __("Show Variant Attributes"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "remove_zero_balance_item",
			label: __("Remove Zero Balance Items"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "show_inward_date_split",
			label: __("Show Inward Date Split"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname == "out_qty" && data && data.out_qty > 0) {
			value = "<span style='color:red'>" + value + "</span>";
		} else if (column.fieldname == "in_qty" && data && data.in_qty > 0) {
			value = "<span style='color:green'>" + value + "</span>";
		} else if (column.fieldname == "inward_split" && data && data.inward_split) {
			const escaped = frappe.utils.escape_html(String(data.inward_split));
			value = `<span title="${escaped}">${escaped.replace(/\n/g, "<br>")}</span>`;
		}
		return value;
	},
};
