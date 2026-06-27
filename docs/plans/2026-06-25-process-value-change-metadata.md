# Process Value-Change Metadata — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add item-generic transformation metadata to the base-yrp `Process` master — a `value_change_attributes` list declaring which attributes' *values* a process may change — so the customization app can later auto-generate the `IPD Process Matrix` from it.

**Architecture:** A new `istable` child DocType `Process Value Change` (one `attribute` Link → Item Attribute), surfaced as a `value_change_attributes` Table field inside a new "Transformation" section on `Process` (shown only for non-group processes). A `validate` on `Process` enforces no-duplicates and groups-carry-none. No engine/matrix/UI wiring — those live in the customization app (future).

**Tech Stack:** Frappe v16 (Python controller + DocType JSON), `IntegrationTestCase` tests, `bench migrate`/`run-tests`.

**Spec:** `apps/yrp/docs/design/2026-06-25-process-transformation-metadata-spec.md`

## Global Constraints

- **Base `yrp` only.** Do NOT touch `apps/frappe`, `apps/erpnext`, `apps/mgk_clothing_yrp`, or any engine/matrix/UI code. Matrix generation + the process-driven UI are the customization app's future work.
- **No git commits without explicit user authorization** (bench standing rule, conventions.md 2026-06-17). Each task ends at a "stage for review" checkpoint, NOT a commit. A single commit happens only when the user says so (final step).
- **DocType folder basename == `frappe.scrub(DocType name)`** (lessons-learned 2026-05-16): `Process Value Change` → folder `process_value_change`; `.json`/`.py` files + JSON `name` must all align.
- **No `frappe.db.commit()` in test files** (memory: tests auto-rollback under `IntegrationTestCase`).
- **`depends_on` is a SHOW condition** — to hide a field for groups use `eval:doc.is_group != 1` (the INVERSE of the existing Sub-Processes section's `== 1`). Do not copy the existing expression verbatim.
- **Active site this session:** `mgk_yrp.site` (base yrp is installed there). Commands below use it; substitute `$(cat .claude/state/current-site.txt)` if it changes. `migrate` is allowed (no prod site configured in the guard hook).
- **Module** for all new yrp DocTypes: `YRP` (matches `Process` and `IPD Matrix Attribute`).
- After any Python edit, the `advise-python-lint.sh` hook runs `py_compile` + `ruff`; address its output before proceeding (validation.md).

---

### Task 1: `Process Value Change` child DocType + `value_change_attributes` field on `Process`

Pure schema. Deliverable: a `Process` can hold `value_change_attributes` rows, and the "Transformation" section renders only for non-group processes.

**Files:**
- Create: `apps/yrp/yrp/yrp/doctype/process_value_change/__init__.py`
- Create: `apps/yrp/yrp/yrp/doctype/process_value_change/process_value_change.json`
- Create: `apps/yrp/yrp/yrp/doctype/process_value_change/process_value_change.py`
- Modify: `apps/yrp/yrp/yrp/doctype/process/process.json` (add to `field_order` + `fields`)
- Test: `apps/yrp/yrp/yrp/doctype/process/test_process.py`

**Interfaces:**
- Produces: child DocType `Process Value Change` with field `attribute` (Link → Item Attribute); parent field `Process.value_change_attributes` (Table → Process Value Change). Task 2 consumes `self.value_change_attributes` (each row exposes `.attribute`) and `self.is_group`.

- [ ] **Step 1: Create the child package init**

Create `apps/yrp/yrp/yrp/doctype/process_value_change/__init__.py` as an empty file (zero bytes).

- [ ] **Step 2: Create the child DocType JSON**

Create `apps/yrp/yrp/yrp/doctype/process_value_change/process_value_change.json` (mirrors `ipd_matrix_attribute.json`):

```json
{
 "actions": [],
 "creation": "2026-06-25 00:00:00",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["attribute"],
 "fields": [
  {"fieldname": "attribute", "fieldtype": "Link", "label": "Attribute", "options": "Item Attribute", "reqd": 1, "in_list_view": 1}
 ],
 "istable": 1,
 "links": [],
 "modified": "2026-06-25 00:00:00",
 "modified_by": "Administrator",
 "module": "YRP",
 "name": "Process Value Change",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 3: Create the child DocType controller**

Create `apps/yrp/yrp/yrp/doctype/process_value_change/process_value_change.py`:

```python
# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProcessValueChange(Document):
	pass
```

- [ ] **Step 4: Add the field + Transformation section to `process.json`**

In `apps/yrp/yrp/yrp/doctype/process/process.json`, edit `field_order` to insert two entries between `"wo_excess_allowed_percentage"` and `"section_break_details"`:

```json
  "wo_excess_allowed_percentage",
  "section_break_transformation",
  "value_change_attributes",
  "section_break_details",
```

Then add these two field objects to the `fields` array (anywhere in the array — order is driven by `field_order`):

```json
  {
   "fieldname": "section_break_transformation",
   "fieldtype": "Section Break",
   "label": "Transformation",
   "depends_on": "eval:doc.is_group != 1"
  },
  {
   "fieldname": "value_change_attributes",
   "fieldtype": "Table",
   "label": "Value Change Attributes",
   "options": "Process Value Change",
   "depends_on": "eval:doc.is_group != 1",
   "description": "Attributes whose VALUE this process may change (e.g. Colour for Dyeing). Empty = no value change. Drives the customization app's IPD Process Matrix auto-generation."
  }
```

- [ ] **Step 5: Migrate to sync the schema**

Run: `bench --site mgk_yrp.site migrate`
Expected: completes without error; creates table `tabProcess Value Change`. (Table fields add no column to `tabProcess`.)

- [ ] **Step 6: Write the schema smoke test**

In `apps/yrp/yrp/yrp/doctype/process/test_process.py`, replace the file body with:

```python
# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def _ensure_item_attribute(name):
	"""Return an Item Attribute name, creating it if absent (rolled back per test)."""
	if not frappe.db.exists("Item Attribute", name):
		frappe.get_doc({"doctype": "Item Attribute", "attribute_name": name}).insert()
	return name


class IntegrationTestProcess(IntegrationTestCase):
	def test_value_change_attribute_round_trips(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Smoke Process",
				"value_change_attributes": [{"attribute": attr}],
			}
		)
		proc.insert()
		self.assertEqual(len(proc.value_change_attributes), 1)
		self.assertEqual(proc.value_change_attributes[0].attribute, attr)
