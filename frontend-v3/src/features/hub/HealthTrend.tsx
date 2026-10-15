import { PanelState } from "../../components/PanelState";
import { Panel, Sparkline } from "../../components/ui/Bits";
import { momentum, momentumNote } from "../../lib/momentum";
import { useHealthTrend, type TrendLevel } from "./useHealthTrend";

/**
 * "Health, 30 days": a project's or a pod's health over the last 30 days, as a
 * sparkline and its direction in words ("steady, now red"), counted over the
 * days that reported a status. Today's health, never the date, so it is drawn
 * only on Early warnings and always labelled as health.
 */
export function HealthTrend({ level, id, name }: { level: TrendLevel; id: string; name: string }) {
  const trend = useHealthTrend(level, id);
  const m = trend.data ? momentum(trend.data.points) : null;
  return (
    <Panel
      title="Health, 30 days"
      note={trend.data && m ? momentumNote(m, trend.data.window_days) : undefined}
    >
      <PanelState
        isLoading={trend.isLoading}
        error={trend.error}
        onRetry={() => void trend.refetch()}
      >
        {m ? (
          <>
            <Sparkline values={m.values} label={`${name}'s health over 30 days: ${m.label}`} />
            <p className="mt-2 text-[12px] text-grey-secondary">
              Measured only over days that reported a status.
            </p>
          </>
        ) : null}
      </PanelState>
    </Panel>
  );
}
