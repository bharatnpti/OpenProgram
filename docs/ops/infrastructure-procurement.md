# OpenProgram — Infrastructure Components & AWS Procurement

Derived from [docker-compose.yml](../../docker-compose.yml), [.env.example](../../.env.example),
[backend/config/settings.py](../../backend/config/settings.py), [Architecture.md](../../Architecture.md),
and the Alembic migrations under `backend/infra/persistence/migrations/versions/`.

Target region assumption: **eu-central-1** (Frankfurt). The app already routes LLM traffic through the
OpenAI **EU** endpoint (`https://eu.api.openai.com/v1`) and stores raw chat content in Postgres, so EU
data residency is treated as a hard constraint throughout.

---

## 1. Component inventory

### 1.1 Compute (must run continuously)

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| C1 | **Backend API** — FastAPI/uvicorn container, port 8000 | `backend/Dockerfile`; serves the SPA API + the public Slack webhook `/webhooks/slack` | ECS Fargate service · EKS deployment · App Runner · EC2 ASG | **ECS Fargate** — one container image, no cluster to run, scales to 2+ tasks behind an ALB |
| C2 | **Worker** — `python -m infra.workflows.worker` container | DBOS durable workflows + all cron schedules (check-in fan-out, reconcile, Jira/GitHub/calendar sync, risk scan, drift scan, narrative briefs, conversation purge, inbound-event sweeper) | ECS Fargate service (no ingress) · EKS deployment | **ECS Fargate**, separate service from C1. Start at 1 task; DBOS serialises schedules via Postgres so >1 task is safe but not needed initially |
| C3 | **Frontend SPA** — React/Vite static build | `frontend/` and `frontend-v2/`; `npm run build` emits static assets only | S3 + CloudFront + ACM · Amplify Hosting | **S3 + CloudFront**. Two origins/paths if both `frontend` and `frontend-v2` must be served |
| C4 | **LiteLLM proxy** — `ghcr.io/berriai/litellm` | `OPENPROGRAM_LLM_PROVIDER=litellm`; the only egress path to the model provider, holds the master key | ECS Fargate service (internal only) · EKS | **ECS Fargate**, internal ALB or service-discovery only. Never publicly exposed |

