import {
  CLOSED_TASK_STATUSES,
  taskDueClass,
  taskDueLabel,
  taskDueState,
} from '@/utils/tasks'

// A fixed "now" so the suite does not change verdict with the wall clock —
// the Stage 1B lesson from `test_quiet_hours_defer_instead_of_cancel`.
const now = new Date(2026, 7, 19, 14, 0, 0) // 2026-08-19 14:00 local

describe('taskDueState', () => {
  it('returns none without a due date', () => {
    expect(taskDueState(null, 'Todo', now)).toBe('none')
    expect(taskDueState('', 'Todo', now)).toBe('none')
    expect(taskDueState(undefined, 'Todo', now)).toBe('none')
  })

  it('returns none for an unparsable due date', () => {
    expect(taskDueState('not a date', 'Todo', now)).toBe('none')
  })

  it('returns none for a finished task, however overdue', () => {
    for (const status of CLOSED_TASK_STATUSES) {
      expect(taskDueState('2020-01-01 09:00:00', status, now)).toBe('none')
    }
  })

  it('paints a later day gray', () => {
    expect(taskDueState('2026-08-20 09:00:00', 'Todo', now)).toBe('future')
  })

  it('paints anything today amber, even an hour already past', () => {
    expect(taskDueState('2026-08-19 18:00:00', 'Todo', now)).toBe('today')
    expect(taskDueState('2026-08-19 09:00:00', 'Todo', now)).toBe('today')
  })

  it('paints an earlier day red', () => {
    expect(taskDueState('2026-08-18 23:59:00', 'Todo', now)).toBe('overdue')
  })

  it('accepts a Date as well as a server string', () => {
    expect(taskDueState(new Date(2026, 7, 19, 9, 0, 0), 'Todo', now)).toBe(
      'today',
    )
  })

  it('treats every open status the same', () => {
    for (const status of ['Backlog', 'Todo', 'In Progress']) {
      expect(taskDueState('2026-08-18 09:00:00', status, now)).toBe('overdue')
    }
  })
})

describe('taskDueClass', () => {
  it('gives each state its own colour', () => {
    const classes = ['none', 'future', 'today', 'overdue'].map(taskDueClass)
    expect(classes[2]).not.toBe(classes[1])
    expect(classes[3]).not.toBe(classes[2])
  })

  it('leaves an unremarkable due date in the default ink', () => {
    expect(taskDueClass('future')).toBe(taskDueClass('none'))
  })
})

describe('taskDueLabel', () => {
  it('explains only the coloured states', () => {
    expect(taskDueLabel('overdue')).toBe('Overdue')
    expect(taskDueLabel('today')).toBe('Due today')
    expect(taskDueLabel('future')).toBe('')
    expect(taskDueLabel('none')).toBe('')
  })
})
