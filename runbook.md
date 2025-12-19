# Bisect Bot Runbook

Simple deployment: every server runs the same code. Each instance handles webhooks AND runs bisect jobs. Put N instances behind a load balancer.

---

## Architecture

```
┌─────────────────┐
│  Load Balancer  │
└────────┬────────┘
         │
    ┌────┴────┬────────┬────────┐
    ▼         ▼        ▼        ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│ Bot 1 │ │ Bot 2 │ │ Bot 3 │ │ Bot N │
│ web + │ │ web + │ │ web + │ │ web + │
│ docker│ │ docker│ │ docker│ │ docker│
└───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
    │         │        │        │
    └─────────┴────────┴────────┘
              │
       ┌──────┴──────┐
       │  PostgreSQL │
       │  (Supabase) │
       └─────────────┘
```

Each bot instance:
- Receives webhooks from GitHub
- Runs bisect jobs in Docker containers
- Writes job state to shared PostgreSQL

No Redis. No separate workers. Just scale horizontally.

---

## Configuration

All secrets in one file: `infrastructure/secrets.yml`

```yaml
# Hetzner
hcloud_token: "your-api-token"
ssh_public_key: "ssh-rsa AAAA..."

# Domain
domain: "bisect.example.com"
admin_email: "you@example.com"

# Supabase (or any PostgreSQL)
database_url: "postgresql://user:pass@host:5432/bisect"

# GitHub App
github_app_id: "123456"
github_webhook_secret: "your_webhook_secret"  # openssl rand -hex 32
github_private_key: |
  -----BEGIN RSA PRIVATE KEY-----
  (your .pem contents)
  -----END RSA PRIVATE KEY-----

# Encryption key for sensitive fields
encryption_key: "generate-with-fernet"
```

### Environment Variables

```bash
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_PATH=/app/secrets/private-key.pem
GITHUB_WEBHOOK_SECRET=your_secret
DATABASE_URL=postgresql://user:pass@host:5432/bisect
DOCKER_RUNNER_IMAGE=bisect-runner:latest
MAX_CONCURRENT_JOBS=4  # Tune per server capacity
```

---

## Quick Start

### 1. Configure secrets

```bash
cd infrastructure
cp secrets.example.yml secrets.yml
# Edit secrets.yml with your values (database URL, GitHub App, etc.)
```

### 2. Provision infrastructure

```bash
cd terraform
terraform init && terraform apply -var-file=../secrets.yml
```

### 3. Set up the database

Before deploying any servers, ensure your PostgreSQL database is ready:

```bash
# If using Supabase: database is already provisioned
# If self-hosting: ensure PostgreSQL is running and accessible

# Test connection from your local machine
psql $DATABASE_URL -c "SELECT 1"
```

### 4. Run database migrations

Migrations must complete before any bot instances start:

```bash
# From a machine with database access (your local machine or a bastion)
export DATABASE_URL="postgresql://user:pass@host:5432/bisect"
alembic upgrade head
```

### 5. Deploy bot instances

Now that the database schema is ready, deploy the servers:

```bash
cd ../ansible
ansible-playbook site.yml
```

### 6. Scale

Add more servers via Terraform, point them at the same database. Done.

---

## Server Setup

Each server needs:
- Docker installed
- Access to PostgreSQL
- GitHub App private key

That's it. The same Docker image runs everywhere.

---

## Operations

### SSH to server

```bash
ssh deploy@YOUR_SERVER_IP
cd /opt/bisect-bot
```

### View logs

```bash
docker compose logs -f bot
```

### Deploy updates

**Important: Always run migrations before starting the new version.**

```bash
# Pull latest code
git pull

# Build new images
docker compose build

# Run migrations BEFORE starting new containers
docker compose run --rm bot alembic upgrade head

# Start the updated bot
docker compose up -d
```

### Check health

```bash
curl https://your-domain.com/health
# {"status": "healthy", "docker_available": true, "running_jobs": 0}
```

### View stats

```bash
curl https://your-domain.com/stats
# {"pending": 0, "running": 1, "completed": 42, "failed": 2, "running_on_this_instance": 1}
```

---

## Scaling

