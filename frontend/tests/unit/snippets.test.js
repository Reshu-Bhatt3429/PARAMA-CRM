import {
  applySnippet,
  filterSnippets,
  htmlToText,
  slashTrigger,
} from '@/utils/snippets'

const snippet = (overrides = {}) => ({
  name: 'SNIP-0001',
  title: 'Booking confirmation',
  shortcut: 'booking',
  body: '<p>Hello</p>',
  shared: 0,
  ...overrides,
})

describe('filterSnippets', () => {
  it('returns everything for an empty query', () => {
    const list = [snippet(), snippet({ name: 'SNIP-0002' })]
    expect(filterSnippets(list, '')).toHaveLength(2)
    expect(filterSnippets(list, '   ')).toHaveLength(2)
  })

  it('survives a missing list', () => {
    expect(filterSnippets(null, 'x')).toEqual([])
    expect(filterSnippets(undefined, '')).toEqual([])
  })

  it('matches the shortcut', () => {
    const list = [snippet(), snippet({ name: 'SNIP-0002', shortcut: 'quote' })]
    expect(filterSnippets(list, 'quo').map((s) => s.name)).toEqual([
      'SNIP-0002',
    ])
  })

  it('matches the title', () => {
    const list = [snippet({ title: 'Payment reminder', shortcut: 'pay' })]
    expect(filterSnippets(list, 'reminder')).toHaveLength(1)
  })

  it('is case-insensitive', () => {
    expect(filterSnippets([snippet()], 'BOOK')).toHaveLength(1)
  })

  it('ranks a shortcut prefix above a title match', () => {
    const list = [
      snippet({
        name: 'title-match',
        title: 'A book about it',
        shortcut: 'zz',
      }),
      snippet({ name: 'prefix-match', title: 'Nothing', shortcut: 'book' }),
    ]
    expect(filterSnippets(list, 'book').map((s) => s.name)).toEqual([
      'prefix-match',
      'title-match',
    ])
  })

  it('drops what does not match at all', () => {
    expect(filterSnippets([snippet()], 'invoice')).toEqual([])
  })

  it('handles snippets with missing fields', () => {
    expect(filterSnippets([{ name: 'x' }], 'anything')).toEqual([])
  })
})

describe('slashTrigger', () => {
  it('fires on a slash at the very start', () => {
    expect(slashTrigger('/', 1)).toEqual({
      active: true,
      query: '',
      from: 0,
      to: 1,
    })
  })

  it('fires on a slash at the start of a later line', () => {
    const text = 'Hello\n/bk'
    expect(slashTrigger(text, text.length)).toEqual({
      active: true,
      query: 'bk',
      from: 6,
      to: 9,
    })
  })

  it('carries what has been typed after the slash', () => {
    expect(slashTrigger('/booking', 8).query).toBe('booking')
  })

  it('ignores a slash inside a sentence', () => {
    expect(slashTrigger('open 24/7 today', 9).active).toBe(false)
  })

  it('ignores a slash inside a URL', () => {
    const text = 'see https://example.com/page'
    expect(slashTrigger(text, text.length).active).toBe(false)
  })

  it('stops once a space is typed', () => {
    expect(slashTrigger('/book now', 9).active).toBe(false)
  })

  it('is inactive with the caret on the slash itself', () => {
    expect(slashTrigger('/bk', 0).active).toBe(false)
  })

  it('survives empty input and a caret past the end', () => {
    expect(slashTrigger('', 0).active).toBe(false)
    expect(slashTrigger('/bk', 99)).toEqual({
      active: true,
      query: 'bk',
      from: 0,
      to: 3,
    })
  })
})

describe('applySnippet', () => {
  it('replaces the trigger slice', () => {
    const result = applySnippet('/boo', { from: 0, to: 4 }, 'Hello there')
    expect(result.text).toBe('Hello there')
    expect(result.caret).toBe(11)
  })

  it('keeps the text around the trigger', () => {
    const result = applySnippet('one\n/bk\ntwo', { from: 4, to: 7 }, 'BODY')
    expect(result.text).toBe('one\nBODY\ntwo')
    expect(result.caret).toBe(8)
  })

  it('inserts at the caret when there is no trigger slice', () => {
    const result = applySnippet('abcdef', { from: 3, to: 3 }, 'XY')
    expect(result.text).toBe('abcXYdef')
    expect(result.caret).toBe(5)
  })

  it('appends when the trigger is missing entirely', () => {
    expect(applySnippet('abc', null, '!').text).toBe('abc!')
  })

  it('survives empty inputs', () => {
    expect(applySnippet('', { from: 0, to: 0 }, '').text).toBe('')
    expect(applySnippet(null, null, 'x').text).toBe('x')
  })
})

describe('htmlToText', () => {
  it('returns nothing for nothing', () => {
    expect(htmlToText('')).toBe('')
    expect(htmlToText(null)).toBe('')
  })

  it('strips tags', () => {
    expect(htmlToText('<p>Hello <b>Ann</b></p>')).toBe('Hello Ann')
  })

  it('turns breaks and paragraphs into newlines', () => {
    expect(htmlToText('<p>one</p><p>two</p>')).toBe('one\ntwo')
    expect(htmlToText('one<br>two')).toBe('one\ntwo')
  })

  it('marks list items', () => {
    expect(htmlToText('<ul><li>one</li><li>two</li></ul>')).toBe('- one\n- two')
  })

  it('decodes the entities a composer produces', () => {
    expect(htmlToText('a&nbsp;b &amp; c &lt;d&gt;')).toBe('a b & c <d>')
  })

  it('collapses runs of blank lines', () => {
    expect(htmlToText('<p>a</p><p></p><p></p><p>b</p>')).toBe('a\n\nb')
  })

  it('removes a script tag rather than running it', () => {
    expect(htmlToText('<script>alert(1)</script>hi')).toBe('alert(1)hi')
  })
})
