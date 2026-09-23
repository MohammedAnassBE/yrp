"""Add the authoritative Customer-derived Sales Partner display link."""
import frappe


def execute():
    frappe.reload_doc('yrp_retail', 'doctype', 'yrp_retailer')
    from yrp.yrp_retail.retailer import backfill_sales_partners
    backfill_sales_partners()
