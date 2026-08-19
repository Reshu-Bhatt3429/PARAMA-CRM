/**
 * Workflow rules (master spec §5 item 16): the shapes and the rules of the
 * When / If / Then editor.
 *
 * Everything here is pure. The editor is one long vertical stack of cards with
 * no nested modal, which means the "is this rule finished?" question is asked
 * in a dozen places on screen at once -- on the Save button, under each card,
 * on the list row. Answering it in a component would put that logic behind a
 * mount; answering it here lets `frontend/tests/unit/workflows.test.js` check
 * every branch of it directly, which is how the rest of this app tests.
 *
 * The server validates all of this again in
 * `crm/fcrm/doctype/crm_workflow_rule/crm_workflow_rule.py`. This copy exists
 * to tell a manager what is missing BEFORE the round trip, never instead of the
 * server's check.
 */

export const DOCTYPE_LEAD = 'CRM Lead'
export const DOCTYPE_DEAL = 'CRM Deal'

export const APPLY_ON_OPTIONS = [
  { label: 'Lead', value: DOCTYPE_LEAD },
  { label: 'Deal', value: DOCTYPE_DEAL },
]

export const EVENT_CREATED = 'Record created'
export const EVENT_FIELD_CHANGED = 'Field changed'
export const EVENT_STAGE_CHANGED = 'Stage changed'

export const EVENT_OPTIONS = [
  { label: 'is created', value: EVENT_CREATED },
  { label: 'moves to a new stage', value: EVENT_STAGE_CHANGED },
  { label: 'has a field changed', value: EVENT_FIELD_CHANGED },
]

export const ACTION_EMAIL = 'Send email template'
export const ACTION_TASK = 'Create task'
export const ACTION_NOTIFY = 'Notify user'
export const ACTION_UPDATE = 'Update field'

export const ACTION_OPTIONS = [
  { label: 'Send an email template', value: ACTION_EMAIL },
  { label: 'Create a task', value: ACTION_TASK },
  { label: 'Notify a user', value: ACTION_NOTIFY },
  { label: 'Update a field', value: ACTION_UPDATE },
]

export const RECIPIENT_RECORD = 'Record email'
export const RECIPIENT_ASSIGNED = 'Assigned user'
export const RECIPIENT_SPECIFIC = 'Specific address'

export const RECIPIENT_OPTIONS = [
  { label: "The lead's or deal's own email", value: RECIPIENT_RECORD },
  { label: 'The assigned user', value: RECIPIENT_ASSIGNED },
  { label: 'A specific address', value: RECIPIENT_SPECIFIC },
]

export const NOTIFY_ASSIGNED = 'Assigned user'
export const NOTIFY_SPECIFIC = 'Specific user'
export const NOTIFY_ROLE = 'Everyone with a role'

export const NOTIFY_OPTIONS = [
  { label: 'The assigned user', value: NOTIFY_ASSIGNED },
  { label: 'A specific user', value: NOTIFY_SPECIFIC },
  { label: 'Everyone with a role', value: NOTIFY_ROLE },
]

export const TASK_PRIORITIES = ['Low', 'Medium', 'High']

export const DEFAULT_DAILY_CAP = 500

/**
 * Fields the framework owns. Kept in step with `PROTECTED_FIELDS` in
 * `crm/workflows.py`; the server refuses them whatever this list says.
 */
export const PROTECTED_FIELDS = [
  'name',
  'owner',
  'creation',
  'modified',
  'modified_by',
  'docstatus',
  'idx',
  'doctype',
  'parent',
  'parenttype',
  'parentfield',
  'naming_series',
  '_assign',
  '_comments',
  '_liked_by',
  '_user_tags',
]

/** Statuses the execution log can hold, and how each one reads on a badge. */
export const RUN_THEMES = {
  Executed: 'green',
  Claimed: 'blue',
  'Skipped-cap': 'orange',
  'Skipped-suppressed': 'orange',
  Failed: 'red',
}

export function runTheme(status) {
  return RUN_THEMES[status] || 'gray'
}

