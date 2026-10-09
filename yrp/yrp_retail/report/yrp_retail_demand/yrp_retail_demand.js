frappe.query_reports["YRP Retail Demand"] = {
  "filters": [
    {
      "fieldname": "from_date",
      "label": "From Date",
      "fieldtype": "Date",
      "reqd": 1,
      "default": frappe.datetime.month_start()
    },
    {
      "fieldname": "to_date",
      "label": "To Date",
      "fieldtype": "Date",
      "reqd": 1,
      "default": frappe.datetime.get_today()
    },
    {
      "fieldname": "customer",
      "label": "Customer",
      "fieldtype": "Link",
      "options": "Customer"
    },
    {
      "fieldname": "item_code",
      "label": "Item",
      "fieldtype": "Link",
      "options": "Item"
    },
    {
      "fieldname": "uom",
      "label": "UOM",
      "fieldtype": "Link",
      "options": "UOM"
    },
    {
      "fieldname": "sales_person",
      "label": "Sales Person",
      "fieldtype": "Link",
      "options": "Sales Person"
    }
  ]
};
