import type { ReportPreviewResponse, ReportSectionResponse } from "../../api/schema";
import { RagChip } from "../../components/ui/RagChip";
import { toneForRag } from "../../lib/status";
import { cn } from "../../lib/utils";
import { RAG_WORDS, askParts, progressWidth } from "./reportView";

/** The section whose groups are people, each with what is needed from them. */
const ASKS_SECTION = "What we need, and from whom";

/**
 * Today's report as its readers get it, laid out to read: the headline, the
 * progress bar, then each section in the report's own order and words.
 */
export function ReportPreview({ preview }: { preview: ReportPreviewResponse }) {
  const width = progressWidth(preview.percent_complete);
  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <h3 className="text-[18px] font-bold leading-snug">{preview.title}</h3>
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <RagChip tone={toneForRag(preview.rag)} dot className="shrink-0">
            {RAG_WORDS[preview.rag]}
          </RagChip>
          <p className="min-w-0 flex-1 basis-[220px] text-[15px] font-bold">{preview.headline}</p>
        </div>
        <div>
          <div
            className="h-2.5 w-full overflow-hidden rounded-full bg-grey-fill"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(width)}
            aria-label="Requirements in production"
          >
            <div className="h-full rounded-full bg-magenta" style={{ width: `${width}%` }} />
          </div>
          <p className="mt-1.5 text-[13px] text-grey-secondary">{preview.progress_line}</p>
        </div>
      </header>
      {preview.sections.map((section) => (
        <Section key={section.title} section={section} />
      ))}
    </article>
  );
}

function Section({ section }: { section: ReportSectionResponse }) {
  const groups = section.groups ?? [];
  const empty =
    section.lines.length === 0 &&
    groups.length === 0 &&
    (!section.table || section.table.rows.length === 0);
  return (
    <section className="flex flex-col gap-2 border-t border-grey-border pt-4">
      <h4 className="text-[13px] font-bold uppercase tracking-wide text-grey-secondary">
        {section.title}
      </h4>
      {empty ? (
        <p className="text-[14px] text-grey-secondary">{section.empty_text || "Nothing today."}</p>
      ) : null}
      {section.lines.length > 0 ? <Lines lines={section.lines} /> : null}
      {groups.length > 0 ? (
        section.title === ASKS_SECTION ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {groups.map((group) => (
              <div key={group.heading} className="rounded-2xl bg-grey-fill p-4">
                <div className="text-[14px] font-bold">{group.heading}</div>
                <ul className="mt-2 flex flex-col gap-2">
                  {group.lines.map((line, index) => {
                    const ask = askParts(line);
                    return (
                      <li key={index} className="text-[14px] leading-snug">
                        {ask.kind ? (
                          <span className="mr-2 inline-flex h-5 items-center rounded-full bg-white px-2 align-[1px] text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
                            {ask.kind}
                          </span>
                        ) : null}
                        {ask.text}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {groups.map((group) => (
              <div key={group.heading}>
                <div className="text-[14px] font-bold">{group.heading}</div>
                <Lines lines={group.lines} />
              </div>
            ))}
          </div>
        )
      ) : null}
      {section.table && section.table.rows.length > 0 ? (
        // A wide table scrolls inside the report, never the page.
        <div className="max-w-full overflow-x-auto rounded-2xl border border-grey-border">
          <table className="w-full min-w-[560px] border-collapse text-left text-[13px]">
            <thead className="bg-grey-fill">
              <tr>
                {section.table.columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-bold">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.table.rows.map((row, index) => (
                <tr key={index} className="border-t border-grey-border align-top">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      // The ticket key and the date keep to one line.
                      className={cn("px-3 py-2", cellIndex === 0 && "whitespace-nowrap")}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function Lines({ lines }: { lines: string[] }) {
  return (
    <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 text-[14px] leading-snug">
      {lines.map((line, index) => (
        <li key={index}>{line}</li>
      ))}
    </ul>
  );
}
