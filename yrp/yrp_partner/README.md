# YRP Partner

## Access flow

1. Configure a source DocType in **YRP Partner Type**, such as Sales Partner.
2. Link a Contact to a source record using Frappe's standard **Links** table.
3. An administrator or a separate custom app creates the login and assigns the
   **YRP Partner** role. YRP Partner does neither.
4. Each Contact email matching an existing enabled User with that role contributes
   membership to the generated Partner. The user's permitted lists and document
   reads are filtered to the linked business records.

All entries in the Contact's email table are considered, including secondary
emails. Multiple Contacts and sources can grant access to the same user. A phone
number or the Contact's `user` field alone does not grant membership. Administrator
and Guest are excluded from generated membership.

YRP Partner never generates emails, creates Users, assigns User roles, changes
login mobile numbers, or enables/disables accounts. Those operations belong to
normal User administration or a separate custom app. Native Frappe Contact/User
behavior remains in place, including filling Contact.user from a matching email.

## Records and synchronization

Saving a Partner Type generates one **YRP Partner** per source record. Source saves
create missing Partners. New IDs use the native `PR.#####` series (`PR00001`,
`PR00002`, ...); resynchronization preserves existing names. Partners cannot be
created manually and their membership table is derived, not manually editable.

Contact saves refresh both former and current links. Removing an email or link,
or deleting the Contact, removes only the memberships no longer supported by other
Contacts. Saving/renaming an existing User refreshes memberships after changes to
its email, enabled state or roles. These hooks only write derived Partner records.

Configurations above 100 source records run after commit in pages of 50 through
Frappe's long worker. The form reports queued/running/completed/failed progress.
**Restart Partner Generation** retries a failed run. Each page checks the initiating
user's current write permission and the configuration revision. Older runs stop
after a configuration change. Backfills never save Contacts or Users, so they
cannot create login accounts or cause Contact timestamp conflicts.

The `remove_partner_provisioning` migration removes retired User Custom Field
metadata and reconciles existing memberships. Existing queued backfills retain
their revisions and run the current membership-only synchronization.
It preserves existing User records, login IDs, roles, mobile numbers and enabled
states. Frappe retains retired columns until normal database maintenance.

## Roles and scope

| Role | Purpose |
| --- | --- |
| System Manager | Configuration and administration; explicit bypass of partner filtering. |
| YRP Partner Manager | Manage Partner Types, Sales Persons, Customers, Retailers, Contacts and Addresses; read generated Partners. |
| YRP Retail User | Manage Retailers and Contacts; read Customers. |
| YRP Partner | Desk access; read related records within ordinary DocType role permissions. |
| YRP Sales Person | With YRP Partner membership: create/edit own Visits; create/edit assigned Customers' Retailers; create/edit draft Retail Orders/Summaries, submit and cancel their own Orders/Summaries. |
| YRP Sales Partner | With YRP Partner membership: read their Sales Orders and Packing Slips; create/edit draft Sales Orders for their Customers. No submit, cancel, stock reservation or delivery processing. |
| Sales Master Manager | Native ERPNext Sales Person management. |
| HR Manager | Native Employee management. |

Assign roles through normal User management. The module defines its roles and base
DocType permissions; it does not add roles to any User. Changing access-bearing
Contact emails or Links requires write permission on the affected source records,
so owning a Contact does not allow a user to grant themselves arbitrary access.

Desk access uses Frappe's native Role setting and does not grant System Manager.
Role definitions, fixed Custom DocPerm rows and Custom Fields are core fixtures
declared in `hooks.py` and exported from a clean fixture-authoring site's database
with `bench --site <fixture-authoring-site> export-fixtures --app yrp`. Frappe performs normal fixture
sync; recurring after-migrate setup code does not recreate these configurations.
Custom apps can layer their configuration through Frappe's normal app ordering.
Existing sessions may require login again after a manual role assignment.

Custom DocPerm fixtures contain the complete native permission baseline plus YRP
grants: Frappe replaces a DocType's permission set when custom permissions exist.
Exact-name filters exclude company-specific permission rows. Export from a clean
site with the app fixtures installed; a copied business database such as `yrp.site`
can have unrelated native-role customizations and is not the source of those
baseline rights. `test_sales_fixtures.py` compares the export to native DocType JSON
and verifies importing onto an empty custom-permission set. The initial export
used a temporary database transaction for baseline normalization and preserved
the development site's unrelated role customizations.

`permissions.py` filters both lists and individual reads. It covers configured
source records, direct parent Link fields, Customer-to-Retailer relationships and
linked Contacts/Addresses. Standard Frappe DocType permissions are still required;
the existence of a link alone does not grant access to an otherwise forbidden
DocType. Explicit Frappe document sharing remains an administrator-controlled
read exception. System Managers bypass partner filtering.

