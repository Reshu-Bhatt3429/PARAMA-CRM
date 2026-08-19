import {
  ACTION_EMAIL,
  ACTION_NOTIFY,
  ACTION_TASK,
  ACTION_UPDATE,
  APPLY_ON_OPTIONS,
  DEFAULT_DAILY_CAP,
  DOCTYPE_DEAL,
  DOCTYPE_LEAD,
  EVENT_CREATED,
  EVENT_FIELD_CHANGED,
  EVENT_STAGE_CHANGED,
  NOTIFY_ROLE,
  NOTIFY_SPECIFIC,
  PROTECTED_FIELDS,
  RECIPIENT_RECORD,
  RECIPIENT_SPECIFIC,
  capUsedPercent,
  describeTrigger,
  emptyAction,
  emptyRule,
  fromServer,
  isArmed,
  parseConditions,
  runTheme,
  summariseAction,
  toPayload,
  trimAction,
  validateAction,
  validateRule,
} from '@/utils/workflows'

describe('emptyRule', () => {
  it('starts disabled, because two switches must be on before anything fires', () => {
    expect(emptyRule().enabled).toBe(0)
  })

  it('starts with a cap rather than unlimited', () => {
    expect(emptyRule().daily_action_cap).toBe(DEFAULT_DAILY_CAP)
  })

  it('starts with one action, so the Then card is never empty on screen', () => {
    expect(emptyRule().actions).toHaveLength(1)
    expect(emptyRule().actions[0].action_type).toBe(ACTION_TASK)
  })

  it('offers exactly the two doctypes the engine serves', () => {
    expect(APPLY_ON_OPTIONS.map((o) => o.value)).toEqual([
      DOCTYPE_LEAD,
      DOCTYPE_DEAL,
    ])
  })
})

describe('parseConditions', () => {
  it('passes a list through', () => {
    expect(parseConditions([['status', '==', 'Open']])).toEqual([
      ['status', '==', 'Open'],
    ])
  })

  it('reads the JSON string the server stores', () => {
    expect(parseConditions('[["status","==","Open"]]')).toEqual([
      ['status', '==', 'Open'],
    ])
  })

  it('never throws on nothing or on junk', () => {
    for (const value of ['', null, undefined, 'not json', '{}', 7]) {
      expect(parseConditions(value)).toEqual([])
    }
  })
})

describe('fromServer', () => {
  it('fills every field a new rule has, from a partial payload', () => {
    const rule = fromServer({ name: 'WF-1', title: 'Test' })
    expect(rule.name).toBe('WF-1')
    expect(rule.apply_on).toBe(DOCTYPE_LEAD)
    expect(rule.event).toBe(EVENT_STAGE_CHANGED)
    expect(rule.daily_action_cap).toBe(DEFAULT_DAILY_CAP)
  })

  it('keeps a cap of zero rather than replacing it with the default', () => {
    expect(fromServer({ daily_action_cap: 0 }).daily_action_cap).toBe(0)
  })

  it('gives every action row the full field set the editor binds to', () => {
    const rule = fromServer({
      actions: [{ action_type: ACTION_EMAIL, email_template: 'welcome' }],
    })
    expect(rule.actions[0].email_template).toBe('welcome')
    expect(rule.actions[0].recipient_mode).toBe(RECIPIENT_RECORD)
    expect(rule.actions[0].task_priority).toBe('Medium')
  })

  it('returns a blank rule for no data at all', () => {
    expect(fromServer(null)).toEqual(emptyRule())
  })
})

describe('trimAction', () => {
  it('keeps only the fields the chosen type uses', () => {
    const action = {
      ...emptyAction(ACTION_TASK),
      email_template: 'left-over',
      task_title: 'Call',
    }
    expect(trimAction(action)).toEqual({
      action_type: ACTION_TASK,
      task_title: 'Call',
      task_priority: 'Medium',
      task_due_offset_days: 1,
    })
  })

  it('drops a specific address when the mode is not specific', () => {
    const action = {
      ...emptyAction(ACTION_EMAIL),
      email_template: 'welcome',
      recipient_address: 'stale@example.com',
    }
    expect(trimAction(action).recipient_address).toBeUndefined()
  })

  it('keeps the address when the mode asks for one', () => {
    const action = {
      ...emptyAction(ACTION_EMAIL),
      email_template: 'welcome',
      recipient_mode: RECIPIENT_SPECIFIC,
      recipient_address: '  ann@example.com ',
    }
    expect(trimAction(action).recipient_address).toBe('ann@example.com')
  })

  it('keeps only the notify target the mode names', () => {
    const role = trimAction({
      ...emptyAction(ACTION_NOTIFY),
      notify_mode: NOTIFY_ROLE,
      notify_role: 'Sales Manager',
      notify_user: 'someone@example.com',
    })
    expect(role.notify_role).toBe('Sales Manager')
    expect(role.notify_user).toBeUndefined()
  })
})

