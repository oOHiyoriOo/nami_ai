#!/bin/bash
# Script to start the GitHub Copilot API proxy server
# This script starts the copilot-api server which provides an OpenAI-compatible
# interface to GitHub Copilot

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
PORT="${COPILOT_PORT:-4141}"
VERBOSE="${COPILOT_VERBOSE:-false}"
ACCOUNT_TYPE="${COPILOT_ACCOUNT_TYPE:-individual}"

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COPILOT_API_DIR="$PROJECT_ROOT/external/copilot-api"

echo -e "${GREEN}GitHub Copilot API Proxy Startup Script${NC}"
echo "=========================================="

# Check if copilot-api directory exists
if [ ! -d "$COPILOT_API_DIR" ]; then
    echo -e "${RED}Error: copilot-api directory not found at $COPILOT_API_DIR${NC}"
    echo "Please ensure the copilot-api submodule is initialized:"
    echo "  git submodule update --init --recursive"
    exit 1
fi

# Check if bun or npx is available
if command -v bun &> /dev/null; then
    echo -e "${GREEN}✓ Found Bun${NC}"
    RUNNER="bun"
    RUN_CMD="bun run start"
elif command -v npx &> /dev/null; then
    echo -e "${GREEN}✓ Found npx${NC}"
    RUNNER="npx"
    RUN_CMD="npx copilot-api@latest start"
else
    echo -e "${RED}Error: Neither bun nor npx is installed${NC}"
    echo "Please install one of the following:"
    echo "  - Bun: https://bun.sh"
    echo "  - Node.js (includes npx): https://nodejs.org"
    exit 1
fi

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -a|--account-type)
            ACCOUNT_TYPE="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -p, --port <port>          Port to run on (default: 4141)"
            echo "  -v, --verbose              Enable verbose logging"
            echo "  -a, --account-type <type>  Account type: individual, business, enterprise (default: individual)"
            echo "  -h, --help                 Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  COPILOT_PORT               Default port (default: 4141)"
            echo "  COPILOT_VERBOSE            Enable verbose logging (default: false)"
            echo "  COPILOT_ACCOUNT_TYPE       Account type (default: individual)"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Build command arguments
CMD_ARGS="--port $PORT --account-type $ACCOUNT_TYPE"
if [ "$VERBOSE" = true ]; then
    CMD_ARGS="$CMD_ARGS --verbose"
fi

echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  Port: $PORT"
echo "  Account Type: $ACCOUNT_TYPE"
echo "  Verbose: $VERBOSE"
echo "  Runner: $RUNNER"
echo ""

# Navigate to copilot-api directory if using bun run
if [ "$RUNNER" = "bun" ]; then
    echo -e "${GREEN}Starting copilot-api server from $COPILOT_API_DIR...${NC}"
    cd "$COPILOT_API_DIR"

    # Install dependencies if needed
    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}Installing dependencies...${NC}"
        bun install
    fi

    # Start the server
    exec bun run start $CMD_ARGS
else
    # Using npx
    echo -e "${GREEN}Starting copilot-api server via npx...${NC}"
    exec npx copilot-api@latest start $CMD_ARGS
fi
