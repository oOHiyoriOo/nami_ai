# External Dependencies

This directory contains external dependencies as git submodules.

## copilot-api

The `copilot-api` is a reverse-engineered proxy for the GitHub Copilot API that exposes it as an OpenAI and Anthropic compatible service.

**Repository:** https://github.com/ericc-ch/copilot-api

**Purpose:** Allows using GitHub Copilot subscription to access GPT-4 and other models through the Nami AI proxy.

### Quick Start

1. Initialize the submodule (if not already done):
   ```bash
   git submodule update --init --recursive
   ```

2. Start the copilot-api server:
   ```bash
   # Using the convenience script
   ../scripts/start_copilot_api.sh

   # Or manually with npx
   npx copilot-api@latest start
   ```

3. Configure Nami AI to use Copilot:
   - Edit `config.yml`
   - Set `ai_provider: copilot`
   - Configure the copilot provider settings

### Documentation

See the main documentation at: [docs/reference/providers.md](../docs/reference/providers.md#using-github-copilot)

For copilot-api specific documentation, see: [copilot-api/README.md](copilot-api/README.md)