export function emptyAction(type = ACTION_TASK) {
  return {
    action_type: type,
    email_template: '',
    recipient_mode: RECIPIENT_RECORD,
    recipient_address: '',
    task_title: '',
    task_priority: 'Medium',
    task_due_offset_days: 1,
    notify_mode: NOTIFY_ASSIGNED,
    notify_user: '',
    notify_role: '',
    update_field: '',
    update_value: '',
  }
}

export function emptyRule() {
  return {
    name: '',
    title: '',
    apply_on: DOCTYPE_LEAD,
    event: EVENT_STAGE_CHANGED,
    watched_field: '',
    enabled: 0,
    daily_action_cap: DEFAULT_DAILY_CAP,
    condition_json: [],
    actions: [emptyAction()],
  }
}

/** The editor's shape, from what `crm.workflows.get_rule` returns. */
export function fromServer(data) {
  const rule = emptyRule()
  if (!data) return rule

  const conditions = data.condition_json
  return {
    ...rule,
    name: data.name || '',
    title: data.title || '',
    apply_on: data.apply_on || DOCTYPE_LEAD,
    event: data.event || EVENT_STAGE_CHANGED,
    watched_field: data.watched_field || '',
    enabled: data.enabled ? 1 : 0,
    daily_action_cap:
      data.daily_action_cap === 0 || data.daily_action_cap
        ? Number(data.daily_action_cap)
        : DEFAULT_DAILY_CAP,
    condition_json: parseConditions(conditions),
    actions: (data.actions || []).map((action) => ({
      ...emptyAction(action.action_type),
      ...action,
    })),
  }
}

