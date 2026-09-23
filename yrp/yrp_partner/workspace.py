"""Build Partner navigation from configuration without persisting user-specific links.

The Workspace and sidebar retain native DocType links, so Frappe still applies
role, module and domain visibility. Source DocTypes are discovered at read time;
editing Partner Types does not rewrite/export standard workspace JSON files.
"""

import frappe
from frappe.desk.desk_views import DeskViews


WORKSPACE = "YRP Partner Management"
SIDEBAR = "YRP Partner"
MANAGEMENT_DOCTYPES = ("YRP Partner Type", "YRP Partner")


def get_partner_links():
	"""Return unique readable lists, including each configured source DocType.

	Configuration controls which lists exist, not permission to read their rows.
	Navigation does not require permission to administer Partner Type settings.
	"""

	sources = frappe.get_all(
		"YRP Partner Type", pluck="reference_doctype", order_by="reference_doctype asc"
	)
	return [
		frappe._dict(type="Link", label=doctype, link_type="DocType", link_to=doctype, child=0)
		for doctype in dict.fromkeys((*MANAGEMENT_DOCTYPES, *sources))
		if doctype and frappe.has_permission(doctype, "read")
	]


class PartnerWorkspaceMixin:
	"""Extend only the Partner card; native Workspace filtering runs afterwards."""

	def get_link_groups(self):
		if self.name != WORKSPACE:
			return super().get_link_groups()
		return [frappe._dict(label=SIDEBAR, type="Card Break", links=get_partner_links())]


def add_partner_navigation(bootinfo):
	"""Populate the existing, allowed sidebar using native visibility checks."""

	sidebar = (bootinfo.get("workspace_sidebar_item") or {}).get(SIDEBAR.lower())
	if not sidebar:
		return
	views = DeskViews()
	user = frappe.get_user()
	if not user.can_read:
		user.build_permissions()
	views.can_read = user.can_read
	# Preserve Home only if native boot filtering already allowed the workspace.
	home = [
		item for item in sidebar["items"]
		if item.get("link_type") == "Workspace" and item.get("link_to") == WORKSPACE
	]
	sidebar["items"] = home + [
		link for link in get_partner_links()
		if views.is_item_allowed(link.link_to, link.link_type)
	]


def clear_partner_navigation_cache(doc=None, method=None):
	"""Refresh future Desk boots after a committed Partner Type change."""

	frappe.db.after_commit.add(lambda: frappe.cache.delete_key("bootinfo"))


def setup_partner_workspace():
	"""Upgrade the app-owned landing page without shadowing the Partner list.

	Frappe resolves Workspace slugs before DocType routes, including `/view/list`.
	Keep the module/sidebar/icon named YRP Partner, but give the landing Workspace
	its own name. Native rename updates links; standard imports install the small
	static skeleton. Configured and user-specific links are never stored here.
	"""

	from frappe.modules.import_file import import_file_by_path

	old = frappe.db.get_value("Workspace", SIDEBAR, ["app", "module"], as_dict=True)
	if old and old.app == "yrp" and old.module == SIDEBAR:
		frappe.rename_doc(
			"Workspace", SIDEBAR, WORKSPACE,
			force=True, merge=bool(frappe.db.exists("Workspace", WORKSPACE)),
			show_alert=False, rebuild_search=False,
		)
	for path in (
		("yrp_partner", "workspace", "yrp_partner_management", "yrp_partner_management.json"),
		("workspace_sidebar", "yrp_partner.json"),
	):
		import_file_by_path(frappe.get_app_path("yrp", *path), force=True)
	clear_partner_navigation_cache()
