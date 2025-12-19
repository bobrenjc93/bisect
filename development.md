# Development Guide

This guide covers setting up a local development environment for the GitHub Bisect Bot.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Creating a GitHub App](#creating-a-github-app)
- [Creating a GitHub OAuth App](#creating-a-github-oauth-app)
- [Environment Configuration (.env)](#environment-configuration-env)
- [Development Workflow](#development-workflow)
- [Running Tests](#running-tests)
- [Database Management](#database-management)
- [Working with GitHub Webhooks](#working-with-github-webhooks)
- [Code Structure](#code-structure)
- [Common Tasks](#common-tasks)
- [Troubleshooting](#troubleshooting)
- [Environment Variables](#environment-variables)

---

## Prerequisites

- **Docker** and **Docker Compose** - that's it!
- **Git** for version control

All dependencies (Python, PostgreSQL, etc.) run inside Docker containers.

### Installing Docker on Mac

1. **Download Docker Desktop** from the official website:
   - Visit [https://www.docker.com/products/docker-desktop/](https://www.docker.com/products/docker-desktop/)
   - Click "Download for Mac" and select the appropriate version:
     - **Apple Silicon** (M1/M2/M3/M4 chips)
     - **Intel chip**

2. **Install Docker Desktop**:
   - Open the downloaded `.dmg` file
   - Drag the Docker icon to your Applications folder
   - Open Docker from Applications

3. **Complete setup**:
   - Docker Desktop will ask for permissions—grant them
   - Wait for Docker to start (you'll see the whale icon in your menu bar)
   - The whale icon will animate while Docker is starting, and become steady when ready

4. **Verify installation**:
   ```bash
   docker --version
   docker compose version
   ```

5. **Recommended settings** (optional):
   - Open Docker Desktop → Settings → Resources
   - Allocate at least 4 GB of memory for smooth operation
   - Enable "Use Virtualization framework" for better performance on Apple Silicon

> **Note**: Docker Desktop includes Docker Compose, so no separate installation is needed.

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-org/bisect.git
cd bisect

# 2. Copy the example environment file
cp .env.example .env

# 3. Create a dummy private key for development
mkdir -p secrets
openssl genrsa -out secrets/private-key.pem 2048

# 4. Start everything
docker compose up --build

# 5. Visit http://localhost:8000/docs to see the API
```

That's it! The application is now running with:
- FastAPI server on port 8000
- PostgreSQL database
- All migrations applied automatically
- Web UI at http://localhost:8000

---

## Creating a GitHub App

A GitHub App is required for the bisect bot to interact with repositories (clone code, post comments). This is different from the OAuth App used for user login.

### Step-by-Step: Create a GitHub App

1. **Go to GitHub Developer Settings**:
   - Navigate to [https://github.com/settings/apps](https://github.com/settings/apps)
   - Or: GitHub → Settings → Developer settings → GitHub Apps

2. **Click "New GitHub App"**

3. **Fill in Basic Information**:

   | Field | Value for Development |
   |-------|----------------------|
   | **GitHub App name** | `Bisect Bot Dev [your-username]` (must be globally unique) |
   | **Homepage URL** | `http://localhost:8000` |
   | **Webhook URL** | `https://example.com/webhook` (placeholder, update later with ngrok URL) |
   | **Webhook secret** | Generate one: `openssl rand -hex 20` |

   > **Note**: GitHub requires a Webhook URL. Use `https://example.com/webhook` as a placeholder during initial setup. When you're ready to test real webhooks, update this to your ngrok URL (see [Using ngrok for Real Webhook Testing](#using-ngrok-for-real-webhook-testing)).

4. **Set Repository Permissions**:

   | Permission | Access Level | Why Needed |
   |------------|--------------|------------|
   | **Contents** | Read-only | Clone repositories for bisection |
   | **Issues** | Read & write | Post bisect results as comments |
   | **Metadata** | Read-only | Access repository information |

5. **Subscribe to Events**:
   - ✅ **Issue comment** (triggers bisect on `/bisect` command)
   - ✅ **Installation** (tracks when app is installed/removed)
   - ✅ **Installation repositories** (tracks repo changes)

6. **Where can this GitHub App be installed?**
   - Select **"Only on this account"** for development

7. **Click "Create GitHub App"**

### Getting Your App ID

After creating the app, you'll be redirected to the app settings page:

1. **Find the App ID**: It's displayed at the top of the page, right under the app name
   - Look for: "App ID: **123456**"
   - This is your `GITHUB_APP_ID`

2. **Note the App Slug**: The URL-friendly name in the URL
   - Example: `https://github.com/settings/apps/bisect-bot-dev-yourname`
   - The slug is `bisect-bot-dev-yourname`

### Generate a Private Key

1. Scroll down to the **"Private keys"** section
2. Click **"Generate a private key"**
3. A `.pem` file will download automatically
4. **Save this file** to your project:
   ```bash
   mkdir -p secrets
   mv ~/Downloads/your-app-name.*.private-key.pem secrets/private-key.pem
   chmod 600 secrets/private-key.pem
   ```

### Summary: What You Should Have

After these steps, you should have:

```bash
# In your .env file or environment
GITHUB_APP_ID=123456                    # From app settings page
GITHUB_WEBHOOK_SECRET=your-secret-here  # The one you generated

# In your project
secrets/private-key.pem                 # Downloaded private key
```

---

## Creating a GitHub OAuth App

A GitHub OAuth App is needed for the **web UI** to authenticate users. This is separate from the GitHub App used for webhooks.

### Step-by-Step: Create an OAuth App

1. **Go to GitHub Developer Settings**:
   - Navigate to [https://github.com/settings/developers](https://github.com/settings/developers)
   - Or: GitHub → Settings → Developer settings → OAuth Apps

2. **Click "New OAuth App"**

3. **Fill in the Application Details**:

   | Field | Value for Development |
   |-------|----------------------|
   | **Application name** | `Bisect Bot UI Dev` |
   | **Homepage URL** | `http://localhost:8000` |
   | **Authorization callback URL** | `http://localhost:8000/auth/callback` |

   > **Important**: The callback URL must match exactly! For local development, use `http://localhost:8000/auth/callback`

4. **Click "Register application"**

### Getting Your OAuth Credentials

After creating the OAuth App:

1. **Copy the Client ID**: Displayed on the app page
   - Example: `Iv1.abc123def456ghi7`
   - This is your `GITHUB_CLIENT_ID`

2. **Generate a Client Secret**:
   - Click **"Generate a new client secret"**
   - **Copy it immediately** (it's only shown once!)
   - This is your `GITHUB_CLIENT_SECRET`

### Add to Your Environment

Add these to your `.env` file:

```bash
# GitHub OAuth (for UI login)
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7
GITHUB_CLIENT_SECRET=your-client-secret-here
BASE_URL=http://localhost:8000
SESSION_SECRET=generate-a-random-string-here
```

Generate a session secret:
```bash
openssl rand -hex 32
```

### Testing the UI

1. Start the application:
   ```bash
   docker compose up
   ```

2. Visit http://localhost:8000

3. Click **"Sign in with GitHub"**

4. You'll be redirected to GitHub to authorize the app

5. After authorization, you'll be redirected back to the dashboard

---

## Environment Configuration (.env)

### Minimal Setup (Recommended for Getting Started)

For basic local development with Docker Compose, **you don't need to set any environment variables**. All settings have sensible defaults that work out of the box:

```bash
# .env file can be empty or not exist at all for basic testing
```

The defaults are configured for Docker Compose development:
- Database URL points to the `postgres` container automatically
- `DEV_MODE` is enabled via `docker-compose.override.yml`
- A dummy private key is used if none exists (in dev mode)

### When You Need to Set Variables

| Scenario | Variables to Set |
|----------|------------------|
| **Basic local dev** | None required |
| **Testing with real GitHub webhooks** | `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, + real private key |
| **Using ngrok** | `NGROK_AUTHTOKEN` |
| **Production deployment** | All production variables (see [Environment Variables Reference](#environment-variables)) |

### Variable Details

| Variable | Required? | Default | Notes |
|----------|-----------|---------|-------|
| `DEV_MODE` | No | `true` (via docker-compose.override.yml) | Enables relaxed security, API docs |
| `DATABASE_URL` | No | `postgresql://bisect:changeme@postgres:5432/bisect` | Works automatically with Docker Compose |
| `GITHUB_APP_ID` | No | `12345` | Only needed for real GitHub integration |
| `GITHUB_APP_SLUG` | No | `bisect-bot` | App URL slug for "Install on Repository" link |
| `GITHUB_PRIVATE_KEY_PATH` | No | N/A | **Don't set in .env** — place key file at `secrets/private-key.pem` |
| `GITHUB_WEBHOOK_SECRET` | No | `dev-secret-at-least-16-chars` | Only validated in production mode |
| `GITHUB_CLIENT_ID` | For UI | None | OAuth Client ID for user login |
| `GITHUB_CLIENT_SECRET` | For UI | None | OAuth Client Secret for user login |
| `BASE_URL` | For UI | `http://localhost:8000` | Base URL for OAuth callbacks |
| `SESSION_SECRET` | For UI | `dev-session-secret...` | Secret for signing session cookies |
| `DOCKER_RUNNER_IMAGE` | No | `bisect-runner:latest` | Change if using custom runner |
| `BISECT_TIMEOUT_SECONDS` | No | `1800` (30 min) | Max time per bisect job |
| `MAX_CONCURRENT_JOBS` | No | `4` | Parallel bisect limit |
| `ENCRYPTION_KEY` | No | None | For encrypting sensitive data at rest |

### Example .env Files

**Empty / Minimal (just run tests and explore):**
```bash
# No variables needed - all defaults work
# Note: Web UI login won't work without OAuth credentials
```

**For web UI development (OAuth login):**
```bash
# Get these from your GitHub OAuth App settings
# See "Creating a GitHub OAuth App" section above
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7
GITHUB_CLIENT_SECRET=your-client-secret-here
BASE_URL=http://localhost:8000
SESSION_SECRET=your-random-session-secret-here
```

**For real GitHub App testing (webhooks + UI):**
```bash
# GitHub App (for webhooks and repo access)
# Get these from your GitHub App settings page
GITHUB_APP_ID=123456
GITHUB_WEBHOOK_SECRET=your-webhook-secret-here
# Also place your private key at secrets/private-key.pem

# GitHub OAuth (for UI login)
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7
GITHUB_CLIENT_SECRET=your-client-secret-here
BASE_URL=http://localhost:8000
SESSION_SECRET=your-random-session-secret-here
```

**With ngrok for webhook testing:**
```bash
GITHUB_APP_ID=123456
GITHUB_WEBHOOK_SECRET=your-webhook-secret-here
NGROK_AUTHTOKEN=your-ngrok-token

# OAuth still uses localhost for callback
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7
GITHUB_CLIENT_SECRET=your-client-secret-here
BASE_URL=http://localhost:8000
SESSION_SECRET=your-random-session-secret-here
```

---

## Development Workflow

### Starting the Development Environment

```bash
# Start all services (foreground, with logs)
docker compose up --build

# Or start in the background
docker compose up -d --build

# View logs
docker compose logs -f bot
```

### Stopping the Environment

```bash
# Stop all services
docker compose down

# Stop and remove volumes (resets database)
docker compose down -v
```

### Rebuilding After Code Changes

The `docker-compose.override.yml` mounts your source code, so many changes are reflected automatically. For dependency changes or Dockerfile updates:

```bash
docker compose up --build
```

### Development Mode Features

The development environment automatically sets `DEV_MODE=true`, which enables:
- API documentation at `/docs` and `/redoc`
- Webhook signature verification bypass
- Relaxed security validation

---

## Running Tests

### Run All Tests

```bash
docker compose run --rm bot pytest
```

### Run Specific Tests

```bash
# Run a specific test file
docker compose run --rm bot pytest tests/test_bisect_e2e.py

# Run a specific test class
docker compose run --rm bot pytest tests/test_bisect_e2e.py::TestBisectCore

# Run a specific test
docker compose run --rm bot pytest tests/test_bisect_e2e.py::TestBisectCore::test_bisect_finds_culprit_commit

# Run tests matching a pattern
docker compose run --rm bot pytest -k "culprit"

# Run with verbose output
docker compose run --rm bot pytest -v

# Run with coverage
docker compose run --rm bot pytest --cov=app --cov-report=html
```

### Test Fixtures

The test suite provides helpful fixtures in `tests/conftest.py`:

- `git_repo_builder` - Create custom test repositories
- `simple_test_repo` - Pre-built repo with a known culprit commit
- `multi_file_test_repo` - More complex Python project scenario

---

## Database Management

### Running Migrations

Migrations run automatically on container startup. To run them manually:

```bash
docker compose run --rm bot alembic upgrade head
```

### Creating New Migrations

After modifying `app/models.py`:

```bash
docker compose run --rm bot alembic revision --autogenerate -m "Add new field"
```

### Resetting the Database

```bash
# Stop services and remove volumes
docker compose down -v

# Start fresh
docker compose up --build
```

### Accessing the Database Directly

```bash
# Connect to PostgreSQL
docker compose exec postgres psql -U bisect -d bisect

# Run a query
docker compose exec postgres psql -U bisect -d bisect -c "SELECT * FROM bisect_jobs;"
```

---

## Working with GitHub Webhooks

### Development Mode

In development mode (`DEV_MODE=true`), webhook signature verification is disabled, allowing you to test webhooks manually:

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issue_comment" \
  -H "X-Hub-Signature-256: sha256=test" \
  -d '{
    "action": "created",
    "comment": {
      "body": "/bisect abc123 def456 npm test",
      "user": {"login": "testuser"}
    },
    "issue": {"number": 1},
    "repository": {
      "name": "test-repo",
      "owner": {"login": "testowner"}
    },
    "installation": {"id": 12345}
  }'
```

### Using ngrok for Real Webhook Testing

To receive real GitHub webhooks locally, use the ngrok service:

1. Get an ngrok auth token from https://dashboard.ngrok.com
2. Add to your `.env`:
   ```bash
   NGROK_AUTHTOKEN=your-token-here
   ```
3. Uncomment the ngrok service in `docker-compose.override.yml`
4. Start the services:
   ```bash
   docker compose up -d
   ```
5. Visit http://localhost:4040 to see your public ngrok URL
6. Update your GitHub App's webhook URL to the ngrok URL + `/webhook`

### Creating a Test GitHub App

1. Go to [GitHub Developer Settings](https://github.com/settings/apps)
2. Click "New GitHub App"
3. Configure:
   - **Name**: `Bisect Bot Dev` (must be unique)
   - **Homepage URL**: `http://localhost:8000`
   - **Webhook URL**: Your ngrok URL + `/webhook`
   - **Webhook secret**: Generate with `openssl rand -hex 20`
4. Set permissions:
   - **Issues**: Read & write
   - **Contents**: Read-only
5. Subscribe to events:
   - **Issue comment**
6. Generate and download a private key, save as `secrets/private-key.pem`

---

## Code Structure

```
bisect/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, webhook handling, serves UI
│   ├── auth.py           # GitHub OAuth authentication routes
│   ├── api.py            # Dashboard API endpoints
│   ├── config.py         # Settings from environment
│   ├── models.py         # SQLAlchemy models
│   ├── database.py       # Database session management
│   ├── bisect_core.py    # Core bisect logic (git operations)
│   ├── bisect_runner.py  # Docker-based bisect execution
│   ├── local_runner.py   # Local filesystem bisect execution
│   ├── github_client.py  # GitHub API client
│   ├── security.py       # Input validation, logging
│   ├── encryption.py     # Field encryption utilities
│   └── static/
│       └── index.html    # Single-page application UI
├── tests/
│   ├── conftest.py       # Test fixtures
│   └── test_bisect_e2e.py # End-to-end tests
├── docker/
│   ├── Dockerfile.runner # Image for running bisect jobs
│   └── bisect_script.py  # Script that runs inside containers
├── alembic/
│   ├── env.py            # Migration environment
│   └── versions/         # Migration files
├── docker-compose.yml          # Production-like setup
├── docker-compose.override.yml # Development overrides
├── Dockerfile                  # Main application image
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Project metadata
└── .env.example                # Example environment file
```

### Key Components

- **`bisect_core.py`**: Pure Python bisect logic, no external dependencies
- **`local_runner.py`**: Runs bisect on the local filesystem (good for testing)
- **`bisect_runner.py`**: Runs bisect inside Docker containers (production)
- **`main.py`**: FastAPI application handling webhooks and job orchestration

---

## Common Tasks

### Opening a Shell in the Container

```bash
# Bash shell with your code
docker compose run --rm bot bash

# Python shell
docker compose run --rm bot python
```

### Adding a New Endpoint

1. Edit `app/main.py`:
   ```python
   @app.get("/my-endpoint")
   async def my_endpoint():
       return {"message": "Hello"}
   ```
2. The change is reflected automatically (source is mounted)

### Adding a New Model

1. Define the model in `app/models.py`:
   ```python
   class MyModel(Base):
       __tablename__ = "my_table"
       id = Column(Integer, primary_key=True)
       name = Column(String(255), nullable=False)
   ```

2. Create a migration:
   ```bash
   docker compose run --rm bot alembic revision --autogenerate -m "Add my_table"
   ```

3. Apply the migration:
   ```bash
   docker compose run --rm bot alembic upgrade head
   ```

### Adding a New Dependency

1. Add to `requirements.txt`
2. Rebuild the container:
   ```bash
   docker compose up --build
   ```

### Running a One-off Script

```bash
docker compose run --rm bot python -c "
from app.bisect_core import run_bisect

result = run_bisect(
    repo_dir='/tmp/myrepo',
    good_sha='abc123',
    bad_sha='def456',
    test_command='npm test',
)
print(f'Culprit: {result.culprit_sha}')
"
```

### Formatting Code

```bash
docker compose run --rm bot black app/ tests/
```

### Linting

```bash
docker compose run --rm bot ruff check app/ tests/
```

---

## Troubleshooting

### "Port 8000 already in use"

```bash
# Find and kill the process
lsof -i :8000
kill -9 <PID>

# Or use a different port
PORT=8001 docker compose up
```

### "Failed to connect to database"

Ensure PostgreSQL is running and healthy:

```bash
# Check service status
docker compose ps

# Check PostgreSQL logs
docker compose logs postgres

# Restart PostgreSQL
docker compose restart postgres
```

### Container Won't Start

```bash
# View detailed logs
docker compose logs bot

# Rebuild from scratch
docker compose down -v
docker compose build --no-cache
docker compose up
```

### "Docker socket not available"

The bot needs access to Docker to run bisect containers. Ensure the Docker socket is mounted correctly in `docker-compose.yml`.

### Tests Failing with Git Errors

Git should be configured inside the container. If you see errors, ensure the test fixtures are creating valid git repositories.

### Database Migration Errors

Reset the database:

```bash
docker compose down -v
docker compose up --build
```

### Viewing Application Logs

```bash
# All logs
docker compose logs

# Bot logs only
docker compose logs bot

# Follow logs in real-time
docker compose logs -f bot

# Last 100 lines
docker compose logs --tail 100 bot
```

---

## Environment Variables

See [Environment Configuration (.env)](#environment-configuration-env) for detailed setup instructions.

**Quick Reference:**

| Variable | Default | Description |
|----------|---------|-------------|
| `DEV_MODE` | `true` (via override) | Enables API docs, bypasses webhook signature checks |
| `DATABASE_URL` | `postgresql://bisect:changeme@postgres:5432/bisect` | PostgreSQL connection string (works with Docker Compose) |
| `GITHUB_APP_ID` | `12345` | Your GitHub App ID (only for real GitHub integration) |
| `GITHUB_APP_SLUG` | `bisect-bot` | App URL slug (e.g., for `github.com/apps/bisect-bot`) |
| `GITHUB_PRIVATE_KEY_PATH` | N/A | **Don't set** — place key file at `secrets/private-key.pem` |
| `GITHUB_WEBHOOK_SECRET` | `dev-secret-at-least-16-chars` | Webhook secret (validated only in production) |
| `GITHUB_CLIENT_ID` | None | GitHub OAuth App Client ID (for UI login) |
| `GITHUB_CLIENT_SECRET` | None | GitHub OAuth App Client Secret (for UI login) |
| `BASE_URL` | `http://localhost:8000` | Base URL for OAuth callbacks |
| `SESSION_SECRET` | `dev-session-secret...` | Secret for signing session cookies |
| `DOCKER_RUNNER_IMAGE` | `bisect-runner:latest` | Docker image for bisect jobs |
| `BISECT_TIMEOUT_SECONDS` | `1800` | Max time for a bisect operation (seconds) |
| `MAX_CONCURRENT_JOBS` | `4` | Max parallel bisect jobs |
| `ENCRYPTION_KEY` | None | Optional key for encrypting sensitive data at rest |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `ALLOWED_HOSTS` | `*` | Comma-separated allowed hostnames |

> **Note:** For local development without UI login, you don't need to set any of these. All defaults are designed to work with `docker compose up`. To enable UI login, you'll need to create a GitHub OAuth App and set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.

---

## Contributing

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make your changes
3. Run tests: `docker compose run --rm bot pytest`
4. Submit a pull request
