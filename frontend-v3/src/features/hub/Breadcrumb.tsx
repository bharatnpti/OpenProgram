import { Link } from "react-router-dom";

/**
 * Where the level on screen sits: "All projects › Digital Platform Program ›
 * Checkout Revamp ›", each step a link up to it where the role has it. The
 * last step, the place itself, is the title below, so it is not repeated here.
 */
export function Breadcrumb({ steps }: { steps: { label: string; to: string | null }[] }) {
  if (steps.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className="text-[13px] text-grey-secondary">
      <ol className="flex flex-wrap items-center gap-x-1.5">
        {steps.map((step, index) => (
          <li key={`${index}:${step.label}`} className="flex min-w-0 items-center gap-x-1.5">
            {step.to ? (
              <Link
                to={step.to}
                className="truncate text-grey-body underline-offset-2 hover:underline max-sm:inline-flex max-sm:min-h-9 max-sm:items-center"
              >
                {step.label}
              </Link>
            ) : (
              <span className="truncate">{step.label}</span>
            )}
            <span aria-hidden>›</span>
          </li>
        ))}
      </ol>
    </nav>
  );
}
