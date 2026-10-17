"""Optional application stock owner. Without a provider, YRP keeps its native stock lifecycle."""

import frappe


def get_provider(note, **context):
	for path in frappe.get_hooks("yrp_stock_provider"):
		provider = frappe.get_attr(path)(note, **context)
		if provider is not None:
			return provider
	return None


def handle(note, operation, *args):
	context = {"invoice": args[0]} if operation in ("invoice_submit", "invoice_reverse") else {}
	provider = get_provider(note, **context)
	if provider is None:
		return False
	getattr(provider, operation)(note, *args)
	return True
