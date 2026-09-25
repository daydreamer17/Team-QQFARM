# Local development launchers

This directory keeps the one-command local development launchers together.
They start the PostgreSQL dependency, apply database migrations, initialise
LangGraph checkpoints, and then start the API, worker, and frontend.

## macOS

Run from the repository root:

```bash
./scripts/dev/setup.sh
./scripts/dev/start.sh
```

The start command accepts optional ports:

```bash
./scripts/dev/start.sh --api-port 8000 --frontend-port 5173
```

Stop the application services while keeping PostgreSQL running:

```bash
./scripts/dev/stop.sh
```

Stop the application services and PostgreSQL:

```bash
./scripts/dev/stop.sh --postgres
```

## Windows PowerShell

Run from the repository root:

```powershell
.\scripts\dev\setup.ps1
.\scripts\dev\start.ps1
.\scripts\dev\stop.ps1
```

Runtime logs are written to `logs/dev/`. Process state is stored under
`.local-data/`; both locations are ignored by Git.

These stop commands never delete Docker volumes. Do not use
`docker compose down -v` unless permanent local data deletion is intentional.
