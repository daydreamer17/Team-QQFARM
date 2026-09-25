# Local Development Environment

[简体中文](LOCAL_ENVIRONMENT.md) · English

The one-command scripts in this repository are recommended. They start PostgreSQL, run migrations, initialize LangGraph checkpoints, and start the API, worker, and frontend.

## 1. Prerequisites

- Python 3.11 or later
- Node.js 22 and npm
- Docker Desktop
- Valid model, embedding, and reranking service credentials when using live extraction, RAG, or AI features

Do not commit `.env`. First-time setup creates the local configuration file from `.env.example`.

## 2. macOS

First-time setup:

```bash
cd /path/to/Team-QQFARM
./scripts/dev/setup.sh
```

Review `.env`, replace at least the model-service credentials, and start the application:

```bash
./scripts/dev/start.sh
```

Custom ports may be supplied:

```bash
./scripts/dev/start.sh --api-port 8000 --frontend-port 5173
```

Stop application processes while keeping PostgreSQL running:

```bash
./scripts/dev/stop.sh
```

Stop the PostgreSQL container as well while preserving its data volume:

```bash
./scripts/dev/stop.sh --postgres
```

## 3. Windows PowerShell

```powershell
cd C:\path\to\Team-QQFARM
.\scripts\dev\setup.ps1
.\scripts\dev\start.ps1
```

Stop the application:

```powershell
.\scripts\dev\stop.ps1
```

## 4. Service endpoints

| Service | Default URL |
| --- | --- |
| Frontend | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:8000` |
| Swagger | `http://127.0.0.1:8000/docs` |
| API liveness | `http://127.0.0.1:8000/health/live` |
| API readiness | `http://127.0.0.1:8000/health/ready` |
| Worker heartbeat | `http://127.0.0.1:8000/health/worker` |

Logs are written to `logs/dev/` and process state to `.local-data/`; neither should be committed to Git.

## 5. Key environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string |
| `QUOTE_STORAGE_PATH` | Quotation and extraction file storage |
| `POLICY_UPLOAD_STORAGE_PATH` | Uploaded policy file storage |
| `SUPPLIER_MODEL_*` | Primary LLM configuration |
| `SUPPLIER_EMBEDDING_*` | Policy vector-retrieval configuration |
| `SUPPLIER_RERANK_*` | Policy reranking configuration |
| `SUPPLIER_CONVERSATION_MODEL_*` | Optional decision-assistant model override; reuses the primary model when unset |
| `SUPPLIER_AGENT_ENABLED` | Enables the optional investigation agent; defaults to `false` |
| `SUPPLIER_PDF_OCR_ENABLED` | Enables OCR; defaults to `false` |

Refer to `.env.example` for exact defaults and options. An independently configured provider must not silently fall back; if the target provider is unavailable, the operation must fail explicitly.

## 6. Manual startup for troubleshooting

If the one-command script fails, run the following commands separately to isolate the problem.

Start the database and run migrations:

```bash
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m alembic upgrade head
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m supplier_comparison.checkpoints setup
```

Terminal 1: API

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

Terminal 2: Worker

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop --poll-interval 1
```

Terminal 3: Frontend

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

## 7. Troubleshooting

| Symptom | Check first |
| --- | --- |
| Historical tasks fail to load | Whether PostgreSQL uses the original data volume, migrations succeeded, and the API is ready |
| An upload remains waiting | Worker heartbeat and `logs/dev/worker.err.log` |
| `worker_failed` | Worker error log, model credentials, model-schema validation, and current task revision |
| Policy publishing fails | Embedding configuration, database `vector` extension, and policy-clause review status |
| Compliance checks do not run | Whether the task binds a published policy, quotations are formally submitted, and the worker is running |
| AI assistant fails | Conversation-model configuration and worker logs; the failure does not change deterministic comparison data |
| The frontend still shows an old page | Current Git branch, Vite process working directory, and a hard browser refresh |

The stop scripts do not remove Docker volumes. Unless you explicitly intend to erase all local business data, do not run `docker compose down -v`.
