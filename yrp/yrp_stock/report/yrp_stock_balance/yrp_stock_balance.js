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
			fieldtype: "MultiSelectList",
			options: "Item",
			get_data: (txt) => frappe.call({
				method: "yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance.get_item_filter_options",
				args: { txt, variant: 1 },
			}).then((r) => r.message),
		},
		{
			fieldname: "parent_item",
			label: __("Item"),
			fieldtype: "MultiSelectList",
			options: "Item",
			get_data: (txt) => frappe.call({
				method: "yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance.get_item_filter_options",
				args: { txt, variant: 0 },
			}).then((r) => r.message),
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
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

// QueryReport waits for script-time AJAX before setup_filters and route filters.
frappe.call({
	method: "yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance.get_filter_dimensions",
	callback: ({ message: dimensions }) => {
		const filters = frappe.query_reports["YRP Stock Balance"].filters;
		const dimension_filters = (dimensions || []).map((dimension) => ({
			fieldname: dimension.fieldname,
			label: __(dimension.label),
			fieldtype: "MultiSelectList",
			options: dimension.dimension_doctype,
			get_data(txt) {
				return frappe.db.get_link_options(dimension.dimension_doctype, txt);
			},
		}));
		filters.splice(filters.findIndex((filter) => filter.fieldname === "supplier") + 1, 0, ...dimension_filters);
	},
});