Generic Partner writes remain blocked even with another edit role. The two sales
action roles grant the explicit exceptions above, subject to native DocPerm,
current membership, assignment and document state. Both Desk and retail APIs use
the same checks. A sales action role without YRP Partner membership grants no
business-record access. Retailers are shared by assigned Customers; visits/orders
remain associated with the acting Sales Person. Attendance is intentionally omitted.
Retail Orders now use native Draft → Submitted → Cancelled states. Existing draft
sources remain usable; no historical orders are automatically submitted. Cancel
the dependent Summary/Sales Orders first. Cancellation cannot edit quantities.
Custom SQL or `get_all` endpoints must enforce permissions themselves.

## Code and tests

- `sync.py`: email-derived membership, source/Contact/User lifecycle and Contact
  access-change validation. No account management.
- `backfill.py`: bounded, revision-checked Partner generation.
- `permissions.py`: list filters, document checks and write guards.
- `setup.py`: read permission for newly configured dynamic Partner source types.
- `sales_roles.py`: action and document-state checks; fixed grants live in fixtures.
- `sales_order.py`: guards for native processing methods that bypass document saves.
- `workspace.py`: navigation derived from configured Partner Types.
- `contacts.py`: native Address/Contact display payload.
- `doctype/yrp_partner_type`: source configuration.
- `doctype/yrp_partner`: generated record, membership validation and unique index.

Tests use fictional records with rollback. They cover email and role matching,
shared access, account non-mutation, revocation, source authority, background
generation, naming and permission boundaries. Run them with Frappe's standard
test runner on a development/test site. Framework source files are not modified.

### Customer documents and ledgers

Assign `YRP Partner` plus the applicable business role to an existing User. A
Contact email must match that enabled User, and its Dynamic Links must point to
records configured in YRP Partner Type. This feature never creates Users or
assigns roles.

| Business role | Customers available to financial reports |
| --- | --- |
| YRP Customer | Every Customer directly linked through the User's Contacts |
| YRP Sales Person | Every currently assigned Customer across linked Sales Persons |
| YRP Sales Partner | Every Customer currently assigned to linked Sales Partners |

Several Contacts, several links on one Contact, and several business roles are
combined. A Sales Person assignment is valid only while the person is enabled
and its Sales Partner agrees with the Customer's current Sales Partner. Contact
unlinking, disabling a User, or removing the required role invalidates access.
A Retailer membership alone never grants its Customer's accounts.

Customer access is read-only. Existing Sales Person retail actions and Sales
Partner draft Sales Order actions remain available under their own assignment
and document-state checks; Customer membership does not expand those actions.
Sales Orders, Delivery Notes, Packing Slips, Sales Invoices, quotations and the
customer-facing service/retail documents have native role fixtures. Generic
Customer Link/Dynamic Link predicates also constrain additional DocTypes when
an administrator explicitly grants them. Internal production, banking and
multi-party accounting vouchers are not exposed simply because they contain a
Customer field. Native administrator-controlled document sharing remains a
separate Frappe read grant; financial reports always enforce Customer scope.

Available reports are General Ledger, Accounts Receivable, Accounts Receivable
Summary, YRP Sales Order Fulfilment, YRP Packing and Delivery, YRP Retail Demand
and YRP Retail Summary Allocation. Report roles remain native Custom Role
fixtures, preserving standard staff roles. Customer filtering happens before
balances, ageing and totals. General Ledger is filtered by `party_type=Customer`
and authorized party IDs, not by the shared receivable account. CSV/Excel
exports use the same live calculation; internal remarks and counterparty fields
are omitted. Prepared reports, custom report columns and background exports
are unavailable to scoped roles because cached staff results can contain other
Customers. No changes are made to the stored Report's prepared-report setting.

Implementation references: [Frappe permission hooks](https://docs.frappe.io/framework/user/en/python-api/hooks),
[ERPNext General Ledger](https://docs.frappe.io/erpnext/general-ledger), and
[Accounts Receivable](https://docs.frappe.io/erpnext/accounts-receivable-and-payable).

For all three roles, Company access comes exclusively from the authorized
Customers' **Default Accounts** table (`Customer.accounts` / `Party Account.company`).
Configure these rows before the first sale; empty defaults grant no Company.
Credit limits, internal-company links, old transactions and site/user defaults do
not grant access. Multiple Customers combine their configured Companies, but a
document or financial report must still match its own Customer/company pair.
Removing or changing an assignment takes effect immediately. Company lists,
link searches, direct reads, official Company addresses and account selection
use this scope; financial reports require an explicitly authorized Company.
Desk's preloaded Companies and initial Company selection use the same scope;
this changes only the response, without rewriting saved User or site defaults.
Sales Partner draft creation checks that same assignment on the server.
Login audit logs and other framework Dynamic
Links are excluded from Customer business-document discovery. GL Entry rows
are read-only and require `party_type=Customer` plus an authorized Customer/company
pair, including rows posted from Journal Entries. Journal Entry, Payment Entry
and Payment Ledger Entry documents remain closed; their multi-party headers do
not become visible through a GL reference. Related Account metadata is read-only:
the Customer's receivable and advance defaults (including the native Customer
Group/Company account fallback within an explicitly assigned Company), plus accounts
on their authorized GL rows. A shared Account never grants another Customer's
ledger or balances. Internal aggregate/search APIs cannot bypass this boundary.
