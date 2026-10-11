import frappe

from yrp.yrp_partner.customer_access import is_partner_user


def execute():
	"""Give partners' existing Prepared Report result Files to Administrator, as new ones are, so no partner
	downloads a raw result by URL. The Files stay private."""
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Prepared Report", "owner": ("!=", "Administrator")},
		fields=["name", "owner"],
		limit=0,
	)
	partners = {owner for owner in {file.owner for file in files} if is_partner_user(owner)}
	for file in files:
		if file.owner in partners:
			frappe.db.set_value("File", file.name, "owner", "Administrator", update_modified=False)