describe('toPayload', () => {
  it('sends no name for a new rule, so the server inserts', () => {
    expect(toPayload(emptyRule()).name).toBeUndefined()
  })

  it('sends the name for an existing rule, so the server updates', () => {
    expect(toPayload({ ...emptyRule(), name: 'WF-1' }).name).toBe('WF-1')
  })

  it('clears the watched field on a rule that does not watch one', () => {
    const rule = {
      ...emptyRule(),
      event: EVENT_STAGE_CHANGED,
      watched_field: 'email',
    }
    expect(toPayload(rule).watched_field).toBe('')
  })

  it('keeps the watched field on a field-changed rule', () => {
    const rule = {
      ...emptyRule(),
      event: EVENT_FIELD_CHANGED,
      watched_field: 'email',
    }
    expect(toPayload(rule).watched_field).toBe('email')
  })

  it('trims the title', () => {
    expect(toPayload({ ...emptyRule(), title: '  Tidy  ' }).title).toBe('Tidy')
  })

  it('sends the enabled flag as 0 or 1, never as a boolean', () => {
    expect(toPayload({ ...emptyRule(), enabled: true }).enabled).toBe(1)
    expect(toPayload({ ...emptyRule(), enabled: false }).enabled).toBe(0)
  })
})

describe('validateRule', () => {
  const good = () => ({
    ...emptyRule(),
    title: 'Qualified leads get a task',
    actions: [{ ...emptyAction(ACTION_TASK), task_title: 'Call back' }],
  })

  it('accepts a finished rule', () => {
    expect(validateRule(good())).toEqual([])
  })

  it('wants a title', () => {
    expect(validateRule({ ...good(), title: '   ' })).toContain(
      'Give the rule a title.',
    )
  })

  it('wants a watched field on a field-changed rule', () => {
    const rule = { ...good(), event: EVENT_FIELD_CHANGED, watched_field: '' }
    expect(validateRule(rule)).toContain('Choose the field to watch.')
  })

  it('does not want one on a stage rule', () => {
    expect(validateRule({ ...good(), event: EVENT_STAGE_CHANGED })).toEqual([])
  })

  it('refuses a rule with no action', () => {
    expect(validateRule({ ...good(), actions: [] })).toHaveLength(1)
  })

  it('refuses a negative cap', () => {
    expect(validateRule({ ...good(), daily_action_cap: -1 })).toContain(
      'The daily cap cannot be negative.',
    )
  })

  it('allows a cap of zero, which means unlimited', () => {
    expect(validateRule({ ...good(), daily_action_cap: 0 })).toEqual([])
  })

  it('says which action is unfinished', () => {
    const rule = {
      ...good(),
      actions: [
        { ...emptyAction(ACTION_TASK), task_title: 'Fine' },
        { ...emptyAction(ACTION_TASK), task_title: '' },
      ],
    }
    expect(validateRule(rule)).toEqual(['Action 2: give the task a title.'])
  })
})

