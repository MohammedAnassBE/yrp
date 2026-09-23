# Attribute mapping values and ownership

Each mapping selects values from one native ERPNext **Item Attribute**. The
`attribute_value` rows store canonical value text such as `Blue`, never the
internal name of an `Item Attribute Value` child row. Desk uses Frappe's native
Autocomplete control. Server validation enforces the same attribute, unique
values, and native numeric range/increment rules. An empty mapping is valid
while its owner is being configured.

**YRP Item Master Template** and **YRP Product** keep separate mapping documents.
Selecting a template only reads defaults; saving the Product copies the selected
mapping and its values in the same transaction. New or replaced mapping links
are copied, private saved links remain stable, and previously shared links are
separated on the owner's next save. Unsaved/template-reset forms hide mapping
links until saving creates the private copies.

Cleanup runs after an owner save or deletion, once its child rows reflect the
change. Only removed/replaced mappings and source mappings actually consumed by
that save are considered. If a consumed standalone mapping has no remaining
references, its private copy replaces it and the original is removed. Unrelated
standalone mappings and drafts are never swept.

Saved references in `YRP Item Item Attribute`, native `Item Variant Attribute`,
and `YRP IPD Item Attribute` preserve mappings needed by other catalog or
manufacturing documents. Native Frappe deletion checks also protect other Link
and Dynamic Link references; cleanup never uses forced deletion. Owner writes,
copies, and cleanup roll back together. Mapping IDs are locked in sorted order;
this does not replace concurrency control in independent Item/IPD integrations.

The idempotent post-model-sync patch
`yrp.patches.separate_template_product_attribute_mappings` repairs existing
shared Template/Product links through normal document saves under row locks.
It preserves selected values and makes no further changes after separation.
It neither purges standalone mappings nor changes the separate
`YRP Item Dependent Attribute Mapping` lifecycle.
