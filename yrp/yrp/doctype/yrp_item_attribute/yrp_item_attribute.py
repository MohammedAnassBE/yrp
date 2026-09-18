"""Compatibility imports for the retired YRP Item Attribute DocType.

ERPNext's Item Attribute is the sole storage authority. Keep this module path
so older integrations can import the controller name without recreating the
legacy standalone attribute-value model.
"""

from erpnext.stock.doctype.item_attribute.item_attribute import ItemAttribute


YRPItemAttribute = ItemAttribute
