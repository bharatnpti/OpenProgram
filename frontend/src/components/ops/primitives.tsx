import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { RefreshCw, Search, TriangleAlert } from "lucide-react";

import { ApiError } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-xs font-medium text-muted-foreground">{eyebrow}</div>}
        <h1 className="truncate text-2xl font-semibold tracking-normal text-foreground">{title}</h1>
        {description && <div className="mt-1 text-sm text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b border-border bg-background/90 py-3 backdrop-blur",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  detail,
  icon,
  tone = "neutral",
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}) {
  const toneClass = {
    neutral: "text-muted-foreground",
    success: "text-success",
    warning: "text-warning",
    danger: "text-danger",
    info: "text-info",
  }[tone];
  return (
    <section className="rounded-lg border border-border bg-surface px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {icon && <span className={cn("shrink-0", toneClass)}>{icon}</span>}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {detail && <div className="max-w-40 truncate text-xs text-muted-foreground">{detail}</div>}
      </div>
    </section>
  );
}

export function DataPanel({
  title,
  description,
  action,
  children,
  className,
  bodyClassName,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("rounded-lg border border-border bg-surface shadow-sm", className)}>
      <div className="flex min-h-12 items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <div className={cn("px-4 py-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function EmptyState({
  title = "No data",
  description,
  action,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-28 flex-col items-center justify-center rounded-md border border-dashed border-border bg-surface-muted/50 px-4 py-6 text-center">
      <div className="text-sm font-medium">{title}</div>
      {description && (
        <div className="mt-1 max-w-md text-sm text-muted-foreground">{description}</div>
      )}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = "Unable to load",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-4 py-4">
      <div className="flex items-start gap-3">
        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-danger">{title}</div>
          <div className="mt-1 text-sm text-muted-foreground">{messageForError(error)}</div>
        </div>
        {onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    </div>
  );
}

export function QueryState<T>({
  query,
  children,
  loadingRows = 4,
  empty,
}: {
  query: Pick<UseQueryResult<T, Error>, "isLoading" | "isError" | "error" | "refetch" | "data">;
  children: (data: T) => ReactNode;
  loadingRows?: number;
  empty?: ReactNode;
}) {
  if (query.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: loadingRows }).map((_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (!query.data) {
    return empty ?? <EmptyState />;
  }
  return children(query.data);
}

export function AsOfControl({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
      As of
      <Input
        type="date"
        className="w-auto min-w-36"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

export function RefreshButton({
  onClick,
  refreshing,
}: {
  onClick: () => void;
  refreshing?: boolean;
}) {
  return (
    <Button type="button" variant="outline" onClick={onClick} disabled={refreshing}>
      <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
      Refresh
    </Button>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        className="pl-9"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

export function EntitySelector({
  value,
  onChange,
  items,
  placeholder,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  items: DirectoryItemResponse[];
  placeholder?: string;
  className?: string;
}) {
  return (
    <Select value={value} onChange={(event) => onChange(event.target.value)} className={className}>
      {placeholder && <option value="">{placeholder}</option>}
      {items.map((item) => (
        <option key={item.id} value={item.id}>
          {item.name}
        </option>
      ))}
    </Select>
  );
}

function messageForError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Request failed.";
}
