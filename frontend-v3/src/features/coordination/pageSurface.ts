// The page surface the Requests and Pod pages draw on. No runtime imports.

/**
 * The report pages' dark hook (`.op-viz`, set by `data-theme="dark"`). Its white
 * cards and ink buttons read as the dark surface and its text inside it by the
 * rules in index.css, the same on every page. In the light theme nothing changes.
 */
export const PAGE_SURFACE = "op-viz";
