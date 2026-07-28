import { AskTheGraph } from "../features/coordination/AskTheGraph";
import { BriefsColumn } from "../features/coordination/BriefsColumn";
import { RequestsBoard } from "../features/coordination/RequestsBoard";

export function CoordinationPage() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-[28px] font-extrabold">Coordination</h1>
        <p className="mt-1 text-[15px] text-grey-secondary">
          Cross-person requests, scheduled briefs, and a direct line to the graph.
        </p>
      </div>

      <RequestsBoard />

      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[1.4fr_1fr]">
        <BriefsColumn />
        <AskTheGraph />
      </div>
    </div>
  );
}
