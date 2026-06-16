# Frontend Shell LLD

## Stack

- Vite + React + TypeScript strict.
- Tailwind CSS with shadcn-style local primitives.
- TanStack Query for server state.
- Typed API client generated from FastAPI OpenAPI into `frontend/src/api/schema.ts`.

## First Screen

The first screen is an operational shell:

- Health/readiness status.
- Demo graph query result.
- Provider and tenant indicators.

There is no landing page.

## Tests

Frontend verification is `npm run lint`, `npm run typecheck`, and `npm run build`.
