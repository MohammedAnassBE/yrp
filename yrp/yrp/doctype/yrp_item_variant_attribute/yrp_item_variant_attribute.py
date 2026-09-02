# Copyright (c) 2021, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class YRPItemVariantAttribute(Document):
	pass

def on_doctype_update():
	frappe.db.add_index('YRP Item Variant Attribute', ["attribute","attribute_value"])


ItemVariantAttribute = YRPItemVariantAttribute
