"""Scoped attribute-value storage and explicit actual-value business adapters.

Only declared fields are converted. ERPNext's native attribute rows stay Data.
"""
import json
from pathlib import Path

import frappe
from frappe import _
from yrp.attribute_values import MASTER, ensure_value_master


def fields():
    cached = getattr(frappe.local, "attribute_link_fields", None)
    if cached is not None:
        return cached
    registry = {}
    for app in frappe.get_installed_apps():
        path = Path(frappe.get_app_path(app, "attribute_link_fields.json"))
        if path.exists():
            registry.update(json.loads(path.read_text()))
    frappe.local.attribute_link_fields = registry
    return registry


def value(raw):
    """Resolve a stored Link without altering ordinary strings or numeric values."""
    if not isinstance(raw, str) or not raw.startswith("IAV-"):
        return raw
    cache = getattr(frappe.local, "attribute_link_values", None)
    if cache is None:
        cache = frappe.local.attribute_link_values = {}
    if raw not in cache:
        row = frappe.db.get_value(MASTER, raw, ["attribute_name", "attribute_value"], as_dict=True)
        if not row:
            frappe.throw(_("Attribute Value {0} does not exist.").format(raw))
        cache[raw] = row
    return cache[raw].attribute_value


def attribute_for(doc, field, ancestors=()):
    """Use the owning document's attribute contract before fixed-domain defaults."""
    context = [doc, *reversed(ancestors)]
    if not ancestors and doc.get("parent") and doc.get("parenttype"):
        parent = frappe.db.get_value(doc.get("parenttype"), doc.get("parent"), "*", as_dict=True)
        if parent:
            context.append(parent)
    related = getattr(frappe.local, "attribute_link_context", None)
    if related is None:
        related = frappe.local.attribute_link_context = {}
    seen = set()
    for node in context:
        for source, target in (("lot", "SD YRP Lot"), ("production_detail", "YRP Item Production Detail"),
                               ("item_production_detail", "YRP Item Production Detail"),
                               ("cutting_plan", "SD YRP Cutting Plan")):
            name = node.get(source)
            key = (target, name)
            if not name or key in seen:
                continue
            seen.add(key)
            if key not in related:
                related[key] = frappe.db.get_value(target, name, "*", as_dict=True)
            if related[key]:
                context.append(related[key])
    contract = {
        "dependent_attribute_value": "dependent_attribute",
        "po_dependent_attribute_value": "po_dependent_attribute",
        "major_attribute_value": "set_item_attribute",
        "major_panel_value": "stiching_attribute",
        "stiching_attribute_value": "stiching_attribute",
        "set_item_attribute_value": "set_item_attribute",
        "packing_attribute_value": "packing_attribute",
    }.get(field)
    if field == "attribute_value":
        dt = doc.get("doctype")
        contract = {
            "YRP Item Dependent Attribute Mapping Detail": "dependent_attribute",
            "SD YRP Cutting Order Colour Value": "packing_attribute",
            "SD YRP Item Production Detail Packing Attribute Detail": "packing_attribute",
            "SD YRP Item Production Detail Packing Size Detail": "primary_attribute",
            "SD YRP Item Production Detail Set Item Combination": "packing_attribute",
        }.get(dt, "attribute")
    if contract:
        for node in context:
            if node.get(contract):
                return node.get(contract)
    if "stage" in field:
        for node in context:
            if node.get("dependent_attribute"):
                return node.get("dependent_attribute")
        return "Stage"
    if "colour" in field:
        if doc.get("doctype") in ("SD YRP Cut Bundle Movement Ledger", "SD YRP Cutting LaySheet Bundle"):
            for node in context:
                if node.get("packing_attribute"):
                    return node.get("packing_attribute")
        return "Colour"
    if field == "dia" or field.endswith("_dia"):
        return "Dia"
    if field in ("size", "size_type_value") or doc.get("doctype") == "SD YRP FG Item Size":
        for node in context:
            if node.get("primary_attribute"):
                return node.get("primary_attribute")
        return "Size"
    if field in ("panel", "part"):
        for node in context:
            if node.get("stiching_attribute"):
                return node.get("stiching_attribute")
    return None


def link(raw, attribute=None):
    if raw in (None, ""):
        return raw
    cache = getattr(frappe.local, "attribute_link_names", None)
    if cache is None:
        cache = frappe.local.attribute_link_names = {}
    key = (str(raw), attribute)
    if key not in cache:
        cache[key] = _link(raw, attribute)
    return cache[key]


