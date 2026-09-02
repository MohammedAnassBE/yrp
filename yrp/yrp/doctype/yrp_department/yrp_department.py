# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class YRPDepartment(Document):
	pass

@frappe.whitelist()
def get_user_departments(department = None):

	user = frappe.session.user
	query = f"""
		SELECT DISTINCT  t1.parent as department from `tabYRP Department User` t1
		JOIN `tabYRP Department` t2 ON t1.parent = t2.name WHERE 1=1 {
			f" AND t1.parent = {frappe.db.escape(department)} " if department else " "
		} AND t2.disabled = 0 AND user = {frappe.db.escape(user)}
	"""
	return [ i['department'] for i in frappe.db.sql(query, as_dict=True)]

def get_all_active_dept():
	return frappe.get_all('YRP Department', filters=[['disabled','=',0]] ,pluck='name')


Department = YRPDepartment
