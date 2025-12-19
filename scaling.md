# Scaling the GitHub Bisect Bot

This document describes how to scale the GitHub Bisect Bot service to handle increased load and concurrent bisect operations.

## Architecture Overview

The service uses a simplified architecture where every instance is identical—each handles webhooks AND runs bisect jobs. Scale horizontally by adding more instances behind a load balancer.

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

### Components

1. **Bot Instances**: Each instance receives webhooks from GitHub, processes them, and runs bisect jobs in Docker containers
2. **PostgreSQL (Supabase)**: Stores job state, enables coordination between instances, and persists results
3. **Load Balancer**: Distributes webhook traffic across instances

### Why No Redis?

- **Simpler architecture**: One database for everything
- **Fewer moving parts**: No Redis to configure, monitor, or scale
- **Built-in persistence**: Job state survives restarts without additional configuration
- **Supabase benefits**: Managed PostgreSQL with connection pooling, automatic backups, and row-level security

## Scaling Instances

Each instance handles both webhooks and job processing. Scale by adding more instances.

### Using Docker Compose

Scale instances instantly using the `--scale` flag:

```bash
# Start with 3 instances
docker compose up -d --scale bot=3

# Scale up to 5 instances during high load
docker compose up -d --scale bot=5

# Scale back down
docker compose up -d --scale bot=2
```

### Using Docker Swarm

For production deployments with Docker Swarm:

```bash
# Deploy the stack
docker stack deploy -c docker-compose.yml bisect

# Scale instances
docker service scale bisect_bot=5

# Check status
docker service ls
docker service ps bisect_bot
```

### Using Kubernetes

For Kubernetes deployments:

```yaml
# kubernetes/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bisect-bot
spec:
  replicas: 3
  selector:
    matchLabels:
      app: bisect-bot
  template:
    metadata:
      labels:
        app: bisect-bot
    spec:
      containers:
      - name: bot
        image: your-registry/bisect-bot:latest
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: bisect-secrets
              key: database-url
        - name: GITHUB_APP_ID
          valueFrom:
            secretKeyRef:
              name: bisect-secrets
              key: github-app-id
        - name: GITHUB_PRIVATE_KEY_PATH
          value: /app/secrets/private-key.pem
        - name: GITHUB_WEBHOOK_SECRET
          valueFrom:
            secretKeyRef:
              name: bisect-secrets
              key: github-webhook-secret
        - name: MAX_CONCURRENT_JOBS
          value: "4"
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "2000m"
            memory: "2Gi"
        volumeMounts:
        - name: github-key
          mountPath: /app/secrets
          readOnly: true
        - name: docker-socket
          mountPath: /var/run/docker.sock
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
      volumes:
      - name: github-key
        secret:
          secretName: github-private-key
      - name: docker-socket
        hostPath:
          path: /var/run/docker.sock
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bisect-bot-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bisect-bot
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

Scale with kubectl:

```bash
# Manual scaling
kubectl scale deployment bisect-bot --replicas=5

# Check status
kubectl get pods -l app=bisect-bot
kubectl get hpa bisect-bot-hpa
```

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL (Supabase) | Required |
| `MAX_CONCURRENT_JOBS` | Jobs per instance | `4` |
| `BISECT_TIMEOUT_SECONDS` | Max time per bisect job | `1800` (30 min) |

### Resource Limits

Each instance and its spawned Docker containers consume resources:

| Component | CPU | Memory |
|-----------|-----|--------|
| Bot instance | 0.5-2 cores | 512MB-2GB |
| Bisect container | 1 core (limited) | 2 GB (limited) |

Plan your scaling based on available resources:
- **4-core, 8GB server**: 1-2 instances with MAX_CONCURRENT_JOBS=2
- **8-core, 16GB server**: 2-3 instances with MAX_CONCURRENT_JOBS=4
- **16-core, 32GB server**: 4-6 instances with MAX_CONCURRENT_JOBS=4

## Monitoring

### Job Statistics

The API exposes job statistics:

```bash
# Get stats
curl http://localhost:8000/stats

# Response
{
  "pending": 5,
  "running": 2,
  "completed": 150,
  "failed": 3,
  "running_on_this_instance": 1
}
```

### Job Status

Check individual job status:

```bash
curl http://localhost:8000/job/{job_id}

# Response
{
  "id": 42,
  "status": "completed",
  "created_at": "2024-01-15T10:30:00Z",
  "started_at": "2024-01-15T10:30:05Z",
  "finished_at": "2024-01-15T10:35:22Z",
  "worker_id": "bot-1-12345",
  "attempt_count": 1
}
```

### Health Checks

```bash
curl http://localhost:8000/health

