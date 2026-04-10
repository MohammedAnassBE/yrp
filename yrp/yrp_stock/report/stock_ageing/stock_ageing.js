frappe.query_reports["Stock Ageing"] = {
	filters: [
		{
			fieldname: "to_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
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
			fieldname: "range1",
			label: __("Ageing Range 1"),
			fieldtype: "Int",
			default: 30,
			reqd: 1,
		},
		{
			fieldname: "range2",
			label: __("Ageing Range 2"),
			fieldtype: "Int",
			default: 60,
			reqd: 1,
		},
		{
			fieldname: "range3",
			label: __("Ageing Range 3"),
			fieldtype: "Int",
			default: 90,
			reqd: 1,
		},
		{
			fieldname: "show_warehouse_wise_stock",
			label: __("Show Warehouse Wise Stock"),
			fieldtype: "Check",
			default: 1,
		},
	],
};