describe('validateAction', () => {
  it('wants a template for an email action', () => {
    expect(validateAction(emptyAction(ACTION_EMAIL), 1)).toEqual([
      'Action 1: choose an email template.',
    ])
  })

  it('wants an address only when the mode asks for one', () => {
    const action = {
      ...emptyAction(ACTION_EMAIL),
      email_template: 'welcome',
      recipient_mode: RECIPIENT_SPECIFIC,
    }
    expect(validateAction(action, 1)).toEqual([
      'Action 1: give the address to send to.',
    ])
  })

  it('wants a user for a notify-specific action', () => {
    const action = {
      ...emptyAction(ACTION_NOTIFY),
      notify_mode: NOTIFY_SPECIFIC,
    }
    expect(validateAction(action, 3)).toEqual([
      'Action 3: choose the user to notify.',
    ])
  })

  it('is happy with the assigned-user default', () => {
    expect(validateAction(emptyAction(ACTION_NOTIFY), 1)).toEqual([])
  })

  it('wants a field for an update action', () => {
    expect(validateAction(emptyAction(ACTION_UPDATE), 1)).toEqual([
      'Action 1: choose the field to update.',
    ])
  })

  it('refuses every field the framework owns', () => {
    for (const field of PROTECTED_FIELDS) {
      const action = { ...emptyAction(ACTION_UPDATE), update_field: field }
      expect(validateAction(action, 1)).toHaveLength(1)
    }
  })

  it('accepts an ordinary field with an empty new value', () => {
    const action = {
      ...emptyAction(ACTION_UPDATE),
      update_field: 'last_name',
      update_value: '',
    }
    expect(validateAction(action, 1)).toEqual([])
  })

  it('refuses an action with no type at all', () => {
    expect(validateAction({ action_type: 'Launch a rocket' }, 1)).toHaveLength(
      1,
    )
  })
})

describe('summariseAction', () => {
  it('names the template when there is one', () => {
    expect(
      summariseAction({ action_type: ACTION_EMAIL, email_template: 'Welcome' }),
    ).toBe('Send "Welcome"')
  })

  it('falls back to the plain type when the card is still empty', () => {
    expect(summariseAction(emptyAction(ACTION_EMAIL))).toBe(
      'Send an email template',
    )
    expect(summariseAction(emptyAction(ACTION_TASK))).toBe('Create a task')
    expect(summariseAction(emptyAction(ACTION_UPDATE))).toBe('Update a field')
  })

  it('names the notify target', () => {
    expect(
      summariseAction({
        action_type: ACTION_NOTIFY,
        notify_mode: NOTIFY_ROLE,
        notify_role: 'Sales Manager',
      }),
    ).toBe('Notify everyone with the role Sales Manager')
  })

  it('reads the update as a sentence', () => {
    expect(
      summariseAction({
        action_type: ACTION_UPDATE,
        update_field: 'status',
        update_value: 'Won',
      }),
    ).toBe('Set status to "Won"')
  })

  it('never throws on nothing', () => {
    expect(summariseAction(null)).toBe('')
  })
})

describe('describeTrigger', () => {
  it('says lead or deal, and what happened', () => {
    expect(
      describeTrigger({ apply_on: DOCTYPE_LEAD, event: EVENT_CREATED }),
    ).toBe('When a lead is created')
    expect(
      describeTrigger({ apply_on: DOCTYPE_DEAL, event: EVENT_STAGE_CHANGED }),
    ).toBe("When a deal's stage changes")
  })

  it('names the watched field when there is one', () => {
    expect(
      describeTrigger({
        apply_on: DOCTYPE_LEAD,
        event: EVENT_FIELD_CHANGED,
        watched_field: 'email',
      }),
    ).toBe("When a lead's email changes")
  })
})

describe('runTheme', () => {
  it('reads a success as green and a failure as red', () => {
    expect(runTheme('Executed')).toBe('green')
    expect(runTheme('Failed')).toBe('red')
  })

  it('reads both skips as a warning, not as a failure', () => {
    expect(runTheme('Skipped-cap')).toBe('orange')
    expect(runTheme('Skipped-suppressed')).toBe('orange')
  })

  it('has a fallback for a status it does not know', () => {
    expect(runTheme('Something New')).toBe('gray')
  })
})

describe('capUsedPercent', () => {
  it('is zero for an unlimited rule', () => {
    expect(capUsedPercent({ daily_action_cap: 0, runs_today: 40 })).toBe(0)
  })

  it('reports the share of the daily cap that is spent', () => {
    expect(capUsedPercent({ daily_action_cap: 200, runs_today: 50 })).toBe(25)
  })

  it('never goes over 100', () => {
    expect(capUsedPercent({ daily_action_cap: 10, runs_today: 40 })).toBe(100)
  })

  it('never throws on nothing', () => {
    expect(capUsedPercent(null)).toBe(0)
  })
})

describe('isArmed', () => {
  it('needs BOTH switches, which is the whole safety design', () => {
    expect(isArmed({ enabled: 1 }, true)).toBe(true)
    expect(isArmed({ enabled: 1 }, false)).toBe(false)
    expect(isArmed({ enabled: 0 }, true)).toBe(false)
    expect(isArmed({ enabled: 0 }, false)).toBe(false)
  })
})
