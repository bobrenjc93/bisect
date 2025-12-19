#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Bisect Bot Test Runner ===${NC}"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv is not installed.${NC}"
    echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Navigate to script directory
cd "$(dirname "$0")"

# Sync dependencies (installs if needed, including dev deps)
echo -e "${YELLOW}Syncing dependencies...${NC}"
uv sync --extra dev

# Run tests
echo -e "${YELLOW}Running tests...${NC}"
uv run pytest "$@"

echo -e "${GREEN}Done!${NC}"

