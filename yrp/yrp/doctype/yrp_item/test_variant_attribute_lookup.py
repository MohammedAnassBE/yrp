"""Template-scoped native attribute lookup preserves multi-value matching."""
import unittest
from unittest.mock import patch

from yrp.yrp.doctype.yrp_item.yrp_item import _get_variants_by_attributes

MODULE = 'yrp.yrp.doctype.yrp_item.yrp_item'


class VariantAttributeLookupTest(unittest.TestCase):
    def test_intersects_attributes_within_template_and_multiple_values(self):
        attributes = {
            'A': {'Colour': 'Wine', 'Size': 'S'},
            'B': {'Colour': 'Black', 'Size': 'M'},
            'C': {'Colour': 'White', 'Size': 'S'},
            'OTHER': {'Colour': 'Wine', 'Size': 'S'},
        }
        queries = []

        def get_all(doctype, filters=None, **kwargs):
            if doctype == 'Item':
                self.assertEqual(filters, {'variant_of': 'Template'})
                return ['A', 'B', 'C']
            self.assertEqual(doctype, 'Item Variant Attribute')
            self.assertEqual(filters['parenttype'], 'Item')
            self.assertEqual(filters['parentfield'], 'attributes')
            candidates = set(filters['parent'][1])
            queries.append(candidates)
            return [name for name in candidates
                    if attributes[name][filters['attribute']] in filters['attribute_value'][1]]

        with patch(f'{MODULE}.frappe.get_all', side_effect=get_all):
            result = _get_variants_by_attributes({'Colour': ['Wine', 'Black'], 'Size': 'S'}, 'Template')
        self.assertEqual(result, ['A'])
        self.assertEqual(queries, [{'A', 'B', 'C'}, {'A', 'B'}])

    def test_empty_template_does_not_scan_global_attributes(self):
        with patch(f'{MODULE}.frappe.get_all', return_value=[]) as query:
            self.assertEqual(_get_variants_by_attributes({'Stage': 'Cut'}, 'Empty'), [])
        query.assert_called_once_with('Item', {'variant_of': 'Empty'}, pluck='name')

    def test_no_matching_attribute_stops_early(self):
        with patch(f'{MODULE}.frappe.get_all', side_effect=[['A'], []]) as query:
            self.assertEqual(_get_variants_by_attributes({'Colour': 'Missing', 'Size': 'S'}, 'Template'), [])
        self.assertEqual(query.call_count, 2)

    def test_unscoped_search_keeps_intersection_semantics(self):
        with patch(f'{MODULE}.frappe.get_all', side_effect=[['A', 'B'], ['B', 'C']]):
            self.assertEqual(_get_variants_by_attributes({'Colour': 'Wine', 'Size': 'S'}), ['B'])
