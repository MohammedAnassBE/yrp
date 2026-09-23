# YRP Retail

Retail demand collection uses standard Frappe documents. It does not submit ERPNext sales, stock or accounting transactions.

## Records and lifecycle

- **Sales Person → YRP Sales Person Customers** lists the customers the salesperson may serve. Duplicate customers and assignments on group Sales Persons are rejected.
- **YRP Retailer** belongs to a Customer and can be assigned to a Sales Person. The mobile creation API always assigns the authenticated salesperson.
- **YRP Visit** records the salesperson, timestamp and GeoJSON point. Primary visits require a Customer; Secondary visits require an active Retailer and derive its Customer. The Customer must be assigned to the salesperson. An ordered visit cannot change its assignment, type or timestamp.
- **YRP Retail Order** records demand against one Visit. Its date and business parties come from that Visit. Each Visit has at most one order; `has_order` is maintained by the server. Items must be enabled, saleable concrete ERPNext Items with valid UOM conversions and positive quantities.
- **YRP Retail Order Summary** groups orders with the same Customer, Sales Person and Primary/Secondary type. Lines aggregate by Item and UOM. `company_qty = requested_qty - customer_stock_qty`; the customer allocation must be between zero and requested quantity. Whole-number UOM rules apply. This allocation represents demand supplied by the Customer, not a stock ledger movement.

Draft summaries immediately claim their source orders to prevent duplicate planning. Claimed orders cannot be edited or deleted. Summary headers and source membership are fixed after creation; customer allocations remain editable until submission. Cancellation or draft deletion releases claims. Use a new summary for a different set of orders. Source rows are locked during validation; conflicting concurrent database operations may require retrying the request.

## Access

System Manager and YRP Partner Manager administer retail records. YRP Retail User can create and edit retail operational records and submit summaries. System Manager/Partner Manager can cancel summaries; only System Manager can delete them.

YRP Partner remains read-only through generic document APIs and Desk, including when combined with editing roles (System Manager is the explicit exception). The narrow retail APIs below authorize a salesperson from generated Partner membership and check the current Customer assignment. Their document-specific write capability cannot be supplied through request data or reused for another document. Automatic Partner generation remains permitted internally.

Partner read filtering protects Visits, Orders and Summaries even if Partner Type configuration is removed. Assigned Customers are visible; another salesperson's Retailers are not exposed merely because the Customer is shared. Native explicit document sharing remains a Frappe read exception. Custom code using raw SQL/get_all must enforce its own permissions.

## Mobile endpoints

Prefix: `/api/method/yrp.yrp_retail.api.`. Use authenticated POST requests for mutations and GET for `list_records`. Normal Frappe CSRF/session or token authentication applies; no guest access is granted.

| Method | Business arguments |
| --- | --- |
| `create_retailer` | retailer_name, customer |
| `create_visit` | visit_type, visit_datetime, latitude, longitude, customer or retailer, optional notes |
| `create_retail_order` | visit, items, optional notes |
| `update_retail_order` | name, items, optional notes |
| `create_summary` | orders, optional customer_stock |
| `update_summary` | name, customer_stock |
| `submit_summary` | name |
| `list_records` | doctype, optional start/page_length |

All endpoints accept an optional `sales_person` to select among the current user's memberships; it never authorizes an unrelated salesperson. Without it, exactly one active leaf Sales Person is required.

Order items contain exactly `item_code`, `qty`, `uom`. Summary allocations contain exactly `item_code`, `uom`, `customer_stock_qty`. `orders` is a nonempty list of unique Retail Order names. Arrays may be passed as JSON strings. Mutation responses identify the saved document; submission also returns docstatus. Failed API mutations roll back to their savepoint.

## Tests

`test_retail_flow.py` creates fictional Customers, Sales Persons, Users, Contacts, Items and UOMs and rolls them back. It covers target validation, ownership, revoked assignments, location, quantity/UOM rules, duplicate visits, summary allocation/claims/cancellation, automatic Partner generation and rejection of forged write scopes. Existing Partner/User/Permission/Retailer tests cover the shared integration.