```

- [ ] **Step 7: Run the smoke test — expect PASS**

Run: `bench --site mgk_yrp.site run-tests --module "yrp.yrp.doctype.process.test_process"`
Expected: PASS (the field + child table exist; no validation yet). If it fails with an unknown-field error, the migrate in Step 5 didn't apply — re-run it.

- [ ] **Step 8: Browser-verify the form (per validation.md "verify in the desk UI")**

Run: `node .claude/hooks/pw-shot.mjs --url "/app/process/new?is_group=0"`
Confirm the screenshot shows a **"Transformation"** section with a **Value Change Attributes** grid. Then:
Run: `node .claude/hooks/pw-shot.mjs --url "/app/process/new" --eval "cur_frm.set_value('is_group',1)"`
Confirm the "Transformation" section is now **hidden** (proves the `!= 1` polarity). Report both screenshots.

- [ ] **Step 9: Checkpoint — stage for review (DO NOT COMMIT)**

Run: `git -C /home/anas/frappe-16 status --short apps/yrp`
Confirm only the four intended paths changed. Do **not** `git commit` — await user authorization (Global Constraints).

---

### Task 2: `Process.validate` — reject duplicate attributes and group-with-rows (TDD)

**Files:**
- Modify: `apps/yrp/yrp/yrp/doctype/process/process.py`
- Test: `apps/yrp/yrp/yrp/doctype/process/test_process.py`

**Interfaces:**
- Consumes: `self.value_change_attributes` (rows with `.attribute`), `self.is_group` (from Task 1).
- Produces: `Process.validate()` raising `frappe.ValidationError` on a duplicate attribute or on a group process carrying any `value_change_attributes`.

- [ ] **Step 1: Write the two failing tests**

Append these two methods to `IntegrationTestProcess` in `apps/yrp/yrp/yrp/doctype/process/test_process.py`:

```python
	def test_duplicate_value_change_attribute_rejected(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Dup Process",
				"value_change_attributes": [{"attribute": attr}, {"attribute": attr}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			proc.insert()

	def test_group_process_cannot_have_value_change_attributes(self):
		attr = _ensure_item_attribute("_Test PVC Colour")
		proc = frappe.get_doc(
			{
				"doctype": "Process",
				"process_name": "_Test PVC Group Process",
				"is_group": 1,
				"value_change_attributes": [{"attribute": attr}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			proc.insert()
```

- [ ] **Step 2: Run the new tests — expect FAIL**

Run: `bench --site mgk_yrp.site run-tests --module "yrp.yrp.doctype.process.test_process"`
Expected: the two new tests FAIL — `proc.insert()` succeeds (no validation yet), so `assertRaises` reports "ValidationError not raised". (`test_value_change_attribute_round_trips` still passes.)

- [ ] **Step 3: Implement `validate` in `process.py`**

Replace the contents of `apps/yrp/yrp/yrp/doctype/process/process.py` with:

```python
# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Process(Document):
	def validate(self):
		self.validate_value_change_attributes()

	def validate_value_change_attributes(self):
		if self.is_group and self.value_change_attributes:
			frappe.throw(
				_("A group Process cannot have Value Change Attributes; remove them or untick Is Group.")
			)

		seen = set()
		for row in self.value_change_attributes:
			if row.attribute in seen:
				frappe.throw(
					_("Attribute {0} is listed more than once in Value Change Attributes.").format(
						frappe.bold(row.attribute)
					)
				)
			seen.add(row.attribute)
```

- [ ] **Step 4: Run the tests — expect PASS**

Run: `bench --site mgk_yrp.site run-tests --module "yrp.yrp.doctype.process.test_process"`
Expected: all three tests PASS.

- [ ] **Step 5: Red-green proof the guards are real**

Temporarily comment out the body of `validate_value_change_attributes` (leave `pass`), re-run Step 4 — confirm the two guard tests FAIL again — then restore the body and re-run to confirm PASS. This proves the tests catch a regression, not tautologies.

- [ ] **Step 6: Compile + lint check**

Run: `python -m py_compile apps/yrp/yrp/yrp/doctype/process/process.py apps/yrp/yrp/yrp/doctype/process_value_change/process_value_change.py`
Expected: no output (success). Address any `advise-python-lint.sh` hook output.

- [ ] **Step 7: Checkpoint — stage for review (DO NOT COMMIT)**

Run: `git -C /home/anas/frappe-16 status --short apps/yrp`
Report the changed files. Do **not** commit — await explicit user authorization. When the user authorizes, the single commit is:

```bash
git -C /home/anas/frappe-16 add apps/yrp/yrp/yrp/doctype/process_value_change apps/yrp/yrp/yrp/doctype/process/process.json apps/yrp/yrp/yrp/doctype/process/process.py apps/yrp/yrp/yrp/doctype/process/test_process.py
git -C /home/anas/frappe-16 commit -m "feat(yrp): add value-change transformation metadata to Process"
```

---

## Self-Review (completed by author)

- **Spec coverage:** §4.1 field + §4.2 child DocType → Task 1; §5 validation (no-dup, group-empty; §5.3 stage-guard deliberately dropped) → Task 2; §8 migrate + browser + tests → covered. §6 (no matrix guard) and §1 out-of-scope (UI/engine/matrix in customization app) → intentionally NOT in plan.
- **Placeholders:** none — all code, paths, and commands are concrete.
- **Type/name consistency:** `value_change_attributes` (Table), child `Process Value Change`, child field `attribute`, `Process.validate`/`validate_value_change_attributes` — names match across Task 1 → Task 2 and the spec.
- **Open questions (spec §9):** item-conversion, per-stage granularity, per-item override are all deferred — correctly absent from this plan.
