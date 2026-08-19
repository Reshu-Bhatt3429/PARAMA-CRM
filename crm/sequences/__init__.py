"""The channel-agnostic sequence engine and its channel adapters.

`crm.sequences.core` owns everything about a follow-up sequence that is NOT
about a particular channel: when a conversation is enrolled, when a stage falls
due, what stops a sequence, and -- above all -- the order in which the claim,
the commit and the external send happen. `crm.sequences.whatsapp` carries the
Meta semantics and `crm.sequences.email` carries the Email Queue semantics over
`crm.outbound`; `crm.sequences.router` decides which of the two answers a call
when one sequence mixes both. `crm.sequences.unsubscribe` mints and verifies the
tokens that put a compliant unsubscribe link on every sequence email.

Nothing here sends anything by itself. A sequence runs only when a caller hands
the core an adapter, and the adapters are built by the follow-up engine for
itself. Both channels are behind default-OFF flags: `email_sequences_enabled`
for the email stages, and `outbound_engine_enabled` for the machine that would
deliver them.
"""

from crm.sequences.core import (
	AlreadyClaimed,
	ChannelAdapter,
	advance,
	deliver,
	enroll,
	enrolment_cutoff,
	in_quiet_hours,
	process_row,
	quiet_hours_end_after,
	send_stage,
	sequence_key,
	sweep,
	to_time,
)
