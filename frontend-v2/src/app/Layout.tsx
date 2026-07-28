import { Outlet } from "react-router-dom";

import { CommandPalette } from "../components/shell/CommandPalette";
import { Header } from "../components/shell/Header";
import { useCommandPalette } from "../lib/useCommandPalette";

export function Layout() {
  const { open, setOpen } = useCommandPalette();

  return (
    <div className="min-h-screen bg-white text-ink">
      <Header onOpenPalette={() => setOpen(true)} />
      <main className="mx-auto max-w-[1360px] px-8 pb-20 pt-8">
        <Outlet />
      </main>
      <CommandPalette open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
