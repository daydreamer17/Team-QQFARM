import { forwardRef } from 'react'
import type { InputHTMLAttributes } from 'react'

type EnglishFilePickerProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  fileName?: string | null
  buttonText?: string
  emptyText?: string
}

export const EnglishFilePicker = forwardRef<HTMLInputElement, EnglishFilePickerProps>(function EnglishFilePicker({
  fileName,
  buttonText,
  emptyText,
  multiple,
  ...props
}, ref) {
  return (
    <label className="english-file-picker">
      <span className="english-file-picker-button">{buttonText ?? (multiple ? 'Choose files' : 'Choose file')}</span>
      <span className="english-file-picker-name">{fileName || emptyText || (multiple ? 'No files selected' : 'No file selected')}</span>
      <input {...props} ref={ref} multiple={multiple} type="file" />
    </label>
  )
})
