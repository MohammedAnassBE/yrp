# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

from frappe.contacts.address_and_contact import delete_contact_and_address, load_address_and_contact
from frappe.model.document import Document


class Customer(Document):
	def onload(self):
		load_address_and_contact(self)

	def on_trash(self):
		delete_contact_and_address("Customer", self.name)
