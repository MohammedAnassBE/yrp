"""Product-owned sales setup, initialized explicitly from an Item Master Template.

Template selection supplies a snapshot. Product edits, including cleared tax or
category rows, survive later saves; this document does not create Items yet.
"""
import frappe
from frappe.model.document import Document
from yrp.yrp_retail.category import validate_template_classification
from yrp.yrp_retail.item_template import rows_of, validate_template
from yrp.yrp_retail.logic import require, finite_number

# HSN belongs to the Product; template selection must not overwrite it.
SCALARS = ('default_unit_of_measure', 'secondary_unit_of_measure',
           'primary_attribute', 'dependent_attribute', 'dependent_attribute_mapping',
           'item_type', 'is_free_item')
TABLES = ('uom_conversion_details', 'attributes', 'additional_parameters',
          'categories', 'taxes', 'item_defaults')


class YRPProduct(Document):
    def validate(self):
        old = self.get_doc_before_save()
        if not old or old.item_template != self.item_template:
            frappe.get_doc('YRP Item Master Template', self.item_template).check_permission('read')
        validate_template_classification(self)
        validate_template(self)
        require(not frappe.db.get_value('UOM', self.default_unit_of_measure, 'secondary_only'),
                'Default UOM cannot be a secondary-only UOM.')
        seen = set()
        for row in self.uom_conversion_details:
            require(row.uom not in seen, 'A UOM conversion can only occur once.')
            seen.add(row.uom)
            factor = finite_number(row.conversion_factor, 'Conversion factor')
            require(factor > 0, 'Conversion factor must be positive.')
            if row.uom == self.default_unit_of_measure:
                require(factor == 1, 'Default UOM conversion factor must be one.')
        names = [row.attribute for row in self.attributes]
        require(len(names) == len(set(names)), 'An attribute can only occur once.')
        for attribute in (self.primary_attribute, self.dependent_attribute):
            require(not attribute or attribute in names, 'Primary and dependent attributes must be in the attribute list.')
        for row in self.attributes:
            if row.mapping:
                require(frappe.db.get_value('YRP Item Item Attribute Mapping', row.mapping, 'attribute_name') == row.attribute,
                        'Attribute mapping must belong to the selected attribute.')
        from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import ensure_owned_mappings
        ensure_owned_mappings(self)

    def on_update(self):
        from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import cleanup_owner_mappings
        cleanup_owner_mappings(self)

    def after_delete(self):
        from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import cleanup_owner_mappings
        cleanup_owner_mappings(self, deleted=True)


@frappe.whitelist()
def get_template_defaults(template_name):
    """Return only business values, never source child IDs or permissions."""
    frappe.has_permission('YRP Product', 'read', throw=True)
    template = frappe.get_doc('YRP Item Master Template', template_name)
    template.check_permission('read')
    values = {field: template.get(field) for field in SCALARS}
    values.update({field: rows_of(template.get(field) or []) for field in TABLES})
    return values


@frappe.whitelist()
def get_product_categories(item_type):
    """Read category controls, including retained assignments on disabled roots."""
    frappe.has_permission('YRP Product', 'read', throw=True)
    root = frappe.get_doc('YRP Item Category', item_type)
    root.check_permission('read')
    require(root.node_type == 'Item Type' and not root.parent_yrp_item_category,
            'Select an Item Type root.')
    return frappe.get_list('YRP Item Category', filters={
        'parent_yrp_item_category': item_type, 'node_type': 'Category', 'disabled': 0},
        fields=['name', 'category_name'], order_by='category_name', limit_page_length=0)