### 1.2 Data stores

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| D1 | **PostgreSQL 16** — system of record | Every domain table, the relational graph (`graph_nodes`/`graph_edges` recursive CTEs), the embedding store (`vector_items`), the AGE graph mirror, the conversation store, and the **DBOS system database** (`OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL`) | RDS for PostgreSQL · Aurora PostgreSQL · self-managed on EC2 | **See decision DP-1 below** — the TimescaleDB **and** AGE extension requirements constrain this |
| D2 | **TimescaleDB extension** | `facts` and `conversation_turns` are hypertables (migrations `0001`, `0005`), and `/ready` fails without it ([readiness.py](../../backend/infra/adapters/readiness.py)) | ❌ Not available on RDS/Aurora · Timescale Cloud on AWS · self-managed Postgres on EC2 | **Blocking decision — DP-1** |
| D2b | **Apache AGE + pgvector extensions** | The graph repository mirrors every node/edge mutation into the `openprogram_graph` AGE graph, `vector_items` uses pgvector, and `/ready` asserts both ([readiness.py](../../backend/infra/adapters/readiness.py)) | ❌ AGE not available on RDS/Aurora (pgvector is) · self-managed Postgres on EC2 | **Blocking decision — DP-1.** No separate service to buy |
| D3 | **Redis 7** | Rate-limit state, reply debounce, chat send-once idempotency keys, and the **OIDC BFF session store** (mandatory when `auth_provider=oidc_bff`, [settings.py:546](../../backend/config/settings.py#L546)) | ElastiCache for Redis (OSS) · ElastiCache Serverless · MemoryDB | **ElastiCache for Redis**, single-node to start, Multi-AZ replica for prod. Encryption in transit + at rest on |
| D4 | **ClickHouse 24.8** | Langfuse v3 only — not used by OpenProgram itself | ❌ No AWS managed ClickHouse · self-host on EC2/EKS · ClickHouse Cloud (AWS Marketplace) | **Avoid entirely — see DP-2** (use Langfuse Cloud EU) |
| D5 | **Object storage** | Langfuse media/event/batch-export buckets only (MinIO locally). The app itself does no file I/O — no `boto3`, no uploads | **S3** | **S3** with SSE-KMS, only if Langfuse is self-hosted |

> Note: pgvector and Apache AGE were dropped by migration `0024_remove_dead_infra` and then
> **restored** by
> [`0026_restore_vector_and_age_graph`](../../backend/infra/persistence/migrations/versions/0026_restore_vector_and_age_graph.py).
> Both are required extensions on the application Postgres again — the `/ready` probe asserts
> `age`, `timescaledb`, **and** `vector`. You still do **not** procure a separate vector database
> or graph database: both live inside D1 as Postgres extensions. See **DP-1**, which AGE now
> constrains further than TimescaleDB alone did.

### 1.3 Workflow engine

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| W1 | **DBOS** (default, `OPENPROGRAM_WORKFLOW_PROVIDER=dbos`) | Durable workflows + cron scheduling | **No new infra** — uses D1 Postgres as its system DB | **Use this.** Zero additional procurement |
| W2 | **Temporal** (optional, config-selectable) | Only if DBOS is swapped out | Temporal Cloud · self-host on EKS (+ its own Postgres) | **Do not procure now.** Keep as a documented fallback |

### 1.4 LLM

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| L1 | **Model provider** | `gpt-5.5` via LiteLLM for status parsing, briefs, risk narratives | **Amazon Bedrock** (eu-central-1) · keep OpenAI EU endpoint · Azure OpenAI EU | **Bedrock as the AWS-native option** — LiteLLM already abstracts the provider, so this is a config change in [infra/litellm/config.yaml](../../infra/litellm/config.yaml). See DP-3 |
| L2 | **API key / model access** | `OPENAI_API_KEY` today | Bedrock model access request + IAM · or an OpenAI EU org key | Whichever DP-3 selects |

### 1.5 Observability

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| O1 | **LLM tracing (Langfuse)** | Every LLM call is traced (prompt/tokens/cost/latency) — a hard requirement in [Architecture.md](../../Architecture.md) §6 | Langfuse **Cloud EU** (SaaS) · self-host web+worker on ECS + D4 ClickHouse + D5 S3 | **Langfuse Cloud EU** — removes ClickHouse, MinIO and two more services from the estate. See DP-2 |
| O2 | **OTel collector** | Backend/worker export OTLP traces to `OPENPROGRAM_OTEL_EXPORTER_OTLP_ENDPOINT` | **ADOT collector** as an ECS sidecar · self-managed `otel-collector-contrib` | **ADOT sidecar** on C1 and C2 |
| O3 | **Trace backend** | Somewhere for O2 to ship spans | **AWS X-Ray** · Amazon Managed Grafana Tempo · self-hosted Jaeger | **X-Ray** |
| O4 | **Metrics** | `prometheus-client` exposes `/metrics`; Prometheus scrapes it | **Amazon Managed Service for Prometheus (AMP)** · self-hosted Prometheus on EC2 | **AMP**, scraped via the ADOT sidecar's remote-write |
| O5 | **Dashboards** | The repo ships a Grafana dashboard: [infra/grafana/dashboards/openprogram-foundation.json](../../infra/grafana/dashboards/openprogram-foundation.json) | **Amazon Managed Grafana (AMG)** · self-hosted Grafana | **AMG**, import the existing dashboard JSON |
| O6 | **Logs** | `structlog` JSON to stdout | **CloudWatch Logs** (`awslogs`/FireLens driver) · OpenSearch | **CloudWatch Logs** with a retention policy |
| O7 | **Alerting** | `/ready` degrades on Postgres, Timescale, Redis, LiteLLM and the dead-letter backlog | CloudWatch Alarms + SNS · AMG alerting | **CloudWatch Alarms → SNS → existing on-call channel** |

### 1.6 Networking & edge

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| N1 | **VPC** — 2–3 AZs, public + private subnets | Standard isolation; DB and Redis private-only | VPC | Required |
| N2 | **NAT Gateway** | Egress to Slack, Jira, GitHub/GitLab, Google Calendar, and the model provider | NAT Gateway · NAT instance | **NAT Gateway**, 1 per AZ in prod |
| N3 | **Public ALB + target group** | Slack **must** reach `POST /webhooks/slack` over public HTTPS; health check on `/health`, readiness on `/ready` | ALB · API Gateway HTTP API + VPC Link | **ALB** — simplest with the existing container |
| N4 | **TLS certificates** | HTTPS for the API and the SPA domain | **ACM** (free, auto-renew) | **ACM** |
| N5 | **DNS** | API + SPA hostnames per environment | **Route 53** · existing corporate DNS | Either; Route 53 if the zone can be delegated |
| N6 | **WAF** | `/webhooks/*` is CSRF-exempt and internet-facing ([api/main.py:170](../../backend/api/main.py#L170)); signature verification is in-app but rate limiting is not | **AWS WAF** on the ALB + CloudFront | **AWS WAF** with rate-based rules on `/webhooks/*` |
| N7 | **CloudFront** | SPA delivery + TLS + caching | CloudFront | Required with C3 |
| N8 | **VPC endpoints** | Private access to ECR, S3, Secrets Manager, CloudWatch, Bedrock — cuts NAT cost and keeps traffic off the internet | Gateway + Interface endpoints | Recommended |

### 1.7 Security & identity

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| S1 | **Secrets store** | Slack bot token + signing secret, Jira token, GitHub/GitLab token, Google Calendar token, OIDC client secret, LiteLLM master key, Langfuse keys, DB credentials, and `OPENPROGRAM_SECRET_KEY` | **Secrets Manager** (rotation, ECS-native injection) · SSM Parameter Store SecureString (cheaper) | **Secrets Manager** for credentials, Parameter Store for non-secret config |
| S2 | **KMS CMK** | The app stores **raw DM content** in Postgres; encryption at rest for RDS/EBS, S3, Secrets Manager, CloudWatch | **KMS** customer-managed key | **One CMK per environment** |
| S3 | **`OPENPROGRAM_SECRET_KEY` (Fernet)** | Application-level encryption for stored tokens and session material ([adapters/secrets/encrypted.py](../../backend/infra/adapters/secrets/encrypted.py)). Boot **refuses** the committed default when `environment != local` ([settings.py:559](../../backend/config/settings.py#L559)) | Generate a unique 44-char Fernet key per env, store in S1 | Required — one per environment, never reused |
| S4 | **OIDC identity provider** | `auth_provider` **must** be `oidc_bff` outside local ([settings.py:557](../../backend/config/settings.py#L557)); needs issuer URL, client ID, client secret, and a role-claim mapping to `DEV/PO/SM/MGR/EXEC/ADMIN` | **Corporate IdP (Entra ID / Okta)** · Amazon Cognito user pool | **Corporate IdP** — roles should come from existing groups. Cognito only if that is unavailable |
| S5 | **IAM roles** | ECS task execution role, task roles (Secrets Manager read, Bedrock invoke, AMP remote-write, X-Ray, CloudWatch), GitHub Actions deploy role | IAM + **GitHub OIDC provider** (no long-lived keys) | Required |
| S6 | **Backups & retention** | GDPR: `conversation_retention_days=30` with a purge cron; DB needs PITR | RDS automated backups + PITR · **AWS Backup** · EBS snapshots if self-managed | Required; agree retention with the DPO |

### 1.8 CI/CD

| # | Component | Why it is needed | AWS options | Recommendation |
|---|---|---|---|---|
| P1 | **Container registry** | CI currently pushes to **GHCR** ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)) | **Amazon ECR** (private, scan-on-push) · keep GHCR + pull-through cache | **ECR** — ECS pulls without cross-cloud credentials |
| P2 | **CI runner** | Existing pipeline: ruff, mypy strict, import-linter, pytest + coverage, Testcontainers integration tests, frontend build, image build | Keep **GitHub Actions** · CodeBuild/CodePipeline | **Keep GitHub Actions**, add an OIDC-assumed IAM role to push to ECR and deploy to ECS |
| P3 | **IaC** | Reproducible environments | Terraform · AWS CDK · CloudFormation | Whatever the DevOps team already standardises on |
| P4 | **Migration runner** | Alembic must run before/with each deploy (`make migrate`) | ECS one-off task in the deploy pipeline · CodeBuild step | **ECS run-task** gated before the service update |

### 1.9 External SaaS / non-AWS procurement

| # | Component | What is needed | Owner |
|---|---|---|---|
| X1 | **Slack app** | Bot token, signing secret, scopes (DM send/read, users:read for directory sync), public Request URL pointing at N3. See [docs/ops/slack-setup.md](slack-setup.md) | Workspace admin |
| X2 | **Jira Cloud** | Service account + API token, base URL, project keys for `OPENPROGRAM_JIRA_SYNC_PROJECTS`. Write-back is off by default (`jira_writeback_enabled=false`) | Jira admin |
| X3 | **GitHub _or_ GitLab** | PAT/app token + owner/namespace. Config supports both — `.env.example` defaults to GitLab, compose defaults to GitHub; **pick one per environment** | VCS admin |
| X4 | **Google Calendar** | API token + calendar ID for availability/PTO. `calendar_provider` also accepts `fake` for non-prod | Workspace admin |
| X5 | **Langfuse Cloud EU** | Org + project + API keys (if DP-2 chooses SaaS) | Procurement |

### 1.10 Environments

Minimum **two** deployed environments plus local: **dev/UAT** (fake or sandbox providers, single-AZ,
smallest instance sizes) and **prod** (Multi-AZ, real providers). See
[docs/ops/uat-runbook.md](uat-runbook.md). The estate above is therefore procured twice, with
prod-only Multi-AZ/HA.

---

## 2. Decision points that block procurement

These four need an answer before DevOps can size and cost anything.

### DP-1 — Postgres: TimescaleDB and Apache AGE are not available on RDS or Aurora

`facts` and `conversation_turns` are Timescale hypertables, and `/ready` returns degraded unless the
`age`, `timescaledb`, **and** `vector` extensions are all present. RDS and Aurora PostgreSQL offer
pgvector but neither TimescaleDB nor Apache AGE, so **two independent extensions** rule them out.
Dropping Timescale alone (option C) is therefore no longer sufficient to reach RDS/Aurora — AGE would
have to go too, or move to a Postgres platform that allows arbitrary extensions.

| Option | Pros | Cons |
|---|---|---|
| **A. Self-managed Postgres 16 on EC2** using the repo's custom image ([infra/docker/postgres/Dockerfile](../../infra/docker/postgres/Dockerfile)) | Zero code change; matches local exactly | DevOps owns patching, backups, failover, PITR |
| **B. Timescale Cloud on AWS** (Marketplace / PrivateLink) | Managed, keeps hypertables, EU regions available | Third-party vendor + contract; another data processor for GDPR review |
| **C. Drop TimescaleDB **and AGE** → plain RDS/Aurora** | Fully managed, cheapest to operate, two fewer extensions to track | Code change: convert 2 hypertables to partitioned/plain tables, update migrations `0001`/`0005`, remove the AGE write mirror again, and update the readiness probe. Larger than the Timescale-only change |

**Recommendation: A for the first deployment** — ship on EC2 now, since the custom image is the only
option that satisfies both TimescaleDB and AGE without code change. C remains the long-term target but
is now a two-part backend story (Timescale *and* AGE), so re-scope it before committing. Confirm
current RDS extension support with AWS before finalising, since supported-extension lists change.

### DP-2 — Langfuse: SaaS vs self-hosted

Self-hosting Langfuse v3 drags in **ClickHouse + MinIO/S3 + Redis + 2 more services** — roughly
doubling the estate for a component that is observability, not product.

**Recommendation: Langfuse Cloud EU.** Falls back to self-hosting on ECS if the data-residency or
vendor review fails. This removes D4 and D5 from the procurement list.

### DP-3 — Model provider: Bedrock vs OpenAI EU

LiteLLM makes this a config change, not a code change. Bedrock is AWS-native (IAM instead of a
long-lived API key, VPC endpoint, EU region, no external data processor). The current config pins
`gpt-5.5` — moving to Bedrock means selecting an equivalent model and re-validating prompt behaviour.

**Recommendation: Bedrock in eu-central-1** if a model of comparable quality is approved; otherwise
keep the OpenAI EU endpoint with the key in Secrets Manager.

### DP-4 — Identity provider for SSO

The app **cannot boot outside local without OIDC**. Needs issuer URL, client ID, client secret,
redirect URI (`{auth_public_backend_url}/api/v1/auth/callback`), and a claim that carries group/role
membership mappable to `DEV/PO/SM/MGR/EXEC/ADMIN` via `OPENPROGRAM_OIDC_ROLE_MAP_JSON`.

**Recommendation: corporate IdP (Entra ID / Okta)**, so roles derive from existing groups. Cognito
is the AWS fallback but means a second user directory.

---

## 3. Jira tickets

One epic, then stories in dependency order. Each is written so DevOps can act without reading this
whole document.

### EPIC — OpenProgram: AWS infrastructure provisioning

> **Description**
> Provision the AWS infrastructure for OpenProgram, an agentic program-management service
> (Python 3.12 / FastAPI backend, React SPA, DBOS durable workflows, PostgreSQL + Redis) in
> **eu-central-1**, for two environments: **dev/UAT** and **prod**.
> The app is already fully containerised — see `docker-compose.yml` for the exact service topology
> and `.env.example` for the complete configuration surface.
> Full component inventory and AWS service options: `docs/ops/infrastructure-procurement.md`.
>
> **Hard constraints**
> - EU data residency: the service stores raw chat/DM content and calls an LLM provider.
> - The backend requires a **public HTTPS endpoint** for Slack webhooks.
> - The backend **refuses to boot** outside `local` without an OIDC provider and a unique Fernet key.
>
> **Out of scope:** standalone vector database, standalone graph database, Temporal, Kafka/MSK,
> email/SES, file storage for the app itself. (pgvector and AGE are used, but as extensions on the
> application Postgres — no separate service to buy.)

---

#### OPS-1 — DECISION: choose the PostgreSQL platform (TimescaleDB constraint) 🚩 blocker
- **Type:** Task · **Priority:** Highest · **Blocks:** OPS-4, OPS-8
- **Context:** The app uses TimescaleDB hypertables (`facts`, `conversation_turns`) and its `/ready`
  probe asserts the `timescaledb` extension exists. RDS and Aurora PostgreSQL do **not** support it.
  The probe also asserts `age` and `vector` (restored by `0026_restore_vector_and_age_graph`).
  RDS/Aurora offer pgvector but **not** Apache AGE, so AGE independently rules them out — option (C)
  below no longer unblocks RDS/Aurora on its own.
- **Options:** (A) self-managed Postgres 16 on EC2 using `infra/docker/postgres/Dockerfile`;
  (B) Timescale Cloud on AWS; (C) backend change to drop Timescale, then plain RDS/Aurora.
- **Acceptance criteria:**
  - Current RDS/Aurora extension support re-confirmed with AWS in writing.
  - Option chosen and recorded, with cost and operational-ownership impact for both environments.
  - If A: patching, backup, PITR and failover ownership named.
  - If C: a linked backend story exists to convert the two hypertables and update the readiness probe.

#### OPS-2 — DECISION: Langfuse hosting (SaaS vs self-hosted) 🚩 blocker
- **Type:** Task · **Priority:** High · **Blocks:** OPS-13
- **Context:** LLM tracing is mandatory. Self-hosting Langfuse v3 adds ClickHouse, MinIO/S3, a Redis
  database and two services (see the `langfuse` profile in `docker-compose.yml`). Langfuse Cloud EU
  removes all of it.
- **Acceptance criteria:** vendor/DPA review completed for Langfuse Cloud EU; decision recorded; if
  self-hosting, ClickHouse and S3 stories are raised.

#### OPS-3 — DECISION: model provider (Amazon Bedrock vs OpenAI EU) 🚩 blocker
- **Type:** Task · **Priority:** High · **Blocks:** OPS-12
- **Context:** All model traffic goes through a LiteLLM proxy, so the provider is a config change in
  `infra/litellm/config.yaml`. Today: `gpt-5.5` via `https://eu.api.openai.com/v1`.
- **Acceptance criteria:** provider chosen; if Bedrock — model access requested in eu-central-1 and an
  equivalent model identified; if OpenAI — an EU org key is procured and stored in Secrets Manager.

#### OPS-4 — Provision the VPC and network foundation
- **Type:** Task · **Priority:** Highest
- **Scope:** VPC across 2–3 AZs (dev may be single-AZ); public + private subnets; NAT Gateway for
  egress to Slack, Jira, GitHub/GitLab, Google Calendar and the model provider; security groups so
  Postgres and Redis are reachable **only** from the backend and worker tasks; VPC endpoints for ECR,
  S3, Secrets Manager, CloudWatch Logs (and Bedrock if OPS-3 selects it).
- **Acceptance criteria:** IaC merged; DB/Redis have no public route; egress to the listed external
  hosts verified from a task in a private subnet.

#### OPS-5 — Provision the ECR repositories
- **Type:** Task · **Priority:** High
- **Scope:** Private repos for `openprogram-backend` (single image serves both the API and the worker;
  the worker just overrides the command). Scan-on-push, immutable tags, lifecycle policy.
- **Acceptance criteria:** an image built from `backend/Dockerfile` pushes and pulls successfully;
  ECS task roles can pull.

#### OPS-6 — Create the GitHub Actions → AWS OIDC deploy role
- **Type:** Task · **Priority:** High · **Depends on:** OPS-5
- **Scope:** GitHub OIDC identity provider in IAM + a least-privilege role for `ecr:Push`,
  `ecs:UpdateService` and `ecs:RunTask`. **No long-lived access keys.** CI currently pushes to GHCR
  (`.github/workflows/ci.yml`) and must be repointed at ECR.
- **Acceptance criteria:** a CI run on the default branch pushes to ECR using only the assumed role.

#### OPS-7 — Provision KMS keys and the secrets store
- **Type:** Task · **Priority:** Highest
- **Scope:** One customer-managed KMS key per environment (RDS/EBS, S3, Secrets Manager, CloudWatch).
  Secrets Manager entries for: `OPENPROGRAM_SECRET_KEY` (unique 44-char Fernet key per environment —
  the value committed to `.env.example` is **refused at boot** outside `local`), DB credentials, Slack
  bot token + signing secret, Jira API token, GitHub/GitLab token, Google Calendar token, OIDC client
  secret, LiteLLM master key, Langfuse public + secret keys. Non-secret config goes to SSM Parameter
  Store.
- **Acceptance criteria:** all secrets created (empty placeholders acceptable pending OPS-16..19); ECS
  task roles read them via `secrets:` injection, never via env vars in the task definition; rotation
  policy agreed for the tokens that support it.

#### OPS-8 — Provision PostgreSQL
- **Type:** Task · **Priority:** Highest · **Depends on:** OPS-1, OPS-4, OPS-7
- **Scope:** Postgres **16** per the OPS-1 decision. One database for the app; **the DBOS workflow
  system tables live in the same database** (`OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL`) — no separate
  instance needed. Encryption at rest with the OPS-7 CMK, private subnets only, automated backups +
  PITR, Multi-AZ in prod.
- **Sizing starting point:** connection pool is 1–5 per task (`OPENPROGRAM_POSTGRES_POOL_MAX_SIZE=5`)
  and check-in fan-out concurrency is 10, so a small instance class suffices initially; revisit after
  the first load profile.
- **Acceptance criteria:** `make migrate` (Alembic) runs clean to head; `GET /ready` reports the
  database and extension probes healthy; backup/restore rehearsed once.

#### OPS-9 — Provision ElastiCache for Redis
- **Type:** Task · **Priority:** Highest · **Depends on:** OPS-4, OPS-7
- **Scope:** Redis 7-compatible, private subnets, encryption in transit **and** at rest, Multi-AZ
  replica in prod. Used for rate limiting, reply debounce, send-once idempotency, **and the OIDC login
  session store — the backend will not start in OIDC mode without it**.
- **Acceptance criteria:** backend and worker connect over TLS; `GET /ready` reports the Redis probe
  healthy; failover tested in prod.

#### OPS-10 — Provision the ECS cluster, ALB, TLS and DNS
- **Type:** Task · **Priority:** Highest · **Depends on:** OPS-4, OPS-5
- **Scope:** ECS cluster (Fargate); public ALB; ACM certificate; Route 53 (or corporate DNS) records
  per environment. Target group health check `GET /health`; deployment gate on `GET /ready`.
  HTTP→HTTPS redirect.
- **Acceptance criteria:** `https://<api-host>/health` returns 200 from the internet; TLS grade
  acceptable to security; certificate auto-renewal confirmed.

#### OPS-11 — Deploy the backend API and worker services
- **Type:** Task · **Priority:** Highest · **Depends on:** OPS-8, OPS-9, OPS-10, OPS-7
- **Scope:** Two Fargate services from the **same image**:
  - `backend` — `uvicorn api.main:create_app --factory --port 8000`, behind the ALB, min 2 tasks in prod.
  - `worker` — `python -m infra.workflows.worker`, no ingress, 1 task to start. Owns all cron
    schedules (check-in fan-out `30 9 * * 1-5`, reconcile `*/15 * * * 1-5`, Jira sync hourly, GitHub
    sync `*/15`, calendar sync daily, risk + drift scans `*/30`, narrative briefs, conversation purge
    `0 3 * * *`, inbound-event sweeper `*/5`).
  - Alembic migrations run as an ECS one-off task **before** each service update.
  - Config via `OPENPROGRAM_*` env vars (full list in `.env.example`); secrets injected from OPS-7.
    Set `OPENPROGRAM_ENVIRONMENT` to a non-`local` value, which enforces OIDC auth and a unique Fernet key.
- **Acceptance criteria:** both services stable; `/ready` green on all probes; one scheduled workflow
  observed firing on time in dev; a rolling deploy completes with no failed requests.

#### OPS-12 — Deploy the LiteLLM proxy
- **Type:** Task · **Priority:** High · **Depends on:** OPS-3, OPS-10, OPS-7
- **Scope:** Internal-only Fargate service from `ghcr.io/berriai/litellm` (mirror to ECR), config
  mounted from `infra/litellm/config.yaml`, master key from Secrets Manager, health check
  `/health/readiness`. **Must not be reachable from the internet.** If Bedrock: task role with
  `bedrock:InvokeModel` + a Bedrock VPC endpoint.
- **Acceptance criteria:** backend completes a live model call through the proxy; the proxy is
  unreachable from outside the VPC; token spend visible in Langfuse or provider metrics.

#### OPS-13 — Set up LLM tracing (Langfuse)
- **Type:** Task · **Priority:** Medium · **Depends on:** OPS-2
- **Scope:** If SaaS: Langfuse Cloud EU org + project, keys into Secrets Manager, set
  `OPENPROGRAM_LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY/PROJECT_ID`. If self-hosted: `langfuse-web` +
  `langfuse-worker` on ECS plus ClickHouse and an S3 bucket (see the `langfuse` profile in
  `docker-compose.yml` for the full env contract).
- **Acceptance criteria:** an LLM call from the deployed backend appears as a trace with prompt,
  token counts, cost and latency.

#### OPS-14 — Set up metrics, traces, logs and dashboards
- **Type:** Task · **Priority:** Medium · **Depends on:** OPS-11
- **Scope:** ADOT collector sidecar on the backend and worker tasks (OTLP in, X-Ray for traces, AMP
  remote-write for metrics). Amazon Managed Prometheus workspace; Amazon Managed Grafana workspace
  with the existing dashboard `infra/grafana/dashboards/openprogram-foundation.json` imported.
  CloudWatch Logs groups with agreed retention.
- **Acceptance criteria:** a request trace is visible end-to-end; the imported Grafana dashboard
  renders live data; logs are queryable and correlate by trace ID.

#### OPS-15 — Set up alerting on readiness and workflow backlog
- **Type:** Task · **Priority:** Medium · **Depends on:** OPS-14
- **Scope:** Alarms on: `/ready` degraded (any probe), ALB 5xx rate, ECS task restarts, Postgres and
  Redis CPU/connections/memory, and the **dead-letter backlog** — `/ready` degrades when
  dead-lettered inbound chat events exceed `OPENPROGRAM_WORKFLOW_BACKLOG_READY_THRESHOLD`, which means
  Slack replies are being dropped. Route to SNS → the existing on-call channel.
- **Acceptance criteria:** each alarm fires in a deliberate test and reaches on-call; runbook links
  attached.

#### OPS-16 — Host the frontend SPA
- **Type:** Task · **Priority:** High · **Depends on:** OPS-10
- **Scope:** S3 bucket (private, OAC) + CloudFront + ACM + DNS per environment. SPA fallback routing
  to `index.html`. WAF on the distribution. The SPA is a pure static Vite build; the API host must be
  added to `OPENPROGRAM_CORS_ORIGINS` and to `OPENPROGRAM_AUTH_FRONTEND_URL`, and
  `OPENPROGRAM_AUTH_COOKIE_SECURE=true` must be set. Note the repo has two SPAs — `frontend/` and
  `frontend-v2/`; confirm with the dev team which is deployed per environment.
- **Acceptance criteria:** SPA loads over HTTPS, authenticates, and calls the API with no CORS or
  cookie errors; a deploy invalidates the CloudFront cache.

#### OPS-17 — Configure the OIDC identity provider 🚩 blocker for any non-local deploy
- **Type:** Task · **Priority:** Highest · **Depends on:** DP-4 decision
- **Scope:** Register an OIDC client in the corporate IdP (or create a Cognito user pool). Provide
  issuer URL, client ID, client secret, allowed redirect URI
  `{OPENPROGRAM_AUTH_PUBLIC_BACKEND_URL}/api/v1/auth/callback`, and scopes `openid profile email`.
  A claim must carry group membership mappable to the app roles `DEV`, `PO`, `SM`, `MGR`, `EXEC`,
  `ADMIN` via `OPENPROGRAM_OIDC_ROLE_MAP_JSON`.
- **Acceptance criteria:** login succeeds end-to-end from the SPA; a test user in each group resolves
  to the expected role; the backend boots with `OPENPROGRAM_AUTH_PROVIDER=oidc_bff`.

#### OPS-18 — Provision the Slack app and webhook path
- **Type:** Task · **Priority:** Highest · **Depends on:** OPS-10, OPS-11
- **Scope:** Slack app per environment with a bot token and signing secret; Event Subscriptions
  Request URL pointing at `https://<api-host>/webhooks/slack`; scopes for DM send/read and
  `users:read` (directory sync). Add a **WAF rate-based rule on `/webhooks/*`** — that path is
  CSRF-exempt by design and internet-facing (signature verification is in-app). See
  `docs/ops/slack-setup.md`.
- **Acceptance criteria:** Slack URL verification passes; a DM reply reaches the backend and is
  recorded; an invalid signature is rejected with 401.

#### OPS-19 — Procure integration credentials (Jira, VCS, Calendar)
- **Type:** Task · **Priority:** High · **Depends on:** OPS-7
- **Scope:** Jira Cloud service account + API token + base URL + project keys
  (`OPENPROGRAM_JIRA_SYNC_PROJECTS`); **either** a GitHub token + owner **or** a GitLab token +
  namespace ID — config supports both and the two default files disagree, so pick one per
  environment; Google Calendar token + calendar ID. All values into Secrets Manager.
  Note: Jira write-back is **off** by default (`OPENPROGRAM_JIRA_WRITEBACK_ENABLED=false`), so
  read-only scopes are sufficient until that is enabled.
- **Acceptance criteria:** each sync workflow completes once against the real provider in dev and
  writes facts to the database.

#### OPS-20 — Backups, retention and GDPR controls
- **Type:** Task · **Priority:** High · **Depends on:** OPS-8
- **Scope:** RDS/EBS automated backups + PITR, AWS Backup plan, restore rehearsal. Confirm with the
  DPO that `OPENPROGRAM_CONVERSATION_RETENTION_DAYS=30` and the nightly purge (`0 3 * * *`) satisfy
  policy — the app stores **raw DM content**, encrypted at rest with the OPS-7 CMK and never exposed
  through the API. Agree CloudWatch Logs retention.
- **Acceptance criteria:** restore rehearsed and timed; retention values signed off; the purge job
  observed deleting expired conversation turns.

#### OPS-21 — Stand up dev/UAT and prod as separate accounts or VPCs
- **Type:** Task · **Priority:** High
- **Scope:** Two full environments. Dev/UAT may use the `fake`/`mock_slack` provider values from
  `.env.example` and single-AZ, smallest sizes; prod uses real providers and Multi-AZ. Separate KMS
  keys, secrets, and Fernet keys per environment. See `docs/ops/uat-runbook.md`.
- **Acceptance criteria:** both environments deploy from the same IaC with only per-env variables
  differing; no shared secrets or keys between them.

#### OPS-22 — Produce the cost estimate
- **Type:** Task · **Priority:** Medium · **Depends on:** OPS-1, OPS-2, OPS-3
- **Scope:** Monthly run-rate for both environments across the components in
  `docs/ops/infrastructure-procurement.md`, with the DP-1/DP-2/DP-3 options priced separately so the
  trade-offs are visible. Include NAT Gateway, ALB, CloudFront, AMP/AMG, Secrets Manager and model
  inference.
- **Acceptance criteria:** estimate reviewed with the product owner; the largest three line items
  identified with a reduction option each.

---

## 4. What NOT to procure

Explicitly out of scope — listed so nobody provisions them by reading the local compose file literally:

| Not needed | Why |
|---|---|
| Vector database / OpenSearch vector engine | Embeddings live in `vector_items` on D1 via the pgvector extension — no separate vector service |
| Graph database (Neptune, Neo4j) | Graph reads are relational recursive CTEs on D1; the Apache AGE mirror is also a D1 extension — no separate graph service |
| Temporal Cloud / Temporal cluster | `OPENPROGRAM_WORKFLOW_PROVIDER=dbos` is the default; DBOS uses the app's Postgres |
| Kafka / MSK / SQS | No message broker in the design; Redis and Postgres carry queueing and idempotency |
| SES / SMTP | No email path in the app; notifications go through Slack |
| S3 for application data | The app does no file I/O — no `boto3`, no uploads. S3 is only needed if Langfuse is self-hosted |
| ClickHouse / MinIO | Langfuse v3 dependencies only — avoided entirely by Langfuse Cloud (DP-2) |
| EventBridge Scheduler | Cron is owned in-process by DBOS scheduled workflows in the worker |
