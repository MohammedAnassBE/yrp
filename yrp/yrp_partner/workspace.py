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
	links = [
		frappe._dict(type="Link", label=doctype, link_type="DocType", link_to=doctype, child=0)
		for doctype in dict.fromkeys((*MANAGEMENT_DOCTYPES, *sources))
		if doctype and frappe.has_permission(doctype, "read")
	]

	from yrp.yrp_partner.customer_access import is_customer_report_user
	if is_customer_report_user():
		from yrp.yrp_partner.customer_reports import CUSTOMER_REPORTS
		for doctype in ("Sales Order", "Delivery Note", "Packing Slip", "Sales Invoice", "GL Entry", "Account", "Company"):
			if frappe.has_permission(doctype, "read") and not any(link.link_to == doctype for link in links):
				link = frappe._dict(type="Link", label=doctype, link_type="DocType", link_to=doctype, child=0)
				if doctype == "Account":
					# Native sidebar route options select the readable account list;
					# the full chart hierarchy contains unrelated internal accounts.
					link.route_options = "{}"
				links.append(link)
		for name, doctype in CUSTOMER_REPORTS.items():
			if frappe.get_doc("Report", name).is_permitted():
				links.append(frappe._dict(type="Link", label=name, link_type="Report", link_to=name,
					report_ref_doctype=doctype, is_query_report=1, child=0))
	return links



class PartnerWorkspaceMixin:
	"""Extend only the Partner card; native Workspace filtering runs afterwards."""

	def get_link_groups(self):
		if self.name != WORKSPACE:
			return super().get_link_groups()
		return [frappe._dict(label=SIDEBAR, type="Card Break", links=get_partner_links())]


def add_partner_navigation(bootinfo):
	"""Populate the existing, allowed sidebar using native visibility checks."""

	scope_company_bootinfo(bootinfo)
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
	links = []
	for link in get_partner_links():
		if not views.is_item_allowed(link.link_to, link.link_type):
			continue
		if link.link_type == "Report":
			# Native sidebar routing requires this object, not the legacy
			# Workspace Link's report_ref_doctype/is_query_report fields.
			link.report = views.allowed_reports[link.link_to]
		links.append(link)
	sidebar["items"] = home + links


def scope_company_bootinfo(bootinfo):
	"""Filter ERPNext's preloaded Companies and choose a permitted UI default.

	ERPNext boot preloads all Companies with raw SQL for tree selectors. Narrow
	that payload as well as link queries. Change this response only, never User
	or site defaults; all server document/report checks remain authoritative.
	"""
	from yrp.yrp_partner.customer_access import is_customer_report_user
	if not is_customer_report_user():
		return
	allowed = sorted(frappe.get_list("Company", pluck="name", limit_page_length=0)) if frappe.has_permission("Company", "read") else []
	if "docs" in bootinfo:
		bootinfo.docs = [doc for doc in bootinfo.docs
			if doc.get("doctype") not in {"Company", ":Company"} or doc.get("name") in allowed]
	if bootinfo.get("user") is not None:
		defaults = dict(bootinfo.user.get("defaults") or {})
		current = defaults.get("Company") or defaults.get("company")
		current = current if isinstance(current, (list, tuple)) else [current]
		selected = next((name for name in current if name in allowed), allowed[0] if allowed else None)
		defaults.update(Company=selected, company=selected)
		bootinfo.user["defaults"] = defaults
		if "sysdefaults" in bootinfo:
			bootinfo.sysdefaults = frappe._dict(bootinfo.sysdefaults.copy())
			bootinfo.sysdefaults.update(Company=selected, company=selected)
			if bootinfo.sysdefaults.get("demo_company") not in allowed:
				bootinfo.sysdefaults.demo_company = None


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
