import { Card } from "../../components/ui/Card";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { toneHex } from "../../lib/status";
import { cn } from "../../lib/utils";

export type SignalCardData = {
  id: string;
  tone: BadgeTone;
  title: string;
  category: string;
  entityLabel: string;
  ageLabel: string;
  watermelon?: boolean;
  ownerSays?: string;
  signalsSay?: string;
  animateDelay?: number;
};

export function SignalCard({ data }: { data: SignalCardData }) {
  return (
    <Card
      padding="p-6"
      animateDelay={data.animateDelay}
      className="transition-transform duration-150 hover:-translate-y-0.5 hover:shadow-op-hover"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={cn(
              "mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full",
              data.tone === "danger" && "animate-op-pulse",
            )}
            style={{ backgroundColor: toneHex[data.tone] }}
          />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h3 className="text-[17px] font-bold">{data.title}</h3>
              <RagChip tone={data.tone}>{data.tone}</RagChip>
              {data.watermelon ? (
                <span className="inline-flex h-7 items-center rounded-full bg-ink px-3 text-[12px] font-bold text-white">
                  🍉 watermelon
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-[13px] text-grey-secondary">
              {data.entityLabel} · {data.category}
            </div>
          </div>
        </div>
        <div className="shrink-0 text-[14px] font-extrabold text-grey-secondary">
          {data.ageLabel}
        </div>
      </div>

      {data.ownerSays || data.signalsSay ? (
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {data.ownerSays ? (
            <div className="rounded-2xl bg-grey-fill p-3.5">
              <div className="text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
                Owner says
              </div>
              <p className="mt-1 text-[14px]">{data.ownerSays}</p>
            </div>
          ) : null}
          {data.signalsSay ? (
            <div className="rounded-2xl bg-grey-fill p-3.5">
              <div className="text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
                Signals say
              </div>
              <p className="mt-1 text-[14px]">{data.signalsSay}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
