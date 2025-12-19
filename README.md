# GitHub Bisect Bot

A standalone GitHub App that performs automated git bisection when triggered by issue comments. When a test starts failing, simply comment on an issue with the bisect command, and the bot will find the exact commit that introduced the failure.

## Features

- **Automated git bisect**: Finds the first bad commit between two known commits
- **Docker isolation**: Runs tests in isolated containers with resource limits
- **GitHub integration**: Posts progress and results directly to issues
- **Configurable timeouts**: Prevent runaway tests from consuming resources

## Architecture

```
User comments "/bisect good_sha bad_sha test_cmd"
        │
        ▼
GitHub sends webhook to Bot Server
        │
        ▼
Bot posts "Starting bisect..." comment
        │
        ▼
Bot spawns Docker container
        │
        ▼
Container clones repo and runs git bisect
        │
        ▼
Bot posts results to issue
```

## Prerequisites

- Docker and Docker Compose
- A GitHub App with:
  - **Permissions**: Issues (read/write), Contents (read)
  - **Webhook events**: Issue comments
  - A generated private key

## Setup

### 1. Create a GitHub App

1. Go to GitHub Settings → Developer settings → GitHub Apps → New GitHub App
2. Set the following:
   - **Name**: Your bot name (e.g., "Bisect Bot")
   - **Homepage URL**: Your server URL
   - **Webhook URL**: `https://your-server.com/webhook`
   - **Webhook secret**: Generate a strong secret
3. Set permissions:
   - **Issues**: Read & write
   - **Contents**: Read-only
4. Subscribe to events:
   - **Issue comment**
5. Generate and download a private key

### 2. Configure the Bot

Create a `.env` file in the project root:

```bash
# GitHub App Configuration
GITHUB_APP_ID=your_app_id
GITHUB_WEBHOOK_SECRET=your_webhook_secret

# Optional: for local development with ngrok
NGROK_AUTHTOKEN=your_ngrok_token
```

Create a `secrets` directory and add your private key:

```bash
mkdir secrets
cp /path/to/your-private-key.pem secrets/private-key.pem
```

### 3. Build and Run

```bash
# Build and start the bot
docker-compose up -d

# View logs
docker-compose logs -f bot
```

### 4. Install the App

1. Go to your GitHub App settings
2. Click "Install App"
3. Select the repositories where you want to use the bot

## Usage

Comment on any issue in a repository where the bot is installed:

```
/bisect <good_sha> <bad_sha> <test_command>
```

### Examples

**Python project:**
```
/bisect abc123 def456 pytest tests/test_feature.py::test_specific_case
```

**Node.js project:**
```
/bisect v1.0.0 main npm test -- --grep "should handle edge case"
```

**Custom script:**
```
/bisect abc123 def456 ./scripts/run_tests.sh
```

### Parameters

| Parameter | Description |
|-----------|-------------|
| `good_sha` | A commit SHA where the test passes |
| `bad_sha` | A commit SHA where the test fails |
| `test_command` | The command to run (exit 0 = pass, non-zero = fail) |

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_APP_ID` | Your GitHub App ID | (required) |
| `GITHUB_PRIVATE_KEY_PATH` | Path to private key file | (required) |
| `GITHUB_WEBHOOK_SECRET` | Webhook secret for signature verification | (required) |
| `DOCKER_RUNNER_IMAGE` | Docker image for running bisects | `bisect-runner:latest` |
| `BISECT_TIMEOUT_SECONDS` | Maximum time for a bisect operation | `1800` (30 min) |
| `HOST` | Server host | `0.0.0.0` |
| `PORT` | Server port | `8000` |

## Development

### Local Development with ngrok

For local testing, you can use ngrok to expose your local server:

1. Uncomment the ngrok service in `docker-compose.yml`
2. Set `NGROK_AUTHTOKEN` in your `.env` file
3. Run `docker-compose up -d`
4. Get your public URL from the ngrok dashboard at `http://localhost:4040`
5. Update your GitHub App's webhook URL

### Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest
```

### Building the Runner Image Manually

```bash
cd docker
docker build -t bisect-runner:latest -f Dockerfile.runner .
```

## Security Considerations

- **Webhook signatures**: All webhooks are verified using HMAC-SHA256
- **Container isolation**: Tests run in isolated Docker containers
- **Resource limits**: Containers have memory and CPU limits
- **Timeouts**: Long-running operations are terminated after the configured timeout
- **Private repos**: The bot uses installation tokens for authenticated access

## Troubleshooting

### Bot not responding to commands

1. Check that the webhook URL is correct
2. Verify the webhook secret matches
3. Check the bot logs: `docker-compose logs bot`

### Bisect timing out

Increase `BISECT_TIMEOUT_SECONDS` in your environment configuration.

### Docker permission issues

Ensure the Docker socket is properly mounted and accessible.

## License

MIT




