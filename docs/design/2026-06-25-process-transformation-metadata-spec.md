# Design Spec — Strengthen the `Process` DocType with transformation metadata

- **Date:** 2026-06-25
- **App:** base `yrp` (generic — every adopter benefits)
- **Status:** Draft, awaiting user review (revised after adversarial review + scope clarification)
- **Author:** co-designed with Mohammed Anas (discussion session, 2026-06-25)

---

## 1. Goal & scope

Make the `Process` master "strong": give it the reusable, item-generic **metadata** that declares
*which attributes a process is allowed to change the value of*. This metadata is the **input that
drives the customization app's auto-generation of the `IPD Process Matrix`** — users never hand-author
matrices.

**The end-to-end flow this metadata enables (consumer is the customization app, future):**

1. Base `yrp` `Process` carries `value_change_attributes` (e.g. Piece Dyeing → `Colour`).
2. The customization app (`mgk_clothing_yrp`) shows a **process-driven UI**: user selects a process
   (e.g. *Piece Dyeing*).
3. The UI reads the process's `value_change_attributes`, then shows the item's **existing values** for
   that attribute (all current Colours).
4. The user enters the **alternative value** (white → black).
5. The app **auto-generates / updates the `IPD Process Matrix`** from that input.

**In scope (this phase — base `yrp` only):**
- Add `value_change_attributes` (the metadata list) to the `Process` master.
- A new child DocType to hold it.
- Validation on the `Process` master for that list.

