import { useRole } from "../app/role";
import { DeveloperToday } from "../features/today/DeveloperToday";
import { PortfolioToday } from "../features/today/PortfolioToday";
import { ProductOwnerToday } from "../features/today/ProductOwnerToday";
import { ScrumMasterToday } from "../features/today/ScrumMasterToday";

/** Every role lands here; each sees the decisions it owns. */
export function TodayPage() {
  const { role } = useRole();
  if (role === "dev") return <DeveloperToday />;
  if (role === "sm") return <ScrumMasterToday />;
  if (role === "po") return <ProductOwnerToday />;
  return <PortfolioToday />;
}
