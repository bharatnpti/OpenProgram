import type { ReactNode } from "react";
import { useId } from "react";

import type { ReportPreviewResponse, ReportSectionResponse } from "../../api/schema";
import { DateBar } from "./DateBar";
import { GateRings } from "./GateRings";
import { StageStrip } from "./StageStrip";
import { WhoActs } from "./WhoActs";
import { type ReportFacts, importantView, questionRows, verdictTone } from "./dailyViz";
import { RAG_WORDS } from "./reportView";
import { DayChip } from "./vizBits";
import "./daily.css";

const RAG_TONE = { green: "green", amber: "amber", red: "red", unknown: "neutral" } as const;

/**
 * The day's report as the console draws it: the same five sections, in the same
 * order and under the same titles as the text Send now sends, each fact drawn once.
 * Everything comes from the preview: its sections for the words, its facts for the
 * pictures, which the server builds from the same parts, so nothing here says more
 * than the message.
 */
export function DailyReport({
  preview,
  facts,
}: {
  preview: ReportPreviewResponse;
  facts: ReportFacts;
}) {
  const section = (title: string) => preview.sections.find((item) => item.title === title);
  const inShort = section("In short");
  const stand = section("Where we stand");
  const important = section("Most important");
  const needs = section("What we need, and from whom");
  const questions = section("Open questions");
  const headId = useId();

  return (
    <article className="dv-card" aria-labelledby={headId}>
      <header className="dv-head">
        <p className="text-[12px] font-bold uppercase tracking-wider text-(--op-day-secondary)">
          {preview.title}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          <DayChip tone={RAG_TONE[preview.rag]} dot>
            {RAG_WORDS[preview.rag]}
          </DayChip>
          <h2 id={headId} className="min-w-0 text-[20px] font-extrabold text-balance">
            {preview.headline}
          </h2>
        </div>
      </header>

      {facts.note || facts.delivery ? (
        <Section title={inShort?.title ?? "In short"}>
          {facts.note ? (
            <p className="rounded-r-[14px] border-l-[3px] border-(--op-day-magenta) bg-(--op-day-note-bg) px-3.5 py-2.5 text-[14px] text-(--op-day-body)">
              <b className="mb-0.5 block text-[11px] uppercase tracking-wider text-(--op-day-secondary)">
                {facts.note.author ? `${facts.note.author} · today's note` : "Today's note"}
              </b>
              {facts.note.text}
            </p>
          ) : null}
          {facts.delivery ? <DateBar facts={facts.delivery} today={preview.report_date} /> : null}
        </Section>
      ) : null}

      <Section title={stand?.title ?? "Where we stand"}>
        <StageStrip progress={facts.progress} progressLine={preview.progress_line} />
        <GateRings gates={facts.gates} />
      </Section>

      <Section title={important?.title ?? "Most important"}>
        <MostImportant facts={facts} emptyText={important?.empty_text || "Nothing today."} />
      </Section>

      <Section title={needs?.title ?? "What we need, and from whom"}>
        <WhoActs owners={facts.asks} emptyText={needs?.empty_text || "Nothing today."} />
      </Section>

      <Section title={questions?.title ?? "Open questions"}>
        <OpenQuestions section={questions} />
      </Section>
    </article>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="dv-rsec" aria-labelledby={id}>
      <h3 id={id} className="dv-rsec-t">
        {title}
      </h3>
      <div className="dv-rsec-b">{children}</div>
    </section>
  );
}

/**
 * Most important without what is drawn elsewhere: every requirement that went around
 * a gate (the message stops at eight lines), the lines no picture draws, and one
 * short pointer to the rest: "See In short for the delivery date, and What we need
 * for the 4 risks." It always shows: with only risks, it is all the section says.
 */
function MostImportant({ facts, emptyText }: { facts: ReportFacts; emptyText: string }) {
  const view = importantView(facts.important);
  if (view.empty) return <p className="text-[14px] text-(--op-day-secondary)">{emptyText}</p>;
  const tone = facts.delivery ? verdictTone(facts.delivery.verdict) : "neutral";
  return (
    <>
      {view.groups.length || view.lines.length ? (
        <ul className="grid gap-2" aria-label="What threatens the delivery date">
          {view.groups.map((group) => (
            <li
              key={group.words}
              className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-r-xl border-l-4 border-(--op-day-red-solid) bg-(--op-day-red-bg) px-3 py-2.5 text-[13.5px]"
            >
              <span className="min-w-0 flex-[1_1_260px] font-bold">{group.words}</span>
              {group.keys.map((key) => (
                <span key={key} className="dv-key">
                  {key}
                </span>
              ))}
            </li>
          ))}
          {view.lines.map((line) => (
            <li
              key={line}
              className="rounded-r-xl border-l-4 px-3 py-2.5 text-[13.5px] font-bold"
              style={{
                borderColor:
                  tone === "neutral"
                    ? "var(--op-day-axis)"
                    : `var(--op-day-${tone === "red" ? "red-solid" : tone})`,
                background:
                  tone === "neutral" ? "var(--op-day-surface-3)" : `var(--op-day-${tone}-bg)`,
              }}
            >
              {line}
            </li>
          ))}
        </ul>
      ) : null}
      {view.note ? <p className="dv-note">{view.note}</p> : null}
    </>
  );
}

function OpenQuestions({ section }: { section: ReportSectionResponse | undefined }) {
  const rows = questionRows(section?.table);
  if (!rows.length) {
    return (
      <p className="text-[14px] text-(--op-day-secondary)">
        {section?.empty_text || "No open questions."}
      </p>
    );
  }
  return (
    <ul className="grid gap-2" aria-label="Open questions">
      {rows.map((row, index) => (
        <li
          key={`${row.ticket}-${index}`}
          className="grid gap-1.5 rounded-[14px] border border-(--op-day-grid) bg-(--op-day-surface-3) px-3.5 py-3 text-[13.5px] text-(--op-day-body)"
        >
          <span>
            {row.ticket ? (
              <span className="dv-key mr-1.5" style={{ background: "var(--op-day-surface-2)" }}>
                {row.ticket}
              </span>
            ) : null}
            <q className="font-bold text-(--op-day-ink)">{row.question}</q>
          </span>
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12.5px] text-(--op-day-secondary)">
            {row.asked ? <span>{row.asked}</span> : null}
            {row.heard ? (
              <DayChip tone={row.tone} small>
                {row.heard}
              </DayChip>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}
