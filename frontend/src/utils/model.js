export const standardFieldsMeta = [
  {
    fieldname: 'name',
    label: 'Name',
    fieldtype: 'Data',
  },
  {
    fieldname: 'creation',
    label: 'Created On',
    fieldtype: 'Datetime',
  },
  {
    fieldname: 'modified',
    label: 'Last Modified',
    fieldtype: 'Datetime',
  },
  {
    fieldname: 'modified_by',
    label: 'Modified By',
    fieldtype: 'Link',
    options: 'User',
  },
  { label: 'Assigned To', fieldtype: 'Text', fieldname: '_assign' },
  {
    label: 'Owner',
    fieldtype: 'Link',
    fieldname: 'owner',
    options: 'User',
  },
  { label: 'Like', fieldtype: 'Data', fieldname: '_liked_by' },
  // Makes "Tags" addable as a list column (master spec §5, item 2). The server
  // already offers `_user_tags` as a filterable field
  // (`crm/api/doc.py::get_filterable_fields`); this is the column side of it.
  { label: 'Tags', fieldtype: 'Data', fieldname: '_user_tags' },
]

export const noValueFieldTypes = [
  'Section Break',
  'Column Break',
  'Tab Break',
  'Table',
  'Table MultiSelect',
  'Button',
  'Image',
]
