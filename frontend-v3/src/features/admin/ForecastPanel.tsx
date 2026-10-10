import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ForecastSettingsResponse, ForecastSettingsUpdateRequest } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { Problems, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  MIN_HISTORY_LABEL,
  draftChanged,
  draftFromSettings,
  minHistoryHelp,
  minHistoryProblem,
  minHistoryStatus,
  requestFromDraft,
} from "./forecastForm";
import { savedLine, useMemberNames } from "./members";

const FORECAST_KEY = ["config", "delivery-forecast"] as const;

/**
 * How many working days of history the delivery forecast waits for before it
 * gives its 50% and 85% dates. Every scope's forecast, Overall's charts and
 * the day report follow it as soon as it is saved.
 */
export function ForecastPanel() {
  const settings = useQuery({
    queryKey: FORECAST_KEY,
    queryFn: () => apiClient.forecastSettings(),
  });
  return (
    <PanelState
      isLoading={settings.isLoading}
      error={settings.error}
      onRetry={() => void settings.refetch()}
    >
      {settings.data ? (
        <ForecastEditor
          key={`${settings.data.updated_at ?? "default"}:${settings.data.min_history_days}`}
          saved={settings.data}
        />
      ) : null}
    </PanelState>
  );
}

function ForecastEditor({ saved }: { saved: ForecastSettingsResponse }) {
  const queryClient = useQueryClient();
  const nameOf = useMemberNames();
  const [text, setText] = useState(draftFromSettings(saved));
  const problem = minHistoryProblem(text, saved);
  const changed = draftChanged(text, saved);

  const save = useMutation({
    mutationFn: (body: ForecastSettingsUpdateRequest) => apiClient.saveForecastSettings(body),
    onSuccess: (next) => {
      toast.success(
        next.is_default
          ? `Forecasts wait for the default ${next.min_history_days} working days of history again.`
          : `Forecasts now wait for ${next.min_history_days} working days of history.`,
      );
      queryClient.setQueryData(FORECAST_KEY, next);
      // Every forecast, the charts that count against it, and the day report.
      for (const key of [
        "delivery",
        "delivery-history",
        "pod-delivery",
        "requirements",
        "day-reports",
      ]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (error) => toast.error(errorText(error)),
  });

  return (
    <Panel title="Forecast">
      <p className="mb-3 max-w-[760px] text-[13px] text-grey-body">
        The delivery forecast replays the working days your requirements reached production. It
        waits until it has enough of them before it says when the work is 50% and 85% likely to be
        done.
      </p>
      <label htmlFor="forecast-min-history" className={labelClass}>
        {MIN_HISTORY_LABEL}
      </label>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (changed && problem === null) save.mutate(requestFromDraft(text));
        }}
      >
        <input
          id="forecast-min-history"
          className={cn(inputClass, "w-[120px] tabular-nums", problem !== null && "border-rag-red")}
          type="number"
          inputMode="numeric"
          min={saved.lowest}
          max={saved.highest}
          step={1}
          value={text}
          aria-describedby="forecast-min-history-help"
          aria-invalid={problem !== null}
          onChange={(event) => setText(event.target.value)}
        />
        <Pill type="submit" size="sm" disabled={!changed || problem !== null || save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Pill>
        {saved.is_default ? null : (
          <Pill
            type="button"
            size="sm"
            variant="ghost"
            disabled={save.isPending}
            onClick={() => save.mutate({ min_history_days: null })}
          >
            Use the default ({saved.default_min_history_days})
          </Pill>
        )}
      </form>
      <p id="forecast-min-history-help" className={hintClass}>
        {minHistoryHelp(saved)}
      </p>
      {changed && problem !== null ? (
        <div className="mt-3">
          <Problems problems={[problem]} />
        </div>
      ) : null}
      <p className="mt-3 text-[13px] text-grey-body">
        {minHistoryStatus(saved, savedLine(saved.updated_at, saved.updated_by, nameOf))}
      </p>
    </Panel>
  );
}
