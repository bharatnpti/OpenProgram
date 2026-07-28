export function firstItemId<T extends { id: string }>(items: T[] | undefined): string {
  return items?.[0]?.id ?? "";
}

export function resolveSelection(
  current: string,
  items: Array<{ id: string }> | undefined,
): string {
  if (current && items?.some((item) => item.id === current)) {
    return current;
  }
  return firstItemId(items);
}
