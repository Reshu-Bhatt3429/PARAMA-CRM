# WhatsApp Integration

Last verified: 2026-08-05

## Overview

This CRM uses the `frappe_whatsapp` app to receive Meta WhatsApp Cloud API
webhooks and store `WhatsApp Message` records. CRM hooks then match each
incoming sender to an existing Contact, Lead, or Deal. If a valid incoming
number is unknown, CRM automatically creates a Lead and links the message to
it.

```text
Meta webhook
  -> frappe_whatsapp creates an Incoming WhatsApp Message
  -> CRM normalizes the sender to E.164
  -> existing Contact/Lead/Deal: link the message
  -> unknown valid sender: create and link one CRM Lead
```

Outgoing messages do not create Leads.

## Callback

The callback route is:

```text
https://<public-host>/api/method/frappe_whatsapp.utils.webhook.webhook
```

Meta Dashboard must use the exact Webhook Verify Token saved on the active
WhatsApp Account. Verification sends `hub.mode`, `hub.verify_token`, and
`hub.challenge`. A `No matching WhatsApp account` response means the token was
missing or did not match an account; it does not mean the route is absent.

For production, expose Frappe through a stable HTTPS domain. A development
tunnel works only while its process, hostname, and account quota remain active.
At the last verification, the configured ngrok hostname was returning
`ERR_NGROK_725` because its monthly bandwidth was exhausted. Local webhook
verification and POST processing continued to work correctly.

## Lead mapping

| WhatsApp value | CRM Lead field | Behavior |
|---|---|---|
| `from` | `mobile_no` | Normalized to E.164 with a leading `+` |
| Contact profile name | `first_name` | Trimmed to 140 characters |
| Missing profile name | `first_name` | Uses `WhatsApp Lead ####` |
| Integration origin | `source` | `WhatsApp` |
| Default CRM state | `status` | Normally `New` |

The implementation uses a Redis lock per normalized phone number and repeats
the CRM lookup while holding the lock. The lock remains held until transaction
commit or rollback, preventing concurrent webhook deliveries from creating
duplicate Leads for the same new sender.

Lead creation is best-effort. Errors are written to Frappe Error Log without
rejecting the stored incoming message. Administrators can reprocess unlinked
messages with:

```python
from crm.api.whatsapp import backfill_unlinked_whatsapp_messages

result = backfill_unlinked_whatsapp_messages()
print(result)
frappe.db.commit()
```

## Files changed

- `crm/api/whatsapp.py`
  - E.164 sender normalization.
  - Existing-record matching and automatic Lead creation.
  - Redis-backed duplicate prevention.
  - `WhatsApp` source creation for existing sites.
  - Backfill helper for stored unlinked messages.
- `crm/install.py`
  - Adds `WhatsApp` to default Lead Sources on new sites.
- `crm/tests/test_whatsapp.py`
  - Covers incoming-only creation, mapping, normalization, invalid input,
    locked duplicate prevention, and failure handling.
- `docker/init.sh`
  - Fixes the shell interpreter path and installs `frappe_whatsapp` in new
    Docker environments.

## Verification

Run the focused test module with:

```bash
bench --site <site-name> run-tests \
  --app crm \
  --module crm.tests.test_whatsapp
```

The current implementation passes 11 tests. A Meta-shaped local POST was also
verified end-to-end: HTTP 200, incoming message storage, E.164 normalization,
new Lead creation with source `WhatsApp`, and message-to-Lead linking. The
synthetic Message, Lead, and Notification Log were removed after inspection.

## Production checklist

1. Install `crm` and `frappe_whatsapp` on the site.
2. Configure an active WhatsApp Account and set the incoming/outgoing defaults.
3. Store the Meta access token and a strong webhook verify token securely.
4. Expose the callback through a stable HTTPS hostname.
5. Verify and save the callback in Meta Dashboard.
6. Subscribe the app to the `messages` webhook field.
7. Send a message from a test recipient and confirm the Message and Lead link.
8. Monitor Frappe Error Log and WhatsApp Notification Log.

Ordinary message webhooks do not provide email, company, product interest, or
other qualification data. Collect those values through a WhatsApp Flow,
chatbot questions, or manual follow-up.
