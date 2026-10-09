/**
 * "Preview what will be sent": the preview's `text`, exactly as the server wrote it,
 * which is what Send now and the scheduled send carry as plain text. Beside the
 * report on a wide screen, above it on a phone. Nothing here is rewritten: the
 * pictures are the console's, the message stays text.
 */
export function SentText({ id, text }: { id: string; text: string }) {
  return (
    <aside
      id={id}
      aria-labelledby={`${id}-title`}
      className="min-w-0 rounded-3xl border border-(--op-day-border) bg-(--op-day-surface-3) text-(--op-day-ink) lg:sticky lg:top-36 lg:col-start-2 lg:row-start-1 lg:max-h-[calc(100vh-10rem-var(--op-float-room))] lg:overflow-y-auto"
    >
      <div className="border-b border-(--op-day-border) px-[18px] pb-3 pt-4">
        <h2 id={`${id}-title`} className="text-[15px] font-extrabold">
          What Send now sends
        </h2>
        <p className="mt-1 text-[12.5px] leading-normal text-(--op-day-secondary)">
          Plain text, exactly as a direct message carries it. The scheduled send carries the same:
          the same five sections, in the same order, as the report.
        </p>
      </div>
      <pre className="dv-sent">{text}</pre>
    </aside>
  );
}
