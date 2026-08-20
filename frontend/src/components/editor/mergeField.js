import { Node, mergeAttributes } from '@tiptap/core'

/**
 * A merge field as one non-editable token (master spec item 4).
 *
 * A form author writes "Hi " and then picks "First name" from a menu. What
 * lands in the document is a single atom: the caret steps over it, backspace
 * removes the whole pill, and it cannot be half-deleted into `{{ first_nam }}`,
 * which is the failure this node exists to prevent -- a broken token renders as
 * nothing and the customer is greeted by "Hi ,".
 *
 * The rendered HTML deliberately CONTAINS the literal `{{ token }}` text:
 *
 *     <span class="merge-field" data-merge-field="first_name">{{ first_name }}</span>
 *
 * so `crm.api.form.render_merge`, which knows nothing about TipTap and searches
 * for `{{ token }}`, substitutes it without any conversion step between the
 * editor and the server. The wrapper span survives into the sent email and is
 * harmless: it wraps the value, it carries no style of its own in mail.
 */
export const MergeField = Node.create({
  name: 'mergeField',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      token: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-merge-field'),
        renderHTML: (attributes) =>
          attributes.token ? { 'data-merge-field': attributes.token } : {},
      },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-merge-field]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    return [
      'span',
      mergeAttributes({ class: 'merge-field' }, HTMLAttributes),
      `{{ ${node.attrs.token} }}`,
    ]
  },

  renderText({ node }) {
    return `{{ ${node.attrs.token} }}`
  },

  addCommands() {
    return {
      insertMergeField:
        (token) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: { token } }),
    }
  },
})
