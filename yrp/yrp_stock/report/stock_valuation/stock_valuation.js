frappe.query_reports["Stock Valuation"] = {
	filters: [
		{
			fieldname: "to_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "item",
			label: __("Item Variant"),
			fieldtype: "Link",
			options: "Item Variant",
		},
		{
			fieldname: "parent_item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "remove_zero_balance_item",
			label: __("Remove Zero Balance Items"),
			fieldtype: "Check",
			default: 1,
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname == "stock_value" && data && data.stock_value > 0) {
			value = "<span style='font-weight:bold'>" + value + "</span>";
		}
		return value;
	},
};
