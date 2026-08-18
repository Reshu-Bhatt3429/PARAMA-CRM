"""The channel-agnostic sequence engine and its channel adapters.

`crm.sequences.core` owns everything about a follow-up sequence that is NOT
about a particular channel: when a conversation is enrolled, when a stage falls
due, what stops a sequence, and -- above all -- the order in which the claim,
the commit and the external send happen. `crm.sequences.whatsapp` carries the
Meta semantics; a later stage adds an email adapter over `crm.outbound`.

Nothing here sends anything by itself. A sequence runs only when a caller hands
the core an adapter, and the only adapter registered today is the WhatsApp one
the follow-up engine builds for itself.
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