def _link(raw, attribute=None):
    if raw in (None, ""):
        return raw
    raw = frappe.utils.cstr(raw)
    row = frappe.db.get_value(MASTER, raw, ["name", "attribute_name"], as_dict=True)
    if row:
        if attribute and row.attribute_name != attribute:
            frappe.throw(_("Attribute Value {0} must belong to {1}.").format(raw, attribute))
        return row.name
    if not attribute:
        # Historical source names are preserved native child IDs; prefer that
        # explicit identity over ambiguous text matching across attributes.
        native = frappe.db.get_value("Item Attribute Value", raw,
                                     ["parent", "attribute_value"], as_dict=True)
        if native and native.attribute_value == raw:
            attribute = native.parent
        else:
            candidates = set(frappe.get_all("Item Attribute Value",
                filters={"attribute_value": raw, "parenttype": "Item Attribute"}, pluck="parent"))
            if len(candidates) != 1:
                frappe.throw(_("Cannot determine the Item Attribute for value {0}; select a scoped Attribute Value.").format(raw))
            attribute = candidates.pop()
    return ensure_value_master(attribute, raw)


def normalize(doc, ancestors=()):
    registry = fields()
    for field in registry.get(doc.doctype, []):
        raw = doc.get(field)
        if raw not in (None, ""):
            # Preserve already-stored historical Link identities on unrelated
            # saves, even where old source rows had inconsistent attribute labels.
            stored = None
            if isinstance(raw, str) and raw.startswith("IAV-") and doc.get("name"):
                stored = frappe.db.get_value(doc.doctype, doc.name, field)
            expected = None if stored == raw else attribute_for(doc, field, ancestors)
            doc.set(field, link(raw, expected))
    for table in doc.meta.get_table_fields():
        for row in doc.get(table.fieldname) or []:
            normalize(row, (*ancestors, doc))


class AttributeLinkStorageMixin:
    """Normalize internal plain-value writes before Frappe validates Links."""
    def _validate_links(self):
        normalize(self)
        return super()._validate_links()

    def db_insert(self, *args, **kwargs):
        normalize(self)
        return super().db_insert(*args, **kwargs)

    def db_update(self, *args, **kwargs):
        normalize(self)
        return super().db_update(*args, **kwargs)


def business_values(data):
    """Convert detached query results; never mutate cached Frappe documents."""
    if isinstance(data, dict):
        return type(data)({key: business_values(val) for key, val in data.items()})
    if isinstance(data, list):
        return [business_values(val) for val in data]
    if isinstance(data, tuple):
        return tuple(business_values(val) for val in data)
    return value(data)


def query_filters(doctype, filters):
    registered = fields().get(doctype, [])
    if not registered or not isinstance(filters, (dict, list, tuple)):
        return filters
    context = frappe._dict(doctype=doctype)
    if isinstance(filters, dict):
        context.update({key: val for key, val in filters.items() if not isinstance(val, (list, tuple, dict))})
    def encode(field, val):
        if field not in registered:
            return val
        if isinstance(val, (list, tuple)):
            operator, operand = val
            if str(operator).lower() in ('is', 'like', 'not like'):
                return val
            return [operator, [link(v, attribute_for(context, field)) for v in operand] if isinstance(operand, (list, tuple)) else link(operand, attribute_for(context, field))]
        return link(val, attribute_for(context, field))
    if isinstance(filters, dict):
        return {key: encode(key, val) for key, val in filters.items()}
    result = []
    for row in filters:
        if not isinstance(row, (list, tuple)):
            result.append(row)
        elif len(row) == 3:
            result.append([row[0], *encode(row[0], row[1:])])
        elif len(row) == 4:
            result.append([row[0], row[1], *encode(row[1], row[2:])])
        else:
            result.append(row)
    return result


def get_value(doctype, filters=None, fieldname='name', *args, **kwargs):
    return business_values(frappe.db.get_value(doctype, query_filters(doctype, filters), fieldname, *args, **kwargs))


def get_single_value(doctype, fieldname, *args, **kwargs):
    return business_values(frappe.db.get_single_value(doctype, fieldname, *args, **kwargs))


def get_cached_value(doctype, name, fieldname='name', *args, **kwargs):
    return business_values(frappe.get_cached_value(doctype, name, fieldname, *args, **kwargs))


def set_value(doctype, name, fieldname, value=None, *args, **kwargs):
    registered = fields().get(doctype, [])
    updates = fieldname if isinstance(fieldname, dict) else {fieldname: value}
    context = frappe._dict(doctype=doctype)
    if any(key in registered for key in updates) and isinstance(name, str):
        context.update(frappe.db.get_value(doctype, name, "*", as_dict=True) or {})
    updates = {key: link(val, attribute_for(context, key)) if key in registered else val for key, val in updates.items()}
    if isinstance(fieldname, dict):
        return frappe.db.set_value(doctype, query_filters(doctype, name), updates, *args, **kwargs)
    return frappe.db.set_value(doctype, query_filters(doctype, name), fieldname, updates[fieldname], *args, **kwargs)