/** Conditions arrive as a parsed list, a JSON string, or nothing at all. */
export function parseConditions(value) {
  if (Array.isArray(value)) return value
  if (typeof value !== 'string' || !value.trim()) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/** What `crm.workflows.save_rule` is given. Only fields the server accepts. */
export function toPayload(rule) {
  const payload = {
    title: (rule.title || '').trim(),
    apply_on: rule.apply_on,
    event: rule.event,
    watched_field: rule.event === EVENT_FIELD_CHANGED ? rule.watched_field : '',
    enabled: rule.enabled ? 1 : 0,
    daily_action_cap: Number(rule.daily_action_cap) || 0,
    condition_json: rule.condition_json || [],
    actions: (rule.actions || []).map((action) => trimAction(action)),
  }
  if (rule.name) payload.name = rule.name
  return payload
}

/**
 * Only the fields the chosen action type uses.
 *
 * A manager who tries "send email", changes their mind and picks "create task"
 * would otherwise save a template name on a task action. The server ignores it,
 * but the next person to open the rule would see it in the payload and wonder.
 */
export function trimAction(action) {
  const type = action.action_type
  const base = { action_type: type }

  if (type === ACTION_EMAIL) {
    base.email_template = action.email_template || ''
    base.recipient_mode = action.recipient_mode || RECIPIENT_RECORD
    if (base.recipient_mode === RECIPIENT_SPECIFIC) {
      base.recipient_address = (action.recipient_address || '').trim()
    }
  } else if (type === ACTION_TASK) {
    base.task_title = (action.task_title || '').trim()
    base.task_priority = action.task_priority || 'Medium'
    base.task_due_offset_days = Number(action.task_due_offset_days) || 0
  } else if (type === ACTION_NOTIFY) {
    base.notify_mode = action.notify_mode || NOTIFY_ASSIGNED
    if (base.notify_mode === NOTIFY_SPECIFIC)
      base.notify_user = action.notify_user || ''
    if (base.notify_mode === NOTIFY_ROLE)
      base.notify_role = action.notify_role || ''
  } else if (type === ACTION_UPDATE) {
    base.update_field = (action.update_field || '').trim()
    base.update_value = action.update_value ?? ''
  }

  return base
}

/** Every reason this rule cannot be saved, in the order they appear on screen. */
export function validateRule(rule) {
  const errors = []

  if (!rule || !(rule.title || '').trim()) {
    errors.push(__('Give the rule a title.'))
  }

  if (!rule) return errors

  if (
    rule.event === EVENT_FIELD_CHANGED &&
    !(rule.watched_field || '').trim()
  ) {
    errors.push(__('Choose the field to watch.'))
  }

  if (Number(rule.daily_action_cap) < 0) {
    errors.push(__('The daily cap cannot be negative.'))
  }

  const actions = rule.actions || []
  if (!actions.length) {
    errors.push(
      __('Add at least one action. A rule with none would do nothing.'),
    )
  }

  actions.forEach((action, index) => {
    validateAction(action, index + 1).forEach((message) => errors.push(message))
  })

  return errors
}

export function validateAction(action, position) {
  const errors = []
  const at = (message) => __('Action {0}: {1}', [position, message])

  if (action.action_type === ACTION_EMAIL) {
    if (!action.email_template) errors.push(at(__('choose an email template.')))
    if (
      action.recipient_mode === RECIPIENT_SPECIFIC &&
      !(action.recipient_address || '').trim()
    ) {
      errors.push(at(__('give the address to send to.')))
    }
  } else if (action.action_type === ACTION_TASK) {
    if (!(action.task_title || '').trim())
      errors.push(at(__('give the task a title.')))
  } else if (action.action_type === ACTION_NOTIFY) {
    if (action.notify_mode === NOTIFY_SPECIFIC && !action.notify_user) {
      errors.push(at(__('choose the user to notify.')))
    }
    if (action.notify_mode === NOTIFY_ROLE && !action.notify_role) {
      errors.push(at(__('choose the role to notify.')))
    }
  } else if (action.action_type === ACTION_UPDATE) {
    const field = (action.update_field || '').trim()
    if (!field) {
      errors.push(at(__('choose the field to update.')))
    } else if (PROTECTED_FIELDS.includes(field)) {
      errors.push(
        at(__('{0} is written by the system and cannot be set.', [field])),
      )
    }
  } else {
    errors.push(at(__('choose what the action does.')))
  }

  return errors
}

/** One line for an action card's collapsed header. */
export function summariseAction(action) {
  if (!action) return ''

  switch (action.action_type) {
    case ACTION_EMAIL:
      return action.email_template
        ? __('Send "{0}"', [action.email_template])
        : __('Send an email template')
    case ACTION_TASK:
      return action.task_title
        ? __('Create task "{0}"', [action.task_title])
        : __('Create a task')
    case ACTION_NOTIFY:
      if (action.notify_mode === NOTIFY_SPECIFIC && action.notify_user) {
        return __('Notify {0}', [action.notify_user])
      }
      if (action.notify_mode === NOTIFY_ROLE && action.notify_role) {
        return __('Notify everyone with the role {0}', [action.notify_role])
      }
      return __('Notify the assigned user')
    case ACTION_UPDATE:
      return action.update_field
        ? __('Set {0} to "{1}"', [
            action.update_field,
            action.update_value ?? '',
          ])
        : __('Update a field')
    default:
      return __('Unknown action')
  }
}

/** One line for a rule list row: what fires it. */
export function describeTrigger(rule) {
  const noun = rule.apply_on === DOCTYPE_DEAL ? __('deal') : __('lead')

  if (rule.event === EVENT_CREATED) return __('When a {0} is created', [noun])
  if (rule.event === EVENT_FIELD_CHANGED) {
    return rule.watched_field
      ? __("When a {0}'s {1} changes", [noun, rule.watched_field])
      : __("When a {0}'s field changes", [noun])
  }
  return __("When a {0}'s stage changes", [noun])
}

/** How many of today's cap the rule has spent, as a 0-100 number. */
export function capUsedPercent(rule) {
  const cap = Number(rule?.daily_action_cap) || 0
  if (cap <= 0) return 0
  const used = Number(rule?.runs_today) || 0
  return Math.min(100, Math.round((used / cap) * 100))
}

/** True when a rule is one save away from acting on real records. */
export function isArmed(rule, flagOn) {
  return Boolean(flagOn) && Boolean(rule?.enabled)
}
