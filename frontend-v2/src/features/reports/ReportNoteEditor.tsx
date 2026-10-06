import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DayReportResponse } from "../../api/schema";
import { TextArea } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { errorMessage } from "../admin/adminTypes";

const MAX_NOTE = 1000;

/**
 * The note today's report opens with: the news, or the ask. The product owner,
 * a manager or an admin writes it (the report's `can_write_note`); everyone
 * else reads it. It goes out with today's report only.
 */
export function ReportNoteEditor({
  report,
  onSaved,
  label,
}: {
  report: DayReportResponse;
  onSaved: (saved: DayReportResponse) => Promise<unknown>;
  /** A visible label above the text, when the surrounding card names no report. */
  label?: string;
}) {
  const saved = report.note?.text ?? "";
  const [text, setText] = useState(saved);
  const write = useMutation({
    mutationFn: (next: string) => apiClient.writeDayReportNote(report.report_id, next),
    onSuccess: async (result) => {
      setText(result.note?.text ?? "");
      await onSaved(result);
      toast.success(result.note ? "Note saved for today's report." : "Note removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const writtenBy = report.note
    ? `Written by ${report.note.author_name ?? report.note.author}`
    : "No note for today yet.";

  if (!report.can_write_note) {
    return (
      <div className="flex flex-col gap-1">
        {label ? <span className="text-[15px] font-bold">{label}</span> : null}
        {report.note ? <p className="whitespace-pre-wrap text-[14px]">{report.note.text}</p> : null}
        <span className="text-[12px] text-grey-secondary">{writtenBy}</span>
      </div>
    );
  }
  const id = `report-note-${report.report_id}`;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className={label ? "text-[15px] font-bold" : "sr-only"}>
        {label ?? "Today's note"}
      </label>
      <TextArea
        id={id}
        value={text}
        maxLength={MAX_NOTE}
        placeholder="Payments is waiting on the vendor's sandbox; we need an answer by Thursday."
        onChange={(event) => setText(event.target.value)}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[12px] text-grey-secondary">{writtenBy}</span>
        <div className="flex gap-2">
          {saved ? (
            <Pill
              variant="ghost"
              size="sm"
              disabled={write.isPending}
              onClick={() => write.mutate("")}
            >
              Remove note
            </Pill>
          ) : null}
          <Pill
            size="sm"
            disabled={text.trim() === saved.trim() || !text.trim() || write.isPending}
            onClick={() => write.mutate(text)}
          >
            {write.isPending ? "Saving…" : "Save note"}
          </Pill>
        </div>
      </div>
    </div>
  );
}
