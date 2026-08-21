import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div><h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  )
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function StatusBadge({ status }: { status: string }) {
  const tones: Record<string, string> = {
    SUCCEEDED: 'success', PUBLISHED: 'success', PARSED: 'success', READY: 'success', RESOLVED: 'success',
    RUNNING: 'info', PROCESSING: 'info', QUEUED: 'neutral',
    WAITING_REVIEW: 'warning', WAITING_COST_CONFIRMATION: 'warning', OPEN: 'warning',
    FAILED: 'danger', CANCELED: 'danger', REJECTED: 'danger',
  }
  return <Badge tone={tones[status] ?? 'neutral'}>{status}</Badge>
}

export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input {...props} />
      {hint && <small>{hint}</small>}
    </label>
  )
}

export function SelectField({ label, children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select {...props}>{children}</select>
    </label>
  )
}

export function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (value: boolean) => void; hint?: string }) {
  return (
    <label className="toggle-row">
      <span><strong>{label}</strong>{hint && <small>{hint}</small>}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}

export function Loading() {
  return <div className="loading"><span /> 正在读取系统状态…</div>
}
