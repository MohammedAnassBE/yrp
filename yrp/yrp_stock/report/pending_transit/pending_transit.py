# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

"""Pending Transit — Send-to-Warehouse Stock Entries that are still un-received
beyond the configured aging threshold (D-011, Gap #24)."""

import frappe
from frappe import _
from frappe.utils import cint, getdate, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	threshold = cint(
		filters.get("transit_aging_threshold_days")
		or frappe.db.get_single_value("YRP Stock Settings", "transit_aging_threshold_days")
		or 7
	)
	cutoff = filters.get("as_of_date") or today()

	rows = frappe.db.sql(
		"""
		SELECT se.name           AS stock_entry,
		       se.posting_date,
		       se.from_warehouse,
		       se.to_warehouse   AS transit_warehouse,
		       sed.item          AS item_code,
		       sed.qty           AS sent_qty,
		       COALESCE(sed.transferred_qty, 0) AS received_qty,
		       se.per_transferred,
		       DATEDIFF(%(cutoff)s, se.posting_date) AS days_in_transit
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.purpose = 'Send to Warehouse'
		  AND se.docstatus = 1
		  AND COALESCE(se.skip_transit, 0) = 0
		  AND COALESCE(se.per_transferred, 0) < 100
		  AND DATEDIFF(%(cutoff)s, se.posting_date) >= %(threshold)s
		ORDER BY days_in_transit DESC, se.posting_date ASC
		""",
		{"cutoff": cutoff, "threshold": threshold},
		as_dict=True,
	)
	return _columns(), rows


def _columns():
	return [
		{"label": _("Stock Entry"), "fieldname": "stock_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 160},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("From"), "fieldname": "from_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Transit"), "fieldname": "transit_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item Variant", "width": 160},
		{"label": _("Sent Qty"), "fieldname": "sent_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Received Qty"), "fieldname": "received_qty", "fieldtype": "Float", "width": 100},
		{"label": _("% Transferred"), "fieldname": "per_transferred", "fieldtype": "Percent", "width": 110},
		{"label": _("Days in Transit"), "fieldname": "days_in_transit", "fieldtype": "Int", "width": 110},
	]