Validation on local yrp.site: 61 Customer stock, retail and partner tests passed; the 11 Customer stock tests also passed after final permission tightening. Browser form verification remains incomplete because the temporary login failed; this is not a completed mobile-app integration. Independent high-model review was completed and its fixes applied; GLM 4.3 returned Unknown Model and the authorized 5.3 fallback produced no review before timeout. No production site or company fixtures are required.

## Customer stock balances

Set **Retail UOM** in **YRP Retail Settings** before using Customer stock. Each Customer/item has one shared **YRP Customer Stock** balance, irrespective of which assigned salesperson reports it. Reporting replaces the quantity; it does not add to it. Balances use that UOM as-is, without ERPNext stock conversion, warehouse entries or a ledger. The UOM cannot be changed while balances exist, including zero balances, to prevent silently relabelling quantities.

Authenticated salesperson endpoints:

- POST `update_customer_stock(customer, item_code, qty, sales_person=None)` replaces the count.
- GET `get_customer_stock(customer, item_code, sales_person=None)` returns `qty`, `uom`, `recorded`, `last_counted_at` and `last_counted_by`.

Both require the Customer to be currently assigned to the salesperson. Only enabled concrete sales Items are accepted. Negative, nonfinite and invalid whole-number quantities fail. An unreported balance returns zero with `recorded=false`; a reported zero has `recorded=true`. The timestamp remains the last physical count even after consumption, enabling a future UI to show stale counts.

Summary **submission**, not draft creation/editing, deducts each Customer allocation once. For example, balance 6 boxes and allocation 10 boxes leaves 0 boxes. Missing counts do not block allocation. The allocation is still bounded by requested demand; it is not bounded by the recorded balance. Customer stock and the summary update in one transaction. Orders use the configured Retail UOM when it is set.

Cancelling a summary does **not** put goods back into a Customer's physical balance; report a corrected count if needed. An amended or newly submitted summary is a new allocation and will consume again. This simple balance model deliberately does not infer physical returns from document cancellation.

Tests in `test_customer_stock.py` cover replacement, clamping, missing counts, latest counts, repeated submission, rollback, UOM safety, authorization and cancellation. UI design remains a separate phase.

## Item categorization and template sales defaults

The existing **YRP Item Category** is now a native Frappe tree: **Item Type → Category → Value**. For example: Mobile → Storage → 128 GB. Classification is independent of ERPNext variant attributes and does not generate variants. Names may repeat in different branches, but sibling labels must be unique.

Items use `yrp_item_type` and the `yrp_categories` child table; YRP Item Master Template uses `item_type` and `categories`. Each row chooses one Value belonging to its Category, under the selected Item Type. New Categories and Values may always be added, including after Items use the tree. Existing nodes cannot be deleted, reparented or moved to another level. Labels, ordinary renaming and disabling remain available. New assignments cannot use disabled nodes; existing assignments remain valid. Category merges are rejected. Selecting an Item Type on YRP Item Master Template prefills its enabled Categories. Users select each Value; created Items inherit those selections. Adding a later season does not rewrite existing Item selections.

**YRP Item Master Template** now holds native **Item Tax** and **Item Default** tables plus `is_free_item`. Item Defaults supports company-wise income/expense accounts and the other standard ERPNext defaults. Creating an Item establishes `yrp_item_master_template`; linked Items inherit the classification, free flag, tax mappings and company defaults. Template changes synchronize those fields through native Item saves in the same transaction. Empty template tables remain empty instead of silently falling back to Item Group defaults. Existing unlinked Items are not guessed or backfilled. Historical orders and invoices are not rewritten.

ERPNext supplies native tax templates and calculations. India Compliance adds India-specific GST behavior; it is not the source of ERPNext's general tax capability. Updating the referenced Item Tax Template changes the definition used in future native calculations; changing a YRP master template's tax links synchronizes linked Item masters. HSN/SAC is configured on the Item or Product, not on YRP Item Master Template. Creating an Item still runs its installed compliance validation, and Product retains its required HSN/SAC field. Selecting a template supplies structural defaults without overwriting a Product's HSN/SAC.

## Standard pricing

Use ERPNext **Price List** and **Item Price**, including its native defaults and currency/UOM/date selection. No parallel YRP price database or SD price logic is introduced. The custom price-management UI and schemes are future work.

