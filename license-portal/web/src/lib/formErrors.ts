import type { FormInstance } from 'antd'

/**
 * Map the server's `errors: {field: message}` payload onto antd form fields.
 * Returns true when at least one field was highlighted.
 */
export function applyServerErrors<T>(form: FormInstance<T>, errors: Record<string, string>): boolean {
  const entries = Object.entries(errors)
  if (!entries.length) return false
  form.setFields(
    entries.map(([name, text]) => ({ name: name as never, errors: [text] })),
  )
  return true
}
