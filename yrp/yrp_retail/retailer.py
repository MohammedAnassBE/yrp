"""Customer-owned retailer assignments; no independent retailer partner choice."""
import frappe


def sync_customer_partner(doc, method=None):
    """Refresh the derived display link in the Customer save transaction.

    Permissions use the live Customer link, so a stale imported copy cannot grant
    access. Updating this derived field does not rewrite visits or order history.
    """
    if not frappe.get_meta('YRP Retailer').has_field('sales_partner'):
        return
    if not doc.is_new() and not doc.has_value_changed('default_sales_partner'):
        return
    for name in frappe.get_all('YRP Retailer', filters={'customer': doc.name}, pluck='name'):
        frappe.db.set_value('YRP Retailer', name, 'sales_partner', doc.default_sales_partner or None)
        frappe.clear_document_cache('YRP Retailer', name)


def backfill_sales_partners():
    """Populate derived links on upgrade; do not guess missing Customer records."""
    frappe.db.sql('''UPDATE `tabYRP Retailer` r
        LEFT JOIN `tabCustomer` c ON c.name=r.customer
        SET r.sales_partner=NULLIF(c.default_sales_partner, '')''')
