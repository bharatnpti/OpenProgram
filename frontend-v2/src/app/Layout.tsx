import { Outlet } from "react-router-dom";

import { CommandPalette } from "../components/shell/CommandPalette";
import { Header } from "../components/shell/Header";
import { ViewingDateBanner } from "../components/shell/ViewingDate";
import { useCommandPalette } from "../lib/useCommandPalette";
import { ViewingDateProvider } from "./ViewingDateProvider";

export function Layout() {
  const { open, setOpen } = useCommandPalette();

  return (
    <ViewingDateProvider>
      <div className="min-h-screen bg-white text-ink">
        <Header onOpenPalette={() => setOpen(true)} />
        <main className="mx-auto max-w-[1360px] px-8 pb-20 pt-8">
          <ViewingDateBanner />
          <Outlet />
        </main>
        <CommandPalette open={open} onClose={() => setOpen(false)} />
      </div>
    </ViewingDateProvider>
  );
}