# Response
{
  "status": "healthy",
  "docker_available": true,
  "running_jobs": 2
}
```

## Scaling Strategies

### 1. Horizontal Scaling (Recommended)

Add more identical instances behind a load balancer:

```bash
# Add servers via Terraform
terraform apply -var="instance_count=5"

# Or scale Docker Compose
docker compose up -d --scale bot=5
```

Each instance:
- Receives webhooks
- Runs up to MAX_CONCURRENT_JOBS bisect operations
- Coordinates via PostgreSQL

### 2. Vertical Scaling

Increase jobs per instance by adjusting MAX_CONCURRENT_JOBS:

```bash
# In .env or docker-compose.yml
MAX_CONCURRENT_JOBS=8
```

### 3. Database Scaling

Supabase handles connection pooling automatically. For high traffic:
- Use the pooled connection string (port 6543)
- Enable connection pooling in Supabase dashboard
- Consider upgrading Supabase plan for more connections

## Job Recovery

Jobs are resilient to instance restarts and crashes.

### How It Works

1. **Worker ID**: Each instance has a unique ID (hostname + pid + timestamp)
2. **Heartbeats**: Running jobs send heartbeats every 60 seconds
3. **Recovery Loop**: Every 30 seconds, each instance scans for orphaned jobs:
   - Jobs marked RUNNING but no heartbeat in 5 minutes → recovered
   - Jobs stuck in PENDING for >30 seconds → picked up
4. **Attempt Limit**: Jobs are retried up to 3 times before marking as failed

### What Happens on Crash

1. Instance A is running job #42, sends heartbeats
2. Instance A crashes (no graceful shutdown)
3. Heartbeat stops updating
4. After 5 minutes, Instance B notices job #42 is stale
5. Instance B claims job #42, restarts from beginning
6. Job continues and completes

## Load Balancer Setup

Any load balancer works. Configure:
- Health check: `GET /health`
- Sticky sessions: Not required
- Protocol: HTTPS

### Example with Caddy

```
bisect.example.com {
    reverse_proxy bot1:8000 bot2:8000 bot3:8000 {
        health_uri /health
        health_interval 10s
    }
}
```

### Example with nginx

```nginx
upstream bisect {
    server bot1:8000;
    server bot2:8000;
    server bot3:8000;
}

server {
    listen 443 ssl http2;
    server_name bisect.example.com;

    location / {
        proxy_pass http://bisect;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /health {
        proxy_pass http://bisect;
    }
}
```

## Troubleshooting

### Jobs Not Processing

1. Check instance health:
   ```bash
   curl localhost:8000/health
   ```

2. Check database connectivity:
   ```bash
   docker compose exec bot python -c "
   from app.database import engine
   print(engine.execute('SELECT 1').scalar())
   "
   ```

3. Verify pending jobs:
   ```bash
   curl localhost:8000/stats
   ```

### Orphaned Jobs

Jobs stuck in RUNNING state with stale heartbeats:

```bash
# Check for stale jobs (running but no recent heartbeat)
docker compose exec bot python -c "
from app.database import SessionLocal
from app.models import BisectJob, JobStatus
from datetime import datetime, timedelta
db = SessionLocal()
stale = db.query(BisectJob).filter(
    BisectJob.status == JobStatus.RUNNING,
    BisectJob.heartbeat_at < datetime.utcnow() - timedelta(minutes=5)
).all()
for j in stale: print(f'{j.id}: last heartbeat {j.heartbeat_at}')
"
```

Recovery happens automatically, but you can force it by restarting any instance.

### Memory Issues

1. Monitor container memory:
   ```bash
   docker stats
   ```

2. Reduce MAX_CONCURRENT_JOBS or add more instances

### Docker Socket Permissions

If instances can't spawn containers:

```bash
# Add docker group permissions
sudo usermod -aG docker $USER

# Or use TCP socket with TLS
export DOCKER_HOST=tcp://localhost:2376
```

## Best Practices

1. **Start Small**: Begin with 2 instances and scale based on observed load
2. **Monitor Job Stats**: Keep pending jobs low (< 10 per instance)
3. **Set Timeouts**: Use `BISECT_TIMEOUT_SECONDS` to prevent runaway jobs
4. **Use Health Checks**: Monitor `/health` endpoint for system status
5. **Log Aggregation**: Collect instance logs for debugging (use Loki, ELK, etc.)
6. **Resource Limits**: Always set CPU/memory limits to prevent resource exhaustion
7. **Connection Pooling**: Use Supabase pooled connections for production
