# Database connections

What opens a Postgres connection, what bounds each pool, and how the local
stack's defaults fit inside the server's `max_connections`. Read it before
raising a pool size, adding a process that talks to the same server, or
turning on the `langfuse` or `temporal` compose profiles.

---

## 1. What connects

The backend (API) and the worker are separate processes, and **both launch
DBOS**: each runs the scheduler and the queue workers, so either may run any
scheduled or queued workflow. Each process holds the same set of pools.

| Who, per process | Pool | Bounded by | Default |
|---|---|---|---|
| Application queries: API requests, readiness checks and **every workflow step the process runs** | one `psycopg_pool.AsyncConnectionPool`, shared by every `ServiceRegistry` in the process | `OPENPROGRAM_POSTGRES_POOL_MAX_SIZE` (hard cap); a caller waits `OPENPROGRAM_POSTGRES_POOL_TIMEOUT_SECONDS`, then gets `PoolTimeout` | 1–10, 30 s |
| Reachability check | one short connection each time that pool opens | — | 1, brief |
| DBOS system database | SQLAlchemy pool, `max_overflow` 0; connections stay open once made | `OPENPROGRAM_DBOS_SYSTEM_POOL_SIZE` | 10 |
| DBOS notification listener (`LISTEN`) | one connection, outside that pool | — | 1 |
| DBOS readiness probe (API only) | one short connection, until DBOS has launched | — | 1, brief |

Outside the two processes: `make migrate` (Alembic) holds one connection while
it runs, and every `psql` session is one more.

A `ServiceRegistry` borrows the process's pool and never closes it. Each
workflow step still builds a registry of its own and closes it when done; that
releases what the registry owns (its Redis client) and leaves the pool open
for everyone else. The process owner closes the pool once, as it stops:
`ServiceRegistry.shutdown()` in the API's lifespan and the worker's main.

The pool belongs to the event loop that opened it. The API and the worker each
run one loop, and DBOS runs its workflows on that loop, so each process has
exactly one application pool.

## 2. The budget

For the compose defaults (`docker compose up`, no profiles), against
Postgres's default `max_connections` of 100:

| | backend | worker | total |
|---|---|---|---|
| Application pool (`OPENPROGRAM_POSTGRES_POOL_MAX_SIZE`) | 10 | 10 | 20 |
| Reachability check, brief | 1 | 1 | 2 |
| DBOS pool (`OPENPROGRAM_DBOS_SYSTEM_POOL_SIZE`) | 10 | 10 | 20 |
| DBOS listener | 1 | 1 | 2 |
| DBOS readiness probe, brief | 1 | — | 1 |
| `make migrate`, while it runs | | | 1 |
| **Worst case** | **23** | **22** | **46** |

`superuser_reserved_connections` keeps 3 back, so 97 are usable and the
headroom is **51**. It covers `psql` sessions and a host `uvicorn` against the
same server: up to 11 connections with `OPENPROGRAM_WORKFLOW_PROVIDER=fake`, 23
with `dbos`. The `langfuse` and `temporal` profiles bring pools of their own
and are not in this table; size them, or raise `max_connections`, before you
turn them on.

The rule, for any other numbers (the two 1s are the API's readiness probe and
a migration):

```
processes × (POOL_MAX_SIZE + DBOS_SYSTEM_POOL_SIZE + 2) + 1 + 1  ≤  max_connections − 3 − headroom
```

The table is a ceiling, not the usual load. The application pool shrinks back
towards its minimum when idle; DBOS keeps every connection it has made, so
after a busy spell an idle process holds about its DBOS pool size plus one.

## 3. A catch-up after sleep or downtime

DBOS's scheduler keeps one thread per schedule, each stepping from one tick to
the next. When the process wakes after a long pause (a laptop that slept, a
container that was frozen), every tick it missed fires back to back, whatever
`automatic_backfill` says: that flag only covers ticks missed while the process
was down. A night's sleep was 35 quarter-hourly Git ticks and 9 hourly Jira
ticks, each fanning out to one sync per repository or project: about 290 syncs
in 5 seconds.

Two things keep that inside the budget:

1. **A superseded tick does nothing.** A scheduled Jira or Git sync whose next
   tick is already due returns at once without dispatching, and logs
   `scheduled_sync_tick_superseded`. A sync reads from its cursor, so the
   latest tick covers the whole gap. The decision is a DBOS step, so a
   recovered run keeps the answer it first recorded.
2. **Syncs wait their turn.** Every Jira and Git sync workflow, from the
   runtime fan-out or from admin's "sync now", is enqueued on the DBOS queue
   `openprogram_sync`. At most `OPENPROGRAM_SYNC_QUEUE_CONCURRENCY` (4) run at
   once across both processes; the rest wait as `ENQUEUED`. The queue is
   stored in the system database and owned by one DBOS application, like the
   schedules: two applications (`OPENPROGRAM_DBOS_APP_NAME`) cannot share a
   system database.

Neither touches the check-in fan-out: its schedule, its catch-up backfill and
its one-message-per-person behaviour are unchanged, and the derived schedules
(risk, drift, rollup, briefs, reports) keep every tick.

## 4. Checking a running stack

```sql
SELECT client_addr, coalesce(nullif(application_name, ''), '(app pool)') AS pool,
       state, count(*)
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1, 2, 3
ORDER BY 4 DESC;
```

`dbos_transact` rows are DBOS (pool plus listener); an empty application name
is the application pool. Map a `client_addr` to its container with
`docker inspect -f '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <container>`.

A burst against a throwaway copy of a seeded database measures one process's
peak; [`test_connection_budget.py`](../../backend/tests/integration/test_connection_budget.py)
says how to run it.

## 5. Why not PgBouncer

The application-level caps already bound the total, so a pooler would add a
hop without adding headroom. It would also break things in transaction mode,
the only mode that saves connections:

- DBOS's notification listener needs one long-lived session for `LISTEN`.
- DBOS passes `idle_in_transaction_session_timeout` as a libpq startup option.
- psycopg 3 prepares a statement on the server after five executions.
- The graph adapter runs `LOAD 'age'` on its connection before each graph
  transaction.

Session mode keeps all of that working, but then the server holds one
connection per client connection anyway.
