/**
 * Email delivery and open state (master spec item 19).
 *
 * The spec is explicit about what this is NOT: no custom pixel, no new
 * tracking, no toast, no timeline text. Everything here reads two fields the
 * server already returned -- `delivery_status`, which is the framework's Email
 * Queue verdict, and `read_by_recipient`, which is the framework's own read
 * receipt -- and turns them into one quiet mark beside the timestamp.
 *
 * Why a state name and not a label: the caller owns the words and the relative
 * time, because those need `__()` and the app's `timeAgo`, and this module is a
 * pure helper with a unit suite (`tests/unit/emailStatus.test.js`) that cannot
 * load the frappe-ui bundle.
 *
 * This module stays free of frappe-ui imports for that reason.
 */

/** The queue says the message left the building. */
const DELIVERED_STATUSES = ['Sent', 'Delivered', 'Clicked', 'Opened', 'Read']

/** The queue still has it. */
const PENDING_STATUSES = ['Sending', 'Scheduled', 'Not Sent', 'Queued']

/** The queue gave up. */
const FAILED_STATUSES = ['Error', 'Expired', 'Bounced', 'Failed']

/**
 * One of `'none' | 'pending' | 'sent' | 'opened' | 'failed'`.
 *
 * `opened` wins over everything: a message somebody has read was self-evidently
 * delivered, and showing "queued" next to "opened" would be the app arguing
 * with itself.
 *
 * `none` is the answer for an INCOMING message and for anything with no state
 * at all. A received email has no delivery status of ours to report, and a
 * check mark on somebody else's message would be a lie about who sent it.
 */
export function emailState(data = {}) {
  if (!data) return 'none'

  if (data.read_by_recipient) return 'opened'

  const status = data.delivery_status
  if (!status) return 'none'
  if (FAILED_STATUSES.includes(status)) return 'failed'
  if (DELIVERED_STATUSES.includes(status)) return 'sent'
  if (PENDING_STATUSES.includes(status)) return 'pending'

  // An unrecognised status is shown as pending rather than hidden: the message
  // exists, we simply do not know its verdict, and inventing "delivered" is the
  // one answer that could mislead.
  return 'pending'
}

/**
 * Which mark the state draws. `null` means draw nothing at all.
 *
 * Two marks only, and both are gray. The spec asks for a quiet indicator, and a
 * green tick beside every sent message turns the timeline into a badge shelf
 * (spec §2.13).
 */
export function emailStateIcon(state) {
  switch (state) {
    case 'opened':
      return 'check-check'
    case 'sent':
      return 'check'
    case 'pending':
      return 'clock'
    case 'failed':
      return 'alert-circle'
    default:
      return null
  }
}

/**
 * The timestamp the indicator's tooltip should name, or null.
 *
 * For an opened message that is when it was opened; for everything else the
 * caller falls back to the message's own date, which it already has.
 */
export function emailStateTimestamp(data = {}) {
  if (!data) return null
  if (data.read_by_recipient && data.read_by_recipient_on) {
    return data.read_by_recipient_on
  }
  return null
}

/**
 * True when the indicator belongs on this activity at all.
 *
 * Only on a message this site sent. An incoming email is somebody else's send,
 * and an automated notification is not a message an agent is waiting on.
 */
export function showEmailState(activity = {}) {
  if (!activity) return false
  if (activity.communication_type === 'Automated Message') return false
  return emailState(activity.data) !== 'none'
}