For template-managed Items, selling Item Prices must be positive unless `is_free_item` is enabled, in which case they must be exactly zero. Free rows are normalized to zero immediately before native Sales Order, Delivery Note and Sales Invoice totals calculations; effective rates are checked afterward. Paid rows cannot become zero through a 100% discount. Purchase pricing and legacy unlinked Items are outside this policy.

Existing incompatible selling prices prevent changing the free flag, including dated prices. Remove the incompatible prices through an authorized workflow before switching the flag, then create valid prices; the module does not silently rewrite price history. The template link cannot be removed to evade the policy. Item merges across different template policies and Item Master Template merges are rejected; ordinary renaming and like-policy Item merges retain native behavior. Customer-balance merging is not implemented by this feature.

A transaction-scoped pricing mutex serializes policy-sensitive saves and calculations. It prioritizes consistency over throughput. Template/Item saves acquire it before native save locks; raw SQL writes remain outside application validations. Concurrency reasoning has been reviewed, but a two-connection load test is not claimed.

Tests: `test_category.py`, `test_item_template_sales.py`, `test_pricing.py`, `test_pricing_integration.py`, and the existing template creation suite use fictional fixtures or isolated calculation inputs. Native calculation tests do not submit stock/accounting transactions. Category and Item UI tests are not yet claimed.

Prior validation for categorization/template pricing: 106 distinct tests passed across the focused suites and existing retail/partner regressions (including the final permission and stale-source merge checks). All database fixtures were rolled back. Independent review findings were fixed and checked. Browser/custom-UI and multi-connection load testing are not claimed. The local category table was empty before its in-place tree upgrade; sites with legacy flat category records need an explicit category mapping before using the new hierarchy.

Categorization correction: whole-tree locking removed; existing nodes cannot be deleted, reparented or change level, while new branches and values remain allowed. The obsolete lock field was removed from DocType metadata. 16 category/template integration tests and 3 native-form event tests passed for this revision. Form event tests use a simulated Frappe context, not a browser session.

## Retail sources to native sales documents

A Primary YRP Retail Order creates a native Sales Order directly. Secondary orders must first belong to a submitted Secondary YRP Retail Order Summary; its `company_qty`, after the Customer stock allocation, is the quantity available for Sales Orders. Primary orders cannot be claimed by summaries. Sales Orders retain a source header link and exact source child-row links; repeated Items therefore remain distinguishable.

`ordered_qty` and `per_ordered` are server-computed on the source. Both draft and submitted Sales Orders count, so two drafts cannot claim the same remaining quantity. Cancellation or deletion releases the allocation. Partial creation is supported. An active allocation prevents changing its source Customer, item rows or quantities, and a linked Sales Order cannot switch or detach its source. Native submitted Sales Order **Update Items** also validates capacity and refreshes progress.

Company processing uses native document permissions. YRP Partner users remain read-only on Sales Order, Delivery Note, Sales Invoice and Packing Slip even with Sales/Stock editing roles; Administrator and System Manager retain their existing exception. These authenticated POST endpoints return draft documents and do not automatically submit stock or accounting transactions:

| Method | Arguments |
| --- | --- |
| `yrp.yrp_retail.sales_sources.make_sales_order` | `source_doctype`, `source_name`, `company`, `delivery_date`; optional `items` (`source_row`, `qty`) and `selling_price_list` |
| `yrp.yrp_retail.fulfilment.make_delivery_note` | `customer`, `sales_orders`; optional `items` (`sales_order_item`, `qty`) |
| `yrp.yrp_retail.fulfilment.make_sales_invoice` | `delivery_note` |

Sales Orders use native pricing/defaults and require a real delivery date. Multiple compatible submitted Sales Orders for one Customer can map into one draft Delivery Note. Company, currency, pricing, tax rows, percentage discounts and address settings must agree. Multi-order consolidation rejects fixed taxes, frozen taxes and fixed additional discounts until an explicit allocation policy is defined; process those orders separately. Native order-row links are retained. Draft Delivery Notes do not introduce a separate order-quantity reservation mechanism; native submission validations continue to govern delivery quantities.

A submitted, non-return Delivery Note maps to a draft Sales Invoice using its row prices. `update_stock` is off to avoid issuing the same goods again through the invoice. Existing stock guards remain in place: this phase does not choose or override the YRP/ERPNext stock engine. Tests use fictional non-stock Items to exercise native submission without deciding that integration.

