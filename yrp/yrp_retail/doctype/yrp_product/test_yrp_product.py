"""Synthetic products and taxonomy; each test rolls its records back."""
import frappe
from yrp.yrp_retail.test_category import TestItemCategory
from yrp.yrp_retail.doctype.yrp_product.yrp_product import get_template_defaults


class TestYRPProduct(TestItemCategory):
    def setUp(self):
        super().setUp()
        self.template=frappe.get_doc(dict(doctype='YRP Item Master Template',name=self.label('Template'),
            default_unit_of_measure=self.uom.name,item_type=self.root.name,
            categories=[dict(category=self.category.name,value=self.value.name)])).insert()

    def product(self):
        defaults=get_template_defaults(self.template.name)
        return frappe.get_doc(dict(doctype='YRP Product',product_name=self.label('Product'),
            item_template=self.template.name,gst_hsn_code=self.hsn,**defaults))

    def test_template_defaults_do_not_overwrite_product_hsn(self):
        defaults=get_template_defaults(self.template.name)
        self.assertNotIn('gst_hsn_code',defaults)
        product=self.product().insert()
        product.update(defaults)
        product.save();product.reload()
        self.assertEqual(product.gst_hsn_code,self.hsn)

    def test_prefill_copies_business_fields_and_independent_child_rows(self):
        product=self.product().insert()
        self.assertEqual(product.gst_hsn_code,self.hsn)
        self.assertEqual(product.default_unit_of_measure,self.uom.name)
        self.assertEqual(product.categories[0].value,self.value.name)
        self.assertNotEqual(product.categories[0].name,self.template.categories[0].name)
        self.assertEqual(product.categories[0].parenttype,'YRP Product')

    def test_cleared_category_rows_stay_deleted_after_save(self):
        product=self.product().insert()
        child=product.categories[0].name
        product.set('categories',[])
        product.save();product.reload()
        self.assertEqual(product.categories,[])
        self.assertFalse(frappe.db.exists('YRP Product Category',child))

    def test_invalid_branch_and_duplicate_categories_rejected(self):
        other_root,other_category,other_value=self.tree()
        product=self.product()
        product.categories[0].value=other_value.name
        with self.assertRaises(frappe.ValidationError):product.insert()
        product=self.product()
        product.append('categories',dict(category=self.category.name,value=self.value.name))
        with self.assertRaises(frappe.ValidationError):product.insert()

    def test_product_edits_survive_template_changes(self):
        product=self.product().insert()
        product.is_free_item=1
        product.set('taxes',[])
        product.save()
        self.template.set('categories',[])
        self.template.save()
        product.reload();product.save()
        self.assertEqual(product.is_free_item,1)
        self.assertEqual(product.categories[0].value,self.value.name)

    def test_hsn_uom_and_conversion_validation(self):
        product=self.product();product.gst_hsn_code=None
        with self.assertRaises(frappe.MandatoryError):product.insert()
        product=self.product()
        product.append('uom_conversion_details',dict(uom=self.uom.name,conversion_factor=0))
        with self.assertRaises(frappe.ValidationError):product.insert()
        product.uom_conversion_details[0].conversion_factor=2
        with self.assertRaises(frappe.ValidationError):product.insert()

    def test_prefill_denies_guest(self):
        original=frappe.session.user
        try:
            frappe.set_user('Guest')
            with self.assertRaises(frappe.PermissionError):get_template_defaults(self.template.name)
        finally:frappe.set_user(original)
