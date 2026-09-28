"""Integration tests: Link storage, native ERPNext domain and semantic readers."""
import unittest
import frappe

from yrp.attribute_values import (
	MASTER, attribute_value_name, get_mapping_document, get_mapping_values,
)


class AttributeValueLinksTest(unittest.TestCase):
	def setUp(self):
		frappe.set_user('Administrator')
		self.point = 'attribute_link_test'
		frappe.db.savepoint(self.point)
		self.attribute = '_LinkTest-' + frappe.generate_hash(length=8)
		self.native = frappe.get_doc({'doctype':'Item Attribute','attribute_name':self.attribute,
			'item_attribute_values':[{'attribute_value':'XL','abbr':'XL'}]}).insert()
		self.link = attribute_value_name(self.attribute,'XL')

	def tearDown(self):
		frappe.db.rollback(save_point=self.point)
		for dt in ['Item Attribute', MASTER, 'YRP Item Item Attribute Mapping']:
			frappe.clear_document_cache(dt)

	def mapping(self, value=None):
		return frappe.get_doc({'doctype':'YRP Item Item Attribute Mapping','attribute_name':self.attribute,
			'values':[{'attribute_value':value or self.link}]}).insert()

	def test_native_insert_creates_linkable_master(self):
		self.assertEqual(frappe.db.get_value(MASTER,self.link,'attribute_value'),'XL')

	def test_link_storage_and_plain_value_api_do_not_mutate_cache(self):
		doc=self.mapping()
		self.assertEqual(frappe.get_cached_doc(doc.doctype,doc.name).values[0].attribute_value,self.link)
		self.assertEqual(get_mapping_values(doc.name),['XL'])
		self.assertEqual(frappe.get_cached_doc(doc.doctype,doc.name).values[0].attribute_value,self.link)
		view=get_mapping_document(doc.name)
		view.save()
		self.assertEqual(frappe.db.get_value('YRP Item Item Attribute Mapping Value',view.values[0].name,'attribute_value'),self.link)

	def test_legacy_internal_text_write_is_normalized(self):
		doc=self.mapping('XL')
		self.assertEqual(doc.values[0].attribute_value,self.link)

	def test_same_value_in_other_attribute_has_distinct_identity(self):
		other=frappe.get_doc({'doctype':'Item Attribute','attribute_name':self.attribute+'-Other',
			'item_attribute_values':[{'attribute_value':'XL','abbr':'XL'}]}).insert()
		other_link=attribute_value_name(other.name,'XL')
		self.assertNotEqual(self.link,other_link)
		with self.assertRaises(frappe.ValidationError):self.mapping(other_link)

	def test_direct_master_creation_populates_native_values(self):
		doc=frappe.get_doc({'doctype':MASTER,'attribute_name':self.attribute,'attribute_value':'XXL'}).insert()
		self.assertEqual(doc.name,attribute_value_name(self.attribute,'XXL'))
		self.assertTrue(frappe.db.exists('Item Attribute Value',{'parent':self.attribute,'attribute_value':'XXL'}))

	def test_value_identity_cannot_be_edited(self):
		doc=frappe.get_doc(MASTER,self.link);doc.attribute_value='Other'
		with self.assertRaises(frappe.ValidationError):doc.save()

	def test_removing_used_native_value_is_blocked(self):
		self.mapping()
		doc=frappe.get_doc('Item Attribute',self.attribute);doc.set('item_attribute_values',[])
		with self.assertRaises(frappe.LinkExistsError):doc.save()

	def test_numeric_attribute_uses_native_range_validation(self):
		doc=frappe.get_doc({'doctype':'Item Attribute','attribute_name':self.attribute+'-Numeric',
			'numeric_values':1,'from_range':1,'to_range':10,'increment':1}).insert()
		master=frappe.get_doc({'doctype':MASTER,'attribute_name':doc.name,'attribute_value':'3'}).insert()
		self.assertTrue(frappe.db.exists(MASTER,master.name))
		self.assertEqual(frappe.db.count('Item Attribute Value',{'parent':doc.name}),0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({'doctype':MASTER,'attribute_name':doc.name,'attribute_value':'3.5'}).insert()

	def test_attribute_rename_updates_master_and_mapping_link(self):
		mapping=self.mapping()
		new=self.attribute+'-Renamed'
		frappe.rename_doc('Item Attribute',self.attribute,new,force=True)
		expected=attribute_value_name(new,'XL')
		self.assertTrue(frappe.db.exists(MASTER,expected))
		self.assertEqual(frappe.db.get_value('YRP Item Item Attribute Mapping',mapping.name,'attribute_name'),new)
		self.assertEqual(frappe.db.get_value('YRP Item Item Attribute Mapping Value',mapping.values[0].name,'attribute_value'),expected)
		self.assertEqual(get_mapping_values(mapping.name),['XL'])