**Out of scope (future, in the customization app — the user's own work):**
- The process-driven customization UI (select process → show existing values → enter alternatives).
- The `IPD Process Matrix` auto-generation logic that consumes this metadata.
- Any change to `ipd_engine.py`.
- Fleshing out item-conversion (`input_item`/`output_item`) — see §9 Q1.

Base `yrp` provides the **metadata**; the **customization app consumes it** to generate the matrix and
the UI. (Base-vs-custom split per conventions.md 2026-05-23.)

---

## 2. Background — where transformation lives today (verified 2026-06-25)

The `Process` master is a thin catalog. Full field list: `process_name`, `is_item_conversion`,
`is_group`, `is_manual_entry_in_grn`, `input_uom`, `default_wastage`, `default_excess`,
`default_lead_time_days`, `output_uom`, `wo_excess_allowed_percentage`, `process_details`.
`process.py` is empty (`class Process(Document): pass`).

- `is_item_conversion`, `input_uom`, `output_uom` are **never read by any logic** (`input_uom`/
  `output_uom` are written once by `yrp_essdee` demo_data and never consumed; `is_item_conversion` is
  only an `insert_after` anchor in an mgk fixture). Only `wo_excess_allowed_percentage` has live logic
  (GRN, `goods_received_note.py:712`).
- `is_group` + `process_details` (sub-process bundling) have **no consuming logic** in yrp today.

The three transformation axes already exist, but **per-IPD**, not on `Process`:

| Axis | Today's home | Mechanism |
|---|---|---|
| Stage change | `IPD Process` child (on `Item Production Detail.ipd_processes`) | `in_stage` / `out_stage` → Link *Item Attribute Value* (`ipd_process.json:10-11`) |
| Attribute-**set** change | derived | `Stage` is each item's **dependent attribute**; `Item Dependent Attribute Mapping` (`mapping` child) declares *which attributes apply at each stage value*, so advancing the stage drops/adds attributes |
| Attribute-**value** change | `IPD Process Matrix` (per-IPD) | `combinations` / `combination_attributes`: Input-side vs Output-side `attribute_value` per `group_index` |

The engine `yrp/yrp/utils/ipd_engine.py` (`get_process_io`, `calculate_major_deliverables`) applies all
of this and stamps the dependent (Stage) attribute onto built variants from the `IPD Process` stage
pair. `IPD Process Matrix.validate_attributes_belong_to_ipd` (`ipd_process_matrix.py:21-31`)
deliberately **forbids** the dependent (Stage) attribute inside a matrix — the engine injects stage
from `IPD Process`, reading the IPD's `dependent_attribute` to know which attribute that is.

**Key fact:** `Stage` is a single `Item Attribute` whose values are `Cut / Piece / Pack / …`, wired as
each item's `dependent_attribute`. A process's stage transition **differs per item** (Printing at Cut
for one item, Piece for another) — which is why `in_stage`/`out_stage` live on the per-item
`IPD Process` child.

---

## 3. The decided split — `Process` (generic) vs `IPD Process` (per-item)

| Lives on… | Holds | Why |
|---|---|---|
| **`Process`** (master, item-generic) | **which attributes' *values* may change** (+ existing `is_item_conversion`) | Same for every item that runs the process — "Dyeing may change Colour" is universal → declare once; it drives the customization UI |
| **`IPD Process`** (per-item child, *unchanged*) | `in_stage → out_stage` | Stage is item-specific (Printing at Cut for item 1, Piece for item 2) → must stay per-item |

**Derivations (no new storage on `Process`):**
- *Attribute-**set** change* is derived **per item** from that item's `IPD Process` stage pair + its
  `Item Dependent Attribute Mapping`. `in_stage == out_stage` → no set change; stage advance → the
  mapping drops/adds attributes. *(Confirmed sound by review.)*
- *Many → one collapse* (sleeve=red + front/back=black → Colour=black) is **realized in the generated
  `IPD Process Matrix`** (N input combos + 1 output combo per `group_index`; the winning value is the
  alternative the user entered in the customization UI). The `Process` list does **not** encode which
  value wins — it only **flags** that Colour may change. (Corrects an earlier draft that wrongly called
  the collapse "emergent" from `Process` metadata.)

**`value_change_attributes` is stage-agnostic and process-global** — it does not say which stage the
value changes at. Piece Dyeing (Piece→Piece) and Stitching (Cut→Piece) both yield
`value_change_attributes = [Colour]`; their difference lives entirely in the per-item `IPD Process`
stage pair.

So the only **net-new** thing on the `Process` master is the *value-change attribute list*.

---

## 4. `Process` master changes

### 4.1 New field on `Process`

| Field | Type | Notes |
|---|---|---|
| `value_change_attributes` | Table → **`Process Value Change`** | Attributes whose value this process *may* change. Empty = no value change (pure handling/stage move). |

Placement: a new **"Transformation"** `Section Break` + this field, shown only for non-group processes
via **`depends_on: "eval:doc.is_group != 1"`**. ⚠️ This is the **inverse** polarity of the existing
Sub-Processes section (`section_break_details`/`process_details` use `eval:doc.is_group == 1`). Do
**not** copy that expression verbatim — `depends_on` is a *show* condition, so `!= 1` is required to
hide it for groups.

### 4.2 New child DocType: `Process Value Change`

- **DocType name:** `Process Value Change`
- **Folder / module path:** `apps/yrp/yrp/yrp/doctype/process_value_change/` (folder basename ==
  `frappe.scrub("Process Value Change")` == `process_value_change`, per the 2026-05-16 folder/name rule)
- **`istable: 1`** (child table), parent = `Process`
- **Structure precedent:** mirror `ipd_matrix_attribute.json` (istable child, single `attribute` Link).

| Field | Type | Reqd | Notes |
|---|---|---|---|
| `attribute` | Link → `Item Attribute` | yes | the attribute whose value may change (e.g. Colour). `Item Attribute` resolves to the **yrp** DocType, same as every other yrp child's Link. |

One column, deliberately minimal. "May change, not must": listing an attribute means its value is
*allowed* to change; the actual per-item alternative is supplied in the customization UI (mapping a
value to itself = no change).

### 4.3 Semantics

- An attribute **listed** → its value **may** change for this process → the customization UI offers it
  for remapping.
- An attribute **not listed** → its value is **preserved** (the customization UI won't offer it).
- Item-generic: a listed attribute a given item doesn't carry is simply inert for that item.
- Numeric/range attributes (e.g. Size) are treated as **opaque values** here — value-to-value mapping,
  no range arithmetic.
- Listing the **Stage** (dependent) attribute is pointless but harmless — the engine handles stage via
  the `IPD Process` stage pair and strips the dependent attribute from value matching. We therefore do
  **not** add a (structurally impossible) base-yrp guard for it — see §5.

---

## 5. Validation (on `Process`, in `process.py`)

`process.py` is currently empty. Add a `validate`:

1. **No duplicate attributes** — an `Item Attribute` may appear at most once. Dedup on the canonical
   Link name (Link values are canonical DocType names, so no case/whitespace ambiguity). Blank rows are
   caught by the child field's `reqd: 1`.
2. **Groups carry none** — if `is_group == 1`, `value_change_attributes` must be empty. *(Forward-looking
   convention; `is_group` has no consuming logic today.)*

**Not validated:** a "Stage attribute excluded" guard — dropped. The `Process` master is item-agnostic
and has no anchor to know which attribute is the dependent/stage one (that's per-item on
`Item Production Detail.dependent_attribute`). Hard-coding the literal "Stage" would breach the base-app
generic rule (conventions.md 2026-05-22), and a stray stage row is harmless (§4.3).

---

## 6. Relationship to `IPD Process Matrix` — no divergence risk

`Process.value_change_attributes` is the **source**; the per-item `IPD Process Matrix` is **generated
from it** (by the customization app, future). Because the matrix is *generated* from the metadata —
never independently hand-authored — the "two sources of truth could diverge" concern raised in review
**does not apply**: the metadata is upstream of the matrix by construction.

This also means **no matrix-governing guard is needed in base `yrp`.** Earlier drafts floated adding a
base-yrp validation that "a matrix may only remap declared attributes"; that's unnecessary (consistency
holds by construction) and would put consumption logic in the wrong layer. The matrix-generation logic
lives in the customization app, which reads this metadata as its input.

---

## 7. Worked examples

| Process | `value_change_attributes` (master) | `IPD Process.in_stage → out_stage` (per item) | reads as |
|---|---|---|---|
| Cutting | — | (none) → Cut | attributes appear (derived) |
| Stitching | Colour | Cut → Piece | set change (Panel dropped, derived) **+** Colour value change; per-panel colours collapse to one (collapse target = the alternative entered in the UI, stored in the generated matrix) |
| Piece Dyeing | Colour | Piece → Piece | only Colour's value may change |
| Ironing | — | Piece → Piece | nothing changes (pure handling) |
| Printing (item 1) | Colour | Cut → Cut | value change at Cut |
| Printing (item 2) | Colour | Piece → Piece | same process, value change at Piece (stage differs per item) |
| (Pack step) | — | Piece → Pack | attribute **added** at advance; its first value is authored on the generated matrix's output side, not from this list |
| (multi-attr) | Colour, Finish | Piece → Piece | a process may list **>1** changeable attribute |

Dyeing and Stitching share `value_change_attributes = [Colour]`; the list is stage-agnostic, so their
difference lives only in the per-item stage pair (§3).

---

## 8. Implementation outline (for the plan phase — base `yrp` only)

1. Create child DocType `Process Value Change` (`process_value_change.json` istable + `.py` stub +
   folder), mirroring `ipd_matrix_attribute.json`.
2. Add `value_change_attributes` Table field + "Transformation" `Section Break`
   (`depends_on: "eval:doc.is_group != 1"`) to `process.json`.
3. Implement `Process.validate` (§5) in `process.py`.
4. `bench --site <active-site> migrate` — pure model/schema sync (creates `tabProcess Value Change`; no
   column on `tabProcess`, Table fields are parent-side virtual). **No data patch, no backfill.** Active
   site this session = `mgk_yrp.site` (base yrp is installed there); use
   `.claude/state/current-site.txt` rather than hard-coding.
5. Load the `Process` form in the browser to confirm the "Transformation" section + child grid render
   only for non-group processes.
6. Tests (`test_process.py`, red-green each): duplicate-attribute rejection; group-must-be-empty.

No engine/UI/matrix wiring in base yrp — that's the customization app's future work.

---

## 9. Open questions (resolve before/at plan time)

1. **Item conversion** — leave `is_item_conversion` as the existing (never-read) flag, or flesh out
   `input_item`/`output_item` now for the Yarn → Cloth case? *Recommendation: defer.*
2. **Per-stage value-change granularity** — confirm a process never needs "Colour changes at stage X but
   not stage Y" on the `Process` master (that per-stage truth would live in the generated matrix). *Assumed
   not needed; the master list is process-global.*
3. **Per-item override** — do we ever need `IPD Process` to override the master's
   `value_change_attributes` for one item? *Recommendation: generic only for now (YAGNI).*