## Cartons and physical delivery

Use native **Packing Slip** for carton packing, not warehouse Pick List. ERPNext creates/submits Packing Slips against a **draft** Delivery Note; pack first, then submit the Delivery Note through the configured stock workflow. Each slip keeps native `from_case_no` / `to_case_no` and exact `dn_detail` references. Packing quantities follow the Delivery Note row UOM. Draft and submitted slips both reserve carton numbers and packing quantities, preventing overlapping ranges and overpacking.

Authenticated POST endpoints in `yrp.yrp_retail.packing`:

- `create_carton(delivery_note_name, case_no, items)` creates a draft single-carton Packing Slip; each item contains `dn_detail` and `qty`.
- `mark_packing_slip_delivered(name, delivered=True)` marks or unmarks a submitted slip against a submitted Delivery Note.
- `mark_delivery_note_delivered(name)` marks all submitted slips only when they cover every Delivery Note row completely.

A single-carton slip can be delivered independently. A slip representing a carton-number range is delivered as a group. Native Packing Slip packing progress remains native; custom `yrp_delivered_qty` and `yrp_per_delivered` describe physical delivery without changing native stock/accounting status. Directly editing derived counters cannot forge completion. Delivered slips/notes must be unmarked before cancellation; whole-note marking is atomic and checks every slip's write permission. Product Bundle packing retains native support, but these physical-delivery APIs reject bundles until a component-to-bundle quantity policy is defined.

Source mapping, carton operations and invoice creation use savepoint rollback on failure. Pricing/source/carton locks follow a consistent order; the global policy mutex favors correctness over concurrent throughput. Custom frontend, print formats, stock-engine changes, ST migration and accounting extraction remain separate phases.

Validation for this sales-flow phase: **58 focused tests passed** (13 source/capacity tests, 7 carton tests, 4 native fulfilment tests, 14 retail regressions, 11 Customer stock regressions and 9 Partner permission tests). Native submitted Sales Order Update Items, partial allocations, cancellation/deletion release, duplicate-item carton row identity, delivery undo/full coverage, invoice rate retention, mixed-tax rejection and extra-role write denial are covered. All fictional database fixtures were rolled back on local `yrp.site`; production was not accessed. Python compilation and diff whitespace checks passed. Browser/mobile, stock-engine posting and concurrent-load tests are not claimed.

## Desk form actions

The shared `public/js/retail_sales_flow.js` is registered through native `doctype_js` hooks. Visit selection fetches its Customer, Sales Person, retailer, type and date into Retail Order's read-only header, ignoring stale asynchronous responses. Saved Visits offer Create → Retail Order. Saved Primary orders offer Create → Sales Order with Company, Delivery Date, optional Price List and remaining source-row quantities. Secondary orders offer Create → Retail Order Summary; add any other matching source orders before saving. Summaries are explicitly Secondary, calculate demand on save, allow Customer supply quantities, and offer Sales Order creation after submission.

Draft Delivery Notes offer Create → Carton for a numbered native Packing Slip. Submit the Packing Slip before the Delivery Note. Submitted slips expose Mark Delivered / Undo Delivery; submitted Delivery Notes expose Mark All Cartons Delivered, subject to server full-coverage checks. Native Sales Order → Delivery Note and Delivery Note → Sales Invoice actions remain available. Partner users do not gain processing buttons through additional editing roles. These buttons call the existing permission-checked APIs; hiding a button is not used as a security boundary.

Desk verification (2026-09-22): Selenium exercised normal form entry and Save/Create/Submit actions on local `yrp.site` with fictional master/visit fixtures. The Primary path saved an order, created/submitted its Sales Order, created a Delivery Note and numbered Packing Slip, submitted delivery, marked the carton delivered, displayed 100% physical delivery and saved a draft invoice retaining the Delivery Note price. The Secondary path saved a summary with demand 10, Customer supply 4 and company quantity 6, then created its Sales Order and displayed 100% ordered. Test Items were non-stock; the run produced no Stock Ledger or GL entries. This verifies the Desk flow, not the pending stock-engine integration or mobile frontend.

