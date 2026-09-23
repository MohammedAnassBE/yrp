# YRP Partner

## Records and configuration

`YRP Partner Type` selects a source DocType. Saving it backfills one `YRP Partner`
per source record. Source saves create any missing partner. Generated partners
cannot be inserted manually, and their users are derived from linked Contacts.
Configurations above 100 source records run after commit in pages of 50 through
Frappe's long worker. The form reports queued/running/completed/failed progress;
use **Restart Partner Generation** to retry a failed run. Each page checks the initiating user's current
write permission and the configuration revision. Older runs stop after settings
change. Completed pages are retained and reruns reconcile them idempotently.

Contacts use Frappe's standard **Links** table. A Contact with both email and mobile
has one User. Email is preferred. **Generate Email When Missing** permits a
mobile-only Contact to receive an address using **Generated Email Format**, for
example `{hash}+{mobile_no}@yrp.com`. The random prefix is generated once; subsequent
saves reuse the account. The email is stored in the Contact's normal email table.
Mobile normalization removes punctuation but does not infer a country code.

Different configured source types on one Contact must agree on the generated
email format. Conflicting existing email/mobile accounts are rejected instead of
being silently merged. New users receive the `YRP Partner` role. Passwords, OTPs,
welcome emails, and authentication bypasses are not created here; account creation
alone does not implement mobile OTP login.

## Lifecycle and account ownership

Contact updates refresh partners on both old and new Links. Removing an identity
can disable the old User only if it was created by this module and no other
Contact still links to that user, email, or phone. Removing a mobile also removes
its now-invalid generated email. Shared identities keep their user enabled.
Automatically disabled accounts can be reused; manually disabled accounts require
an administrator. Existing accounts are never automatically disabled.

Backfill locks each Contact before provisioning. ERPNext's User creation hook can
save that Contact inside the same transaction, so reconciliation reloads that
persisted result before saving any remaining identity changes. Unchanged Contacts
are not saved, and normal stale-document validation remains enabled.

Trusted YRP provisioning uses a private User mixin token to apply Frappe's import
exemption only around the native User `before_insert` throttle. This permits bulk
Partner Type backfills and subsequent linked Contact provisioning without changing
the site's normal User/sign-up limit. The import flag is restored even on failure;
User defaults, validation and lifecycle hooks retain their standard behavior.

Three hidden, read-only User markers record module ownership, generated email,
and automatic disablement. `setup.py` installs them through Frappe's Custom Field
API. Disabling uses a narrow database update plus session/cache/notification
cleanup: normal User saves enqueue Contact synchronization, which would recreate
a deliberately removed identity.

## Code map

- `users.py`: identity selection, email-format validation, provisioning and retirement.
- `sync.py`: idempotent Partner generation and Contact/source event handling.
- `backfill.py`: bounded, revision-checked background generation for large types.
- `workspace.py`: permission-filtered navigation derived from configured types.
- `setup.py`: repeatable installation of markers, roles, Address/Contact panels and permissions.
- `contacts.py`: native Address/Contact display payload for Sales Person, Employee and Retailer.
- `doctype/yrp_partner_type`: configuration validation and backfill controller.
- `doctype/yrp_partner`: generated-only record and membership validation; unique source index.
- `doctype/yrp_partner_user`: child table of generated memberships.
- `../public/js/partner_contacts.js`: Frappe's native Contact panel renderer.
- `../yrp_retail/doctype/yrp_retailer`: standard Retailer master with optional Customer link.

Changes belong in YRP; ERPNext/Frappe source is not patched. DocTypes are exported
through developer-mode DocType saves. Install/migrate hooks recreate integration
fields and management roles on another site.

## Roles

| Role | Purpose |
| --- | --- |
| System Manager | Configuration and administration, including Retailer deletion. |
| YRP Partner Manager | Create/edit Partner Types, Sales Persons, Customers, Retailers, Contacts and Addresses; read generated Partners. |
| YRP Retail User | Create/edit Retailers and Contacts; read Customers. No Partner Type configuration. |
| YRP Partner | Read related masters, Contacts and Addresses. No create/edit/delete; System Manager is the explicit bypass. |
| Sales Master Manager | Existing ERPNext role for Sales Person management. |
| HR Manager | Existing HR role for Employee management. |

Assign internal roles explicitly through normal User role management. Generated
users receive only YRP Partner, not internal management roles. Native Frappe
owner-based Contact permissions still apply. Changes to Contacts linked to a
configured source require write permission on that source to prevent users from
assigning themselves to arbitrary partners. Employee access is not expanded by
the new management roles. `permissions.py` applies membership scope to native list queries and individual
record permission checks. It includes configured source records, direct parent
Link fields, Customer-to-Retailer relationships, and linked Contacts/Addresses.
The membership lookup is live, so removing the last membership removes scoped
visibility immediately. Core masters with no membership return no scoped rows.

Frappe's explicit document sharing remains a read exception to membership scope.
Write lifecycle guards still reject partner writes, including permission-bypassing
saves, submission, cancellation and deletion. These hooks do not secure custom
endpoints that directly query SQL or use `get_all` and return data without checking
permissions; such APIs must use permission-aware reads. Partners with another edit
role are still read-only on protected records. System Manager bypasses the partner
restriction, with normal DocType permissions still required.

The standard forms display Addresses and Contacts, and partner users see them as
read-only. Sales Person and Employee panels are app-owned Custom Fields; Retailer
panels are standard fields on the new DocType. Customer retains its native panels.

## Tests and limits

Tests use fictional records and transaction rollback. They cover generation,
shared identities, checkbox behavior, account retirement, role boundaries and
Contact panel payloads. Deletion-hook tests invoke the registered lifecycle hook;
they do not claim end-to-end deletion validation across every historical table.
Run the modules with Frappe's normal site test runner on a disposable/test site.

## Retail API integration

The YRP Retail module adds narrow authenticated salesperson APIs for visits, retailer creation, retail orders and summaries. Generic Partner document writes remain blocked; each authorized API operation uses a server-only capability tied to the exact document. See [YRP Retail documentation](../yrp_retail/README.md) for roles, source locking, validation and endpoints.