### Vertical: More jobs per server

```bash
# In .env or docker-compose.yml
MAX_CONCURRENT_JOBS=8
```

### Horizontal: More servers

1. Add servers via Terraform (or manually)
2. Point them at the same database (already migrated)
3. Put behind load balancer

Each server is identical. Health checks at `/health`.

### Load Balancer Setup

Any load balancer works. Configure:
- Health check: `GET /health`
- Sticky sessions: Not required
- Protocol: HTTPS

Example with Caddy (reverse proxy):
```
bisect.example.com {
    reverse_proxy server1:8000 server2:8000 server3:8000 {
        health_uri /health
        health_interval 10s
    }
}
```

---

## Local Development

```bash
# 1. Start database
docker compose up -d postgres

# 2. Run migrations (database must be ready first)
alembic upgrade head

# 3. Build runner image
docker build -t bisect-runner:latest -f docker/Dockerfile.runner docker/

# 4. Start the bot
python -m app.main
```

For webhook testing, use ngrok:
```bash
ngrok http 8000
# Update GitHub App webhook URL with ngrok URL
```

---

## Troubleshooting

### Job stuck?

```bash
# Check what's running on this instance
curl localhost:8000/stats

# Check database for all jobs
docker compose exec bot python -c "
from app.database import SessionLocal
from app.models import BisectJob, JobStatus
db = SessionLocal()
running = db.query(BisectJob).filter(BisectJob.status == JobStatus.RUNNING).all()
for j in running: print(f'{j.id}: started {j.started_at}')
"
```

### Docker issues?

```bash
# Check docker is accessible
docker ps

# Check runner image exists
docker images | grep bisect-runner

# Rebuild runner
docker build -t bisect-runner:latest -f docker/Dockerfile.runner docker/
```

### Database connection?

```bash
# Test connection
docker compose exec bot python -c "
from app.database import engine
print(engine.execute('SELECT 1').scalar())
"
```

### Migration failed?

```bash
# Check current migration state
docker compose run --rm bot alembic current

# See migration history
docker compose run --rm bot alembic history

# If stuck, check for lock issues in PostgreSQL
psql $DATABASE_URL -c "SELECT * FROM pg_locks WHERE NOT granted;"
```

---

## File Locations

| Path | Purpose |
|------|---------|
| `infrastructure/secrets.yml` | All secrets (never commit!) |
| `/opt/bisect-bot/` | App on server |
| `/opt/bisect-bot/.env` | Runtime config |
| `/opt/bisect-bot/secrets/` | GitHub private key |

---

## Job Recovery

Jobs are resilient to server restarts and crashes.

### How It Works

1. **Worker ID**: Each instance has a unique ID (hostname + pid + timestamp)
2. **Heartbeats**: Running jobs send heartbeats every 60 seconds
3. **Recovery Loop**: Every 30 seconds, each instance scans for orphaned jobs:
   - Jobs marked RUNNING but no heartbeat in 5 minutes → recovered
   - Jobs stuck in PENDING for >30 seconds → picked up
4. **Attempt Limit**: Jobs are retried up to 3 times before marking as failed

### What Happens on Crash

1. Server A is running job #42, sends heartbeats
2. Server A crashes (no graceful shutdown)
3. Heartbeat stops updating
4. After 5 minutes, Server B notices job #42 is stale
5. Server B claims job #42, restarts from beginning
6. Job continues and completes

### Graceful Shutdown

On graceful shutdown (SIGTERM):
1. Running jobs are reset to PENDING (not counted as an attempt)
2. Another instance picks them up immediately

### Monitoring Orphaned Jobs

```bash
# Find stale jobs (running but no recent heartbeat)
curl localhost:8000/stats

# Check specific job
curl localhost:8000/job/42
# Returns worker_id, heartbeat_at, attempt_count
```

---

## Key Differences from Old Architecture

| Before | After |
|--------|-------|
| Separate web + worker processes | Single unified process |
| Redis for job queue | Jobs run inline (background tasks) |
| Scale workers independently | Scale everything together |
| Complex orchestration | Simple: same code everywhere |
| Jobs lost on crash | Jobs automatically recovered |