The browser run exposed two additional fixes: related cached documents are marked for native refresh without discarding unsaved edits, and generated Sales Orders clear inherited currency/conversion defaults before native Customer/selected-Company default resolution. Seven focused form tests and three relevant backend checks passed, including the cross-Company currency regression. Use native ERPNext operational roles, including Sales User for Price List access; a Manager role alone does not necessarily grant every master-data permission. Existing copied-site S3 logo/favicon errors are unrelated to this flow and were not changed.

After browser verification, all manifested fictional masters and transactions were removed through native cancellation/deletion, including their test-only repost records. The previous Retail UOM setting was restored and verified; temporary browser credentials were deleted. Screenshots and the run record are outside the repository under `/tmp/st-sales-pilot/`.

## Sales and retail reports

Four standard Script Reports in YRP Retail provide source-row detail:

- **YRP Retail Demand**: Primary/Secondary demand by Customer, Retailer, Sales Person, Item and transaction UOM; Sales Order allocations include drafts.
- **YRP Retail Summary Allocation**: requested demand, Customer supply, company demand, allocated and remaining company quantities. Date means summary period end, not submission date.
- **YRP Sales Order Fulfilment**: native ordered, ERP delivered and remaining delivery quantities. Native delivery is not proof of physical receipt; closed orders retain any numerical shortfall.
- **YRP Packing and Delivery**: carton range, packed, physically delivered and outstanding quantities. Date filter means Packing Slip creation date; physical delivery timestamp is a separate column. Draft packing is planned, not completed. Cancelled Delivery Notes are excluded.

All reports require a date range, exclude cancellations, support Item/UOM and Customer filters, and omit mixed-UOM grand totals. Submitted documents are the default except Retail Orders, which are non-submittable. Include Drafts is explicit. Native permission-filtered parent queries and document checks apply; packing additionally requires Delivery Note access. These are current-state reports, not historical as-of snapshots. No accounting amounts or stock ledger balances are inferred.

Research reference: [ERPNext Sales Reports](https://docs.frappe.io/erpnext/sales-analytics). Reuse native Sales Analytics, Accounts Receivable and target reports for their existing business questions; these YRP reports cover the additional retail-source and carton-delivery workflow. Native reports require their own permission and UOM review before being offered to partners.

Report verification (2026-09-22): nine focused tests passed (six row/query policy tests and three native fixture tests). Coverage includes separate UOM rows, summary remainder, physical-delivery submission gates, cancellation/draft query policy, inaccessible linked Delivery Notes, real Sales Order allocation/deletion, required dates and revoked Partner membership. All four reports also executed successfully through `frappe.desk.query_report.run` on local `yrp.site`; that smoke check returned empty rows after fictional fixtures were rolled back. Report-specific browser rendering/export has not been tested. Report metadata was installed only on `yrp.site`; no production access or server restart.

## Retailer Customer and Sales Partner validation

A Retailer requires a Customer. `sales_partner` is a read-only Link fetched from native `Customer.default_sales_partner`; `before_validate` derives it again for Desk, imports and APIs. Missing Customer partner assignments remain blank rather than being guessed. Customer partner changes or removal synchronize existing Retailer display links in the same transaction. The upgrade patch backfills existing links without inventing Customers for legacy orphan Retailers; those records need a Customer before their next save. Existing visit-based restrictions on changing a Retailer's Customer/Sales Person remain.

Partner permission conditions deliberately ignore the Retailer copy and evaluate the live Customer relationship. A stale or forged copy therefore cannot preserve the previous Sales Partner's access. Existing separately authorized Sales Person/Customer/Retailer memberships and explicit document shares remain valid read paths; partner write restrictions are unchanged.

Verification: four new native-record tests and 27 existing retail/partner/retailer regressions passed on local `yrp.site`; fictional fixtures were rolled back. New coverage includes mandatory Customer, server overwrite of forged partner, synchronization on reassignment/removal, list/direct-read parity, stale-copy denial and read-only partner behavior. Browser autofill is configured through native `fetch_from`; browser interaction has not been verified in this revision.

Fixture limitation: the new Sales Partner tests skip the initial Partner Type scan of copied business records and exercise normal generation on newly created fictional records. An exploratory full scan encountered an existing Contact timestamp conflict during user provisioning; full legacy Contact provisioning is not validated by these tests.
