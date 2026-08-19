import { DRAFT_PRESETS, bodyToHtml, disclosureLine } from '@/utils/aiDraft'

describe('bodyToHtml', () => {
  it('makes one paragraph per blank-line-separated block', () => {
    expect(bodyToHtml('First.\n\nSecond.')).toBe('<p>First.</p><p>Second.</p>')
  })

  it('keeps a single newline inside a paragraph as a break', () => {
    expect(bodyToHtml('Line one.\nLine two.')).toBe(
      '<p>Line one.<br>Line two.</p>',
    )
  })

  it('collapses runs of blank lines rather than emitting empty paragraphs', () => {
    expect(bodyToHtml('First.\n\n\n\nSecond.')).toBe(
      '<p>First.</p><p>Second.</p>',
    )
  })

  it('handles Windows line endings', () => {
    expect(bodyToHtml('First.\r\n\r\nSecond.')).toBe(
      '<p>First.</p><p>Second.</p>',
    )
  })

  it('escapes markup, because the text came from a model', () => {
    // The server returns plain text on purpose. Anything that looks like markup
    // is content, not structure.
    expect(bodyToHtml('<img src=x onerror="alert(1)">')).toContain('&lt;img')
    expect(bodyToHtml('<img src=x>')).not.toContain('<img')
  })

  it('escapes ampersands and quotes', () => {
    expect(bodyToHtml('Tea & "biscuits"')).toBe(
      '<p>Tea &amp; &quot;biscuits&quot;</p>',
    )
  })

  it('returns nothing for nothing', () => {
    expect(bodyToHtml('')).toBe('')
    expect(bodyToHtml('   \n\n  ')).toBe('')
    expect(bodyToHtml(null)).toBe('')
    expect(bodyToHtml(undefined)).toBe('')
  })
})

describe('disclosureLine', () => {
  it('names every field it was given', () => {
    const line = disclosureLine(['Lead Name', 'Destination'])
    expect(line).toContain('Lead Name')
    expect(line).toContain('Destination')
    expect(line).toContain('last 10 emails')
  })

  it('still names the message history when there are no fields', () => {
    expect(disclosureLine([])).toContain('last 10 emails')
    expect(disclosureLine(null)).toContain('last 10 emails')
  })

  it('drops empty labels rather than printing a gap', () => {
    expect(disclosureLine(['Destination', '', null])).toContain('Destination,')
  })
})

describe('DRAFT_PRESETS', () => {
  it('offers the three chips the spec names', () => {
    expect(DRAFT_PRESETS.map((p) => p.label)).toEqual([
      'Follow up',
      'Answer their question',
      'Send pricing info',
    ])
  })

  it('gives every chip an instruction to send', () => {
    for (const preset of DRAFT_PRESETS) {
      expect(preset.instruction.trim().length).toBeGreaterThan(0)
    }
  })
})

// Item 14's "immediate Undo" is NOT tested here, and deliberately not faked.
// It is the editor's own history, not code this app owns: `frappe-ui`'s
// `RichTextKit` pushes `UndoRedo` unless `starterKit.undoRedo === false`
// (node_modules/frappe-ui/src/molecules/editor/kits.ts), and the composer
// passes only `{ paragraph: false }`. Asserting that here would mean importing
// `frappe-ui/editor`, which pulls .vue files this vitest config has no plugin
// for. The behaviour is verified in the running app instead; see
// demo-package/specs/stage4-notes.md.
