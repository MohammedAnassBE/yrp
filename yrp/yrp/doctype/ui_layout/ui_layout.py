import frappe
from frappe import _
from frappe.model.document import Document

from yrp.yrp.api.ui_config import (
	DEFAULT_LAYOUT_NAME,
	validate_config,
	validate_layout_rendering,
)


class UILayout(Document):
	def after_insert(self):
		self._ensure_terminology()

	def validate(self):
		# Hard errors raise inside validate_config and block the save;
		# soft issues come back as warning strings (spec §3.1).
		for warning in validate_config(self.config, layer="layout"):
			frappe.msgprint(warning, indicator="orange")

		validate_layout_rendering(self.render_mode, self.experience_key, self.config)

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

		# App code can be newer than an individual site's schema during a rolling
		# deploy. Keep the existing UI Layout lifecycle usable until that site is
		# migrated and the terminology tables exist.
		if frappe.db.table_exists("YRP UI Terminology"):
			terminology = frappe.db.get_value("YRP UI Terminology", {"ui_layout": self.name}, "name")
			if terminology:
				frappe.delete_doc("YRP UI Terminology", terminology, ignore_permissions=True, force=True)

	def _ensure_terminology(self):
		if not frappe.db.exists("DocType", "YRP UI Terminology"):
			return
		if frappe.db.exists("YRP UI Terminology", {"ui_layout": self.name}):
			return
		frappe.get_doc(
			{
				"doctype": "YRP UI Terminology",
				"ui_layout": self.name,
			}
		).insert(ignore_permissions=True)