def get_all(doctype, *args, **kwargs):
    if 'filters' in kwargs:
        kwargs['filters'] = query_filters(doctype, kwargs['filters'])
    return business_values(frappe.get_all(doctype, *args, **kwargs))


def get_list(doctype, *args, **kwargs):
    if 'filters' in kwargs:
        kwargs['filters'] = query_filters(doctype, kwargs['filters'])
    return business_values(frappe.get_list(doctype, *args, **kwargs))


def boot_session(bootinfo):
    # Desk needs the text side of a Link when constructing variant/stock payloads.
    # Attribute values are the shared Item catalogue, not business transactions.
    registry = fields()
    parents = set(registry)
    tables = frappe.get_all('DocField', filters={'fieldtype': ['in', ['Table', 'Table MultiSelect']]}, fields=['parent', 'options'])
    tables += [frappe._dict(parent=r.dt, options=r.options) for r in frappe.get_all('Custom Field',
        filters={'fieldtype': ['in', ['Table', 'Table MultiSelect']]}, fields=['dt', 'options'])]
    while True:
        previous = len(parents)
        parents.update(row.parent for row in tables if row.options in parents)
        if len(parents) == previous:
            break
    bootinfo.yrp_attribute_link_forms = sorted(parents)
    bootinfo.yrp_attribute_link_fields = registry
    bootinfo.yrp_attribute_values = {
        row.name: [row.attribute_name, row.attribute_value]
        for row in frappe.get_all(MASTER, fields=['name', 'attribute_name', 'attribute_value'])
    }


def backfill(*, dry_run=False):
    """Convert each distinct source identity in batches, retaining audit timestamps."""
    changes = []
    for doctype, fieldnames in fields().items():
        if not frappe.db.exists('DocType', doctype):
            continue
        meta = frappe.get_meta(doctype)
        print("Checking attribute Links:", doctype, flush=True)
        for field in fieldnames:
            # Field names such as colour, dia, size and stage have a stable
            # attribute domain. Keep that domain while converting historical
            # text so equal labels used by another attribute cannot make the
            # backfill ambiguous.
            attribute = attribute_for(frappe._dict(doctype=doctype), field)
            if meta.issingle:
                raw = frappe.db.get_single_value(doctype, field)
                rows = [(raw, 1)] if raw else []
            else:
                # Identifiers come exclusively from the code-owned registry.
                rows = frappe.db.sql(f"SELECT `{field}`, COUNT(*) FROM `tab{doctype}` "
                    f"WHERE COALESCE(`{field}`, '') != '' GROUP BY `{field}`")
            for raw, count in rows:
                if isinstance(raw, str) and raw.startswith('IAV-'):
                    link(raw)
                    continue
                try:
                    target = link(raw, attribute)
                except Exception as exc:
                    raise frappe.ValidationError(f"Cannot convert {doctype}.{field}={raw}: {exc}") from exc
                if target != raw:
                    changes.append((doctype, field, raw, target, meta.issingle, count))
    if dry_run:
        return {'distinct_changes': len(changes), 'field_values': sum(row[-1] for row in changes)}
    grouped = {}
    for doctype, field, raw, target, single, count in changes:
        if single:
            frappe.db.set_single_value(doctype, field, target, update_modified=False)
        else:
            grouped.setdefault((doctype, field), []).append((raw, target))
    for (doctype, field), pairs in grouped.items():
        # One scan per batch instead of one full ledger scan per distinct value.
        for offset in range(0, len(pairs), 500):
            batch = pairs[offset:offset + 500]
            cases = " ".join("WHEN %s THEN %s" for _ in batch)
            placeholders = ", ".join("%s" for _ in batch)
            parameters = [part for pair in batch for part in pair] + [raw for raw, _ in batch]
            frappe.db.sql(f"UPDATE `tab{doctype}` SET `{field}` = CASE `{field}` {cases} ELSE `{field}` END "
                          f"WHERE `{field}` IN ({placeholders})", parameters)
    for doctype in fields():
        frappe.clear_document_cache(doctype)
    return {'distinct_changes': len(changes), 'field_values': sum(row[-1] for row in changes)}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_values(doctype, txt, searchfield, start, page_len, filters):
    if not frappe.has_permission('Item Attribute', 'read'):
        frappe.throw(_('Not permitted to read Item Attribute values'), frappe.PermissionError)
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    row = frappe._dict(filters.get('row') or {})
    parent = frappe._dict(filters.get('parent') or {})
    attribute = attribute_for(row, filters.get('fieldname'), (parent,))
    conditions = {'attribute_name': attribute} if attribute else {}
    if txt:
        conditions['attribute_value'] = ['like', '%' + txt + '%']
    return frappe.get_list(MASTER, filters=conditions,
        fields=['name', 'attribute_value', 'attribute_name'],
        start=start, page_length=page_len, as_list=True, order_by='attribute_value asc')
