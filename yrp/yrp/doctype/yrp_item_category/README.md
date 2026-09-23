# YRP Item Category identity

New records use their trimmed Category Name as the document ID. For example,
the Item Type `Apparel` is named `Apparel`. A repeated label in another branch
uses Frappe's standard numeric suffix (`Season`, `Season-1`); both records keep
the visible Category Name `Season`. Duplicate labels under the same parent
remain invalid.

The native tree displays escaped Category Names without adding internal IDs.
An `All Item Types` container keeps every actual Item Type editable, including
when the tree contains just one Item Type. Internal tree values still contain
the actual document IDs for routing and links.

Saving an existing category changes its label without silently renaming its
document ID or moving the branch. Existing hash IDs remain valid. Converting
them is a separate, explicit `frappe.rename_doc` operation, which updates Link
fields and preserves nested-set references; never replace IDs with raw SQL.
Existing spellings and classification assignments are not inferred or changed.

## Creating classifications

Use **Add Child** on All Item Types to create an Item Type, on an Item Type to
create a Category, and on a Category to create a Value. The standard Frappe tree
dialog asks only for the name. Its `add_tree_node` hook derives the level from
the selected parent and uses normal document insertion and permissions.

New records outside the tree open the full form so the parent is available;
the generic Quick Entry dialog omits this conditionally mandatory field.
On the full form, Category and Value require a parent of the preceding level.
Is Group is read-only and derived from Node Type on the server as well as the
form, so a Value cannot accidentally become a group. Values cannot have children;
the three-level hierarchy and existing restrictions on moving nodes remain.
