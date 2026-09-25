import type { InputHTMLAttributes } from 'react'

type EnglishDateInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'onChange'> & {
  value: string
  onChange: (value: string) => void
}

export function EnglishDateInput({ value, onChange, ...props }: EnglishDateInputProps) {
  return (
    <input
      {...props}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      placeholder="YYYY-MM-DD"
      pattern="\d{4}-\d{2}-\d{2}"
      maxLength={10}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}
