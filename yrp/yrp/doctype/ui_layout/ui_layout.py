import frappe
from frappe import _
from frappe.model.document import Document

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME, validate_config


class UILayout(Document):
	def validate(self):
		# Hard errors raise inside validate_config and block the save;
		# soft issues come back as warning strings (spec §3.1).
		for warning in validate_config(self.config, layer="layout"):
			frappe.msgprint(warning, indicator="orange")

	def before_rename(self, old, new, merge=False):
		# Mirror of the on_trash protection: the engine resolves missing or
		# disabled layouts by falling back to DEFAULT_LAYOUT_NAME, so renaming
		# it would silently break that fallback (allow_rename stays 1 for all
		# other layouts).
		if old == DEFAULT_LAYOUT_NAME and not (frappe.flags.in_uninstall or frappe.flags.in_install):
			frappe.throw(
				_(
					"The {0} UI Layout is protected and cannot be renamed. "
					"Create a new layout instead."
				).format(frappe.bold(DEFAULT_LAYOUT_NAME))
			)

	def on_trash(self):
		linked = frappe.db.count("YRP UI Preference", {"layout": self.name})
		if linked:
			frappe.throw(
				_(
					"Cannot delete UI Layout {0}: {1} YRP UI Preference record(s) still link to it. "
					"Repoint those users to another layout first, or set Disabled to retire this layout."
				).format(frappe.bold(self.name), linked)
			)

		if self.name == DEFAULT_LAYOUT_NAME and not (
			frappe.flags.in_uninstall or frappe.flags.in_install
		):
			frappe.throw(
				_(
					"The {0} UI Layout is protected and cannot be deleted. "
					"Set Disabled to retire it instead."
				).format(frappe.bold(DEFAULT_LAYOUT_NAME))
			)
