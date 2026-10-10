import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { ReportPreviewResponse, ReportSectionResponse } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { RAG_WORDS, toneForRag } from "../../lib/status";
import { cn } from "../../lib/utils";
import { DailyReport } from "./DailyReport";
import { SentText } from "./SentText";
import { ASKS_SECTION, askParts } from "./reportView";

/**
 * Today's report built live from the project's state, exactly as it would be
 * sent, drawn as the console's pictures from the preview's facts. With `showText`
 * the text itself is beside it (above it on a phone). One read: the pictures and
 * the text come from the same response, so they cannot disagree.
 */
export function ReportPreview({
  reportId,
  showText,
  textId,
}: {
  reportId: string;
  showText: boolean;
  textId: string;
}) {
  const preview = useQuery({
    queryKey: ["day-reports", "preview", reportId],
    queryFn: () => apiClient.previewDayReport(reportId),
  });
  const data = preview.data;

  return (
    <PanelState
      isLoading={preview.isLoading}
      error={preview.error}
      onRetry={() => void preview.refetch()}
    >
      {data ? (
        <div
          className={cn(
            "grid grid-cols-[minmax(0,1fr)] items-start gap-5",
            showText && "lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]",
          )}
        >
          {showText ? <SentText id={textId} text={data.text} /> : null}
          <div className="min-w-0 lg:col-start-1 lg:row-start-1">
            {data.facts ? (
              <DailyReport preview={data} facts={data.facts} />
            ) : (
              <ReportSections preview={data} />
            )}
          </div>
        </div>
      ) : null}
    </PanelState>
  );
}

/** A server without facts: the report's sections in its own words, as before the pictures. */
function ReportSections({ preview }: { preview: ReportPreviewResponse }) {
  return (
    <article className="rounded-3xl border border-grey-border">
      <header className="border-b border-grey-border p-5">
        <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
          {preview.title}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <RagChip tone={toneForRag(preview.rag)} dot>
            {RAG_WORDS[preview.rag]}
          </RagChip>
          <h2 className="min-w-0 text-[20px] font-extrabold text-balance">{preview.headline}</h2>
        </div>
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-0">
        {preview.sections.map((section) => (
          <Section key={section.title} section={section} />
        ))}
      </div>
    </article>
  );
}

function Section({ section }: { section: ReportSectionResponse }) {
  const groups = section.groups ?? [];
  const table = section.table;
  const empty = section.lines.length === 0 && groups.length === 0 && !table?.rows.length;

  return (
    <section className="grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-grey-border p-5 first:border-t-0 md:grid-cols-[200px_minmax(0,1fr)] md:gap-6">
      <h3 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary md:pt-1">
        {section.title}
      </h3>
      <div className="grid grid-cols-[minmax(0,1fr)] min-w-0 gap-3 text-[14px] text-grey-body">
        {empty ? (
          <p className="text-grey-secondary">{section.empty_text || "Nothing today."}</p>
        ) : null}
        {section.lines.map((line, i) => (
          <p key={i}>{line}</p>
        ))}
        {groups.length > 0 && section.title === ASKS_SECTION ? (
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-4">
            {groups.map((group) => (
              <li
                key={group.heading}
                className="grid grid-cols-[minmax(0,1fr)] gap-1.5 sm:grid-cols-[180px_minmax(0,1fr)]"
              >
                <span className="font-extrabold text-ink">{group.heading}</span>
                <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
                  {group.lines.map((line, i) => {
                    const ask = askParts(line);
                    return (
                      <li key={i} className="flex flex-wrap items-baseline gap-2">
                        {ask.kind ? (
                          <span className="rounded bg-grey-fill px-1.5 py-0.5 text-[11px] font-extrabold uppercase tracking-wide text-grey-secondary">
                            {ask.kind}
                          </span>
                        ) : null}
                        <span>{ask.text}</span>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        ) : null}
        {groups.length > 0 && section.title !== ASKS_SECTION
          ? groups.map((group) => (
              <div key={group.heading}>
                <p className="font-extrabold text-ink">{group.heading}</p>
                <ul className="mt-1 grid gap-1">
                  {group.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </div>
            ))
          : null}
        {table && table.rows.length > 0 ? (
          <TableBox>
            <table className="w-full min-w-[560px] border-collapse">
              <thead>
                <tr>
                  {table.columns.map((column) => (
                    <th key={column} className={th}>
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c} className={td}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableBox>
        ) : null}
      </div>
    </section>
  );
}
