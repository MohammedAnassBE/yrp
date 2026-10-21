import frappe


def skip_generated_test_records():
	"""Mark every DocType's test records as loaded so the runner never generates any.

	Tests here build their own fixtures on the migrated site. Generated records pull in
	ERPNext's BootStrapTestData, which rewrites System Settings and clashes with the
	migrated fiscal years.
	"""
	for doctype in frappe.get_all("DocType", pluck="name"):
		frappe.local.test_objects.setdefault(doctype, [])


skip_generated_test_records()
