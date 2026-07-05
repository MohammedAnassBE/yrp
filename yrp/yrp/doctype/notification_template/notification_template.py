# Copyright (c) 2023, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils.safe_exec import get_safe_globals
from frappe.core.doctype.sms_settings.sms_settings import send_sms

class NotificationTemplate(Document):
	def send(self, docname, event, recipients: list[str]):
		if not self.enabled:
			return
		if not recipients:
			return
		if self.event != event:
			return
		if self.channel == "Email":
			self.send_email(docname, recipients)
		elif self.channel == "SMS":
			self.send_sms(docname, recipients)
		elif self.channel == "WhatsApp":
			self.send_whatsapp(docname, recipients)

	def get_message(self, context=None, docname=None):
		return frappe.render_template(self.template, context or get_context(self.document_type, docname))

	def send_email(self, docname, recipients):
		from email.utils import formataddr
		from frappe.core.doctype.communication.email import _make as make_communication

		context = get_context(self.document_type, docname)
		subject = frappe.render_template(self.subject, context)
		message = frappe.render_template(self.template, context)
		sender = None
		if self.sender and self.sender_email:
			sender = formataddr((self.sender, self.sender_email))
		attachments = self.get_attachment(docname)
		frappe.sendmail(
			sender=sender,
			recipients=recipients,
			subject=subject,
			message=message,
			reference_doctype=self.document_type,
			reference_name=docname,
			attachments=attachments,
			print_letterhead=((attachments and attachments[0].get("print_letterhead")) or False),
		)

		make_communication(
				doctype=self.document_type,
				name=docname,
				content=message,
				subject=subject,
				sender=sender,
				recipients=recipients,
				communication_medium="Email",
				send_email=False,
				attachments=attachments,
				communication_type="Automated Message",
			)

	def send_sms(self, docname, recipients, message=None):
		if not message:
			context = get_context(self.document_type, docname)
			message = frappe.render_template(self.template, context)
		dynamic_params = []
		if self.parameters:
			dynamic_params = frappe.parse_json(self.parameters)
		send_sms(recipients, message, dynamic_params=dynamic_params)
		from frappe.core.doctype.communication.email import _make as make_communication
		make_communication(
				doctype=self.document_type,
				name=docname,
				content=message,
				subject="SMS",
				sender="",
				recipients=recipients,
				communication_medium="SMS",
				send_email=False,
				communication_type="Automated Message",
			)

	def send_whatsapp(self, docname, recipients):
		"""Dormant event path: resolve the per-doctype approved template from
		Hub Settings and deliver it via the hub. The Jinja `template` body is
		NOT the WhatsApp payload (Meta accepts only the approved template name
		+ ordered variables); it renders a human-readable preview only."""
		from yrp.whatsapp import deliver_whatsapp_template

		resolved = self._resolve_whatsapp_template()
		if not resolved:
			frappe.msgprint(
				_("No WhatsApp template configured for {0}").format(self.document_type)
			)
			return
		account_name, template_name, language_code = resolved

		context = get_context(self.document_type, docname)
		preview = frappe.render_template(self.template, context) if self.template else ""

		for recipient in recipients:
			deliver_whatsapp_template(
				account_name=account_name,
				to_number=recipient,
				template_name=template_name,
				language_code=language_code,
			)
			self._log_whatsapp_communication(docname, recipient, preview)

	def _resolve_whatsapp_template(self):
		"""(account_name, template_name, language_code) for this doctype from the
		Hub Settings routing table, or None when nothing is configured."""
		settings = frappe.get_cached_doc("YRP WhatsApp Hub Settings")
		config = settings.get_template_config(self.document_type)
		if not config:
			return None
		template_name = frappe.db.get_value(
			"YRP WhatsApp Template", config.whatsapp_template, "template_name"
		)
		language_code = config.language_code or "en"
		return settings.get_default_account_name(), template_name, language_code

	def _log_whatsapp_communication(self, docname, recipient, content):
		"""Best-effort timeline Communication (medium WhatsApp). Isolated in a
		savepoint + try/except so a Communication failure (e.g. the medium
		option missing) can NEVER roll back the just-sent state."""
		from frappe.core.doctype.communication.email import _make as make_communication

		savepoint = "wa_comm"
		frappe.db.savepoint(savepoint)
		try:
			make_communication(
				doctype=self.document_type,
				name=docname,
				content=content,
				subject="WhatsApp",
				sender="",
				recipients=[recipient],
				communication_medium="WhatsApp",
				send_email=False,
				communication_type="Automated Message",
			)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error("WhatsApp timeline Communication failed")

	def get_attachment(self, docname):
		if not self.attach_print:
			return None
		doc = frappe.get_doc(self.document_type, docname)
		print_settings = frappe.get_doc("Print Settings", "Print Settings")
		if (doc.docstatus == 0 and not print_settings.allow_print_for_draft) or (
			doc.docstatus == 2 and not print_settings.allow_print_for_cancelled
		):
			status = "Draft" if doc.docstatus == 0 else "Cancelled"
			frappe.throw(
				_(
					"""Not allowed to attach {0} document, please enable Allow Print For {0} in Print Settings"""
				).format(status),
				title=_("Error in Notification"),
			)
		else:
			return [
				{
					"print_format_attachment": 1,
					"doctype": doc.doctype,
					"name": doc.name,
					"print_format": self.print_format,
					"print_letterhead": print_settings.with_letterhead,
					"lang": frappe.db.get_value("Print Format", self.print_format, "default_print_language")
					if self.print_format
					else "en",
				}
			]

