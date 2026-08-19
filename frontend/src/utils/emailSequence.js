/**
 * Item 21: the timeline's "Sequence · Stage n" chip.
 *
 * A sequence email is an ordinary outgoing email and renders as one — §2.13
 * forbids turning the record header or the timeline into a badge shelf. The
 * chip exists for one question an agent actually asks: "did I write this, or
 * did the follow-up sequence?" One quiet label answers it.
 *
 * The stage number comes from `crm.api.activities.sequence_stages`, which reads
 * it off the outbound job that sent the message. Every other message carries
 * `null` and gets no chip, and the whole lookup is skipped server-side while the
 * feature flag is off — so this returns null on a site that does not use it.
 */

/**
 * The chip label for one timeline activity, or null when it needs none.
 *
 * Defensive about the stage value because it crosses a JSON boundary: a string
 * "2" is as valid an answer as the number 2, and anything that is not a positive
 * whole number is not a stage and gets no chip rather than a chip saying NaN.
 */
export function sequenceChipLabel(activity) {
  const stage = sequenceStage(activity)
  if (!stage) return null
  return __('Sequence · Stage {0}', [stage])
}

/** The stage number on an activity, or 0 when there is not a usable one. */
export function sequenceStage(activity) {
  const raw = activity?.data?.sequence_stage
  if (raw === null || raw === undefined || raw === '') return 0

  const stage = Number(raw)
  if (!Number.isFinite(stage) || !Number.isInteger(stage) || stage < 1) return 0
  return stage
}

/** True when this activity was sent by a follow-up sequence. */
export function isSequenceEmail(activity) {
  return sequenceStage(activity) > 0
}
