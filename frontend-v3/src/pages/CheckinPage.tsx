import { useRole } from "../app/role";
import { SectionHeader } from "../components/PanelState";
import { CheckinCard } from "../features/today/CheckinCard";
import { TaskList } from "../features/today/TaskList";
import { ChatPage } from "./ChatPage";

/**
 * Check-in (`/check-in`): where the person's own work stands, to confirm or
 * correct, task by task; on a local tenant the built-in chat's thread beside
 * it. For now the developer Today's check-in card and task list and the Chat
 * page as they were; lane T builds the page (D95).
 */
export function CheckinPage() {
  const { access } = useRole();
  return (
    <>
      <SectionHeader
        title="Check-in"
        meta="Confirm today's check-in and say where each task stands. Saving here counts as today's reply."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
          <CheckinCard />
          <TaskList />
        </div>
        {access.checkin.chat ? (
          <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
            <ChatPage />
          </div>
        ) : null}
      </div>
    </>
  );
}
