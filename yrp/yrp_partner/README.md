# YRP Partner

## Access flow

1. Configure a source DocType in **YRP Partner Type**, such as Sales Partner.
2. yrp generates one **YRP Partner** per source record.
3. An administrator or a separate custom app adds Users to the Partner's **Users**
   table. yrp never derives members from Contacts, emails or phone numbers.
4. yrp gives each member the **YRP Partner** role plus the role of the source type
   (see *Member roles*). The member's permitted lists and document reads are
   filtered to the linked business records.

YRP Partner never generates emails, creates Users, changes login mobile numbers
or enables/disables accounts. A custom app (such as essdee_yrp_partner) owns the
mapping of Contacts to Users.

## Records and synchronization

Saving a Partner Type generates one **YRP Partner** per source record. Source saves
create missing Partners. New IDs use the native `PR.#####` series (`PR00001`,
`PR00002`, ...); resynchronization preserves existing names. Partners cannot be
created manually. Their **Users** table is explicit and editable by users with write
permission. Generation never changes existing members, and Contact, source or User
saves never change membership. A User that is a member cannot be deleted until it
is removed from its Partners.

Configurations above 100 source records run after commit in pages of 50 through
Frappe's long worker. The form reports queued/running/completed/failed progress.
**Restart Partner Generation** retries a failed run. Each page checks the initiating
user's current write permission and the configuration revision. Older runs stop
after a configuration change. Backfills never save Contacts or Users.

The `remove_partner_provisioning` migration removes retired User Custom Field
metadata. The `grant_partner_member_roles` migration keeps the existing users as
explicit members and adds their missing member roles. It removes no role.

## Member roles

Saving or deleting a Partner updates the roles of the users that were added or
removed (`member_roles.py`). The update is idempotent and saves only the User:

| Source type | Roles given to a member |
| --- | --- |
| Customer | YRP Partner, YRP Customer |
| Sales Partner | YRP Partner, YRP Sales Partner |
| Sales Person | YRP Partner, YRP Sales Person |
| Any other type (e.g. YRP Retailer) | YRP Partner |

A removed member loses the roles of that Partner only when no other membership
of the user needs them. Other roles are never changed. Existing sessions may need to log in again after a role change.

## Roles and scope

| Role | Purpose |
| --- | --- |
| System Manager | Configuration and administration; explicit bypass of partner filtering. |
| YRP Partner Manager | Manage Partner Types, Sales Persons, Customers, Retailers, Contacts and Addresses; read generated Partners. |
| YRP Retail User | Manage Retailers and Contacts; read Customers. |
| YRP Partner | Desk access; read related records within ordinary DocType role permissions. |
| YRP Sales Person | With YRP Partner membership: create/edit own Visits; create/edit assigned Customers' Retailers; create/edit draft Retail Orders/Summaries, submit and cancel their own Orders/Summaries. |
| YRP Sales Partner | With YRP Partner membership: read their YRP Sales Orders, Delivery Notes and Packing Slips; create/edit draft YRP Sales Orders for their Customers. No submit, cancel or delivery processing unless a registered action extension allows it. |
| Sales Master Manager | Native ERPNext Sales Person management. |
| HR Manager | Native Employee management. |

Membership gives the partner roles (see *Member roles*). Other roles are assigned
through normal User management.

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

- `sync.py`: Partner generation for source records. No membership or account management.
- `member_roles.py`: roles that follow membership.
- `backfill.py`: bounded, revision-checked Partner generation.
- `permissions.py`: list filters, document checks and write guards.
- `setup.py`: read permission for newly configured dynamic Partner source types.
- `sales_roles.py`: action and document-state checks; fixed grants live in fixtures.
- `sales_order.py`: guards for native processing methods that bypass document saves.
- `workspace.py`: navigation derived from configured Partner Types.
- `contacts.py`: native Address/Contact display payload.
- `doctype/yrp_partner_type`: source configuration.
- `doctype/yrp_partner`: generated record, explicit members, role sync and unique index.

Tests use fictional records with rollback. They cover explicit membership, member
roles, shared access, account non-mutation, revocation, background generation,
naming and permission boundaries. Run them with Frappe's standard
test runner on a development/test site. Framework source files are not modified.

### Customer documents and ledgers

Add an existing enabled User to the Partner of a record configured in YRP Partner
Type; the membership gives `YRP Partner` plus the business role. This feature
never creates Users.

| Business role | Customers available to financial reports |
| --- | --- |
| YRP Customer | Every Customer whose Partner lists the User |
| YRP Sales Person | Every currently assigned Customer across linked Sales Persons |
| YRP Sales Partner | Every Customer currently assigned to linked Sales Partners |

Several memberships and several business roles are combined. A Sales Person assignment is valid only while the person is enabled
and its Sales Partner agrees with the Customer's current Sales Partner. Removing
the membership, disabling a User, or removing the required role invalidates access.
A Retailer membership alone never grants its Customer's accounts.

Customer access is read-only. Existing Sales Person retail actions and Sales
Partner draft YRP Sales Order actions remain available under their own assignment
and document-state checks; Customer membership does not expand those actions.
YRP Sales Orders, Delivery Notes and Packing Slips carry partner read DocPerms in
their DocType; a Packing Slip follows its Delivery Note's Customer/company pair.
ERPNext Sales Orders, Delivery Notes, Packing Slips and Pick Lists grant partners
nothing. Sales Invoices, quotations and the customer-facing service/retail
documents have native role fixtures. Generic
Customer Link/Dynamic Link predicates also constrain additional DocTypes when
an administrator explicitly grants them. Internal production, banking and
multi-party accounting vouchers are not exposed simply because they contain a
Customer field. Native administrator-controlled document sharing remains a
separate Frappe read grant; financial reports always enforce Customer scope.

Available reports are General Ledger, Accounts Receivable, Accounts Receivable
Summary, YRP Retail Demand and YRP Retail Summary Allocation. Report roles remain native Custom Role
fixtures, preserving standard staff roles. Customer filtering happens before
balances, ageing and totals. General Ledger is filtered by `party_type=Customer`
and authorized party IDs, not by the shared receivable account. CSV/Excel
exports use the same live calculation; internal remarks and counterparty fields
are omitted. Prepared reports, custom report columns and background exports
are unavailable to scoped roles because cached staff results can contain other
Customers. No changes are made to the stored Report's prepared-report setting.

`PORTAL_REPORTS` adds the Essdee partner portal reports (order, packing and
invoice reports) for YRP Sales Partner only. They run their own `execute`, whose
reads are permission-checked lists, so rows follow the same Customer/company
scope; a Customer or Sales Partner filter outside it is refused.

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