def get_context(doctype, docname):
	doc = frappe.get_doc(doctype, docname)
	return {
		"doc": doc,
		"nowdate": frappe.utils.nowdate,
		"frappe": frappe._dict(utils=get_safe_globals().get("frappe").get("utils")),
	}

def add_whatsapp_communication_medium():
	"""Append 'WhatsApp' to Communication.communication_medium options so the
	best-effort timeline Communication validates. Idempotent; wired to
	after_install + after_migrate. A property setter is allowed (it does not
	edit core)."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	field = frappe.get_meta("Communication").get_field("communication_medium")
	options = (field.options or "").split("\n")
	if "WhatsApp" in options:
		return
	options.append("WhatsApp")
	make_property_setter(
		"Communication",
		"communication_medium",
		"options",
		"\n".join(options),
		"Text",
		validate_fields_for_doctype=False,
	)
	frappe.clear_cache(doctype="Communication")

def send_sms(receiver_list, msg, dynamic_params):

	import json

	if isinstance(receiver_list, str):
		receiver_list = json.loads(receiver_list)
		if not isinstance(receiver_list, list):
			receiver_list = [receiver_list]

	receiver_list = validate_receiver_nos(receiver_list)

	arg = {
		"receiver_list": receiver_list,
		"message": frappe.safe_decode(msg).encode("utf-8"),
		"dynamic_params": dynamic_params,
		"success_msg": True
	}

	if frappe.db.get_single_value("SMS Settings", "sms_gateway_url"):
		send_via_gateway(arg)
	else:
		frappe.throw(_("Please Update SMS Settings"))

def send_via_gateway(arg):
	ss = frappe.get_doc("SMS Settings", "SMS Settings")
	headers = get_headers(ss)
	if arg.get("dynamic_params"):
		for d in arg.get("dynamic_params"):
			if d.header:
				headers[d.parameter] = d.value
	use_json = headers.get("Content-Type") == "application/json"

	message = frappe.safe_decode(arg.get("message"))
	args = {ss.message_parameter: message}
	for d in ss.get("parameters"):
		if not d.header:
			args[d.parameter] = d.value

	if arg.get("dynamic_params"):
		for d in arg.get("dynamic_params"):
			if not d.header:
				args[d.parameter] = d.value

	success_list = []
	for d in arg.get("receiver_list"):
		args[ss.receiver_parameter] = d
		response = send_request(ss.sms_gateway_url, args, headers, ss.use_post, use_json)
		status = response.status_code
		if 200 <= status < 300:
			success_list.append(d)

	if len(success_list) > 0:
		args.update(arg)
		if arg.get("success_msg"):
			frappe.msgprint(_("SMS sent to following numbers: {0}").format("\n" + "\n".join(success_list)))

def get_headers(sms_settings=None):
	if not sms_settings:
		sms_settings = frappe.get_doc("SMS Settings", "SMS Settings")

	headers = {"Accept": "text/plain, text/html, */*"}
	for d in sms_settings.get("parameters"):
		if d.header == 1:
			headers.update({d.parameter: d.value})

	return headers

def send_request(gateway_url, params, headers=None, use_post=False, use_json=False):
	import requests

	if not headers:
		headers = get_headers()
	kwargs = {"headers": headers}

	if use_json:
		kwargs["json"] = params
	elif use_post:
		kwargs["data"] = params
	else:
		kwargs["params"] = params
	if use_post:
		response = requests.post(gateway_url, **kwargs)
	else:
		response = requests.get(gateway_url, **kwargs)
	response.raise_for_status()
	return response

def validate_receiver_nos(receiver_list):
	validated_receiver_list = []
	for d in receiver_list:
		if not d:
			break
		for x in [" ", "-", "(", ")"]:
			d = d.replace(x, "")

		validated_receiver_list.append(d)

	if not validated_receiver_list:
		frappe.throw(_("Please enter valid mobile nos"))

	return validated_receiver_list
