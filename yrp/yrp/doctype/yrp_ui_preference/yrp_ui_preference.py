import frappe
from frappe import _
from frappe.model.document import Document

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME, validate_config


class YRPUIPreference(Document):
	def validate(self):
		# Same hard errors as UI Layout, plus the overrides-layer whitelist
		# warning (top-level keys outside OVERRIDABLE_KEYS) — spec §3.2.
		for warning in validate_config(self.overrides, layer="overrides"):
			frappe.msgprint(warning, indicator="orange")

		if self.layout:
			disabled = frappe.db.get_value("UI Layout", self.layout, "disabled")
			if disabled is None:
				frappe.throw(_("UI Layout {0} does not exist").format(frappe.bold(self.layout)))
			elif disabled:
				frappe.msgprint(
					_(
						"UI Layout {0} is disabled — this user will fall back to the {1} layout "
						"until it is re-enabled or the preference is repointed."
					).format(frappe.bold(self.layout), frappe.bold(DEFAULT_LAYOUT_NAME)),
					indicator="orange",
				)
