import frappe


def get_job_worker_stock_variant(doc, item_variant):
	"""Return the variant that ``doc`` holds at its job worker.

	Apps register ``yrp_job_worker_stock_variant`` handlers, called as
	``handler(doc, item_variant)``, when stock changes stage on arrival at the
	job worker (for example garments held as Loose Piece by a packing unit).
	"""
	for path in frappe.get_hooks("yrp_job_worker_stock_variant") or []:
		item_variant = frappe.get_attr(path)(doc, item_variant)
	return item_variant
