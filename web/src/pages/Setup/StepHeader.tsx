export function StepHeader({ title, text }: { title: string; text: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-[20px] font-semibold text-ink">{title}</h1>
      <p className="mt-1 max-w-[60ch] text-sm text-ink-2">{text}</p>
    </div>
  )
}
