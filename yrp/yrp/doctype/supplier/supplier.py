# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.contacts.address_and_contact import load_address_and_contact, delete_contact_and_address
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.contacts.doctype.contact.contact import get_default_contact
from jinja2 import TemplateSyntaxError

class Supplier(Document):

	def onload(self):
		"""Load address and contacts in `__onload`"""
		load_address_and_contact(self)

	def on_trash(self):
		delete_contact_and_address('Supplier', self.name)

	def get_primary_contact_details(self) -> dict:
		"""Primary email + mobile from this supplier's default Contact.
		Throws when no default contact is set."""
		contact_name = get_default_contact(self.doctype, self.name)
		if not contact_name:
			frappe.throw(_("Please set a default contact for supplier {0}").format(self.name))
		contact = frappe.get_doc("Contact", contact_name)

		primary_email = None
		if contact.email_ids:
			primary_emails = [email.email_id for email in contact.email_ids if email.is_primary == 1]
			if primary_emails:
				primary_email = primary_emails[0]

		primary_mobile = None
		if contact.phone_nos:
			primary_mobiles = [phone.phone for phone in contact.phone_nos if phone.is_primary_mobile_no == 1]
			if primary_mobiles:
				primary_mobile = primary_mobiles[0]

		return {"contact": contact_name, "email": primary_email, "mobile": primary_mobile}

	def send_notification(self, doctype: str, docname: str, channels: list[str], event: str):
		details = self.get_primary_contact_details()
		recipient_by_channel = {"Email": details["email"], "SMS": details["mobile"], "WhatsApp": details["mobile"]}

		templates = frappe.get_all(
			"Notification Template",
			filters={
				"document_type": doctype,
				"channel": ["in", channels],
				"event": event,
				"enabled": 1,
			},
			pluck="name",
		)
		if not templates:
			frappe.msgprint(
				_("No enabled Notification Template for {0} / {1} / {2}").format(
					doctype, event, ", ".join(channels)
				)
			)
			return

		sent_channels = []
		skipped_channels = []
		for template_name in templates:
			template = frappe.get_doc("Notification Template", template_name)
			recipient = recipient_by_channel.get(template.channel)
			if not recipient:
				skipped_channels.append(template.channel)
				continue
			template.send(docname, event, [recipient])
			sent_channels.append(template.channel)

		# A channel without a recipient detail is only an error when nothing
		# at all could be sent; otherwise warn and deliver the rest.
		if skipped_channels and not sent_channels:
			frappe.throw(
				_("Contact {0} has no recipient detail for channel(s): {1}").format(
					details["contact"], ", ".join(sorted(set(skipped_channels)))
				)
			)
		elif skipped_channels:
			frappe.msgprint(
				_("Skipped channel(s) without recipient detail: {0}").format(
					", ".join(sorted(set(skipped_channels)))
				)
			)

def make_gstin_custom_field():
	custom_fields = {
		'Address': [
			dict(fieldname='gstin', label='GSTIN', fieldtype='Data',
				insert_after='fax'),
		]
	}
	create_custom_fields(custom_fields)
	make_property_setter('Address', 'county', 'hidden', 1, 'Check')

@frappe.whitelist()
def get_primary_address(supplier):
	filters = [
		["Dynamic Link", "link_doctype", "=", "Supplier"],
		["Dynamic Link", "link_name", "=", supplier],
		["Dynamic Link", "parenttype", "=", "Address"],
		["Address", "disabled", "=", "0"],
		["Address", "is_primary_address", "=", 1]
	]

	address = frappe.get_list("Address", filters=filters, pluck="name") or {}

	if address:
		return address[0]

@frappe.whitelist()
def get_address(supplier, type):
	filters = [
		["Dynamic Link", "link_doctype", "=", "Supplier"],
		["Dynamic Link", "link_name", "=", supplier],
		["Dynamic Link", "parenttype", "=", "Address"],
		["Address", "disabled", "=", "0"],
		["Address", "address_type", "=", type]
	]

	address = frappe.get_list("Address", filters=filters, pluck="name") or {}

	if address:
		return address[0]

@frappe.whitelist()
def get_supplier_address_display(supplier):
	address_dict = get_primary_address(supplier)
	if not address_dict:
		return

	if not isinstance(address_dict, dict):
		address_dict = frappe.db.get_value("Address", address_dict, "*", as_dict=True, cache=True) or {}

	template = '''
		{{ address_line1 }}, {% if address_line2 %}{{ address_line2 }}{% endif -%}<br>
		{{ city }}, {% if state %}{{ state }}{% endif -%}{% if pincode %} - {{ pincode }}{% endif -%}
	'''

	try:
		return frappe.render_template(template, address_dict)
	except TemplateSyntaxError:
		frappe.throw(_("There is an error in your Address Template"))


def update_supplier_department_on_bill_tracking(supplier, dept):
	"""Set Supplier.department if currently empty. Called from Bill Tracking
	assignment so a supplier's bills route to a consistent department over time."""
	existing = frappe.db.get_value("Supplier", supplier, "department")
	if existing is None and not frappe.db.exists("Supplier", supplier):
		frappe.throw(f"Can't find supplier -> {supplier}")
	if not existing:
		frappe.db.set_value("Supplier", supplier, "department", dept)
