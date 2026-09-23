"""Use Frappe's native Contact display payload on supported source forms."""

from frappe.contacts.address_and_contact import load_address_and_contact


def load_contacts(doc, method=None):
	"""Populate __onload for the standard Contact panel renderer."""
	load_address_and_contact(doc)
