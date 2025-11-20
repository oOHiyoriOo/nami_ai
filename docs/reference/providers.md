# AI Providers Guide

The Personality Proxy supports multiple AI backends through a pluggable provider system. Switch between Ollama, OpenAI, Anthropic, or create your own custom provider.

## Available Providers

| Provider | Status | Requires | Use Case |
|----------|--------|----------|----------|
| **Ollama** | ✅ Built-in | Local Ollama | Privacy, offline, free |
| **OpenAI** | ✅ Built-in | API key | GPT-4, hosted |
| **Copilot** | ✅ Built-in | GitHub Copilot, copilot-api | GPT-4 via Copilot subscription |
| **Anthropic** | 📝 Template | API key | Claude, hosted |
| **Custom** | 📝 DIY | Your code | Any API |

## Using Ollama (Local)

### Setup

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama2
ollama pull mistral
ollama pull deepseek-r1:70b

# Start Ollama (usually auto-starts)
ollama serve
```

### Configuration

```yaml
ai_provider: ollama

providers:
  ollama:
    url: http://localhost:11434
    model: llama2
    system_prompt: nami
    max_tool_calls: 3
```

### Benefits

- ✅ **Free** - No API costs
- ✅ **Private** - Data stays local
- ✅ **Offline** - No internet needed
- ✅ **Fast** - No network latency
- ✅ **Tool support** - Full function calling

### Limitations

- ❌ Requires GPU for good performance
- ❌ Larger models need significant VRAM
- ❌ Model quality varies

## Using OpenAI

### Setup

```bash
# Install OpenAI library
pip install openai

# Get API key from https://platform.openai.com/api-keys
```

### Configuration

```yaml
ai_provider: openai

providers:
  openai:
    api_key: sk-your-api-key-here
    model: gpt-4
    system_prompt: nami
    organization: org-your-org-id  # Optional
```

### Available Models

- `gpt-4` - Most capable
- `gpt-4-turbo` - Faster, cheaper
- `gpt-4o` - Multimodal
- `gpt-3.5-turbo` - Fast, cheap

### Benefits

- ✅ **High quality** - GPT-4 is very capable
- ✅ **No setup** - Just API key
- ✅ **Reliable** - Hosted service
- ✅ **Tool support** - Full function calling

### Limitations

- ❌ **Costs money** - Pay per token
- ❌ **Requires internet** - Cloud API
- ❌ **Privacy** - Data sent to OpenAI
- ❌ **Rate limits** - API throttling

### Pricing

See [OpenAI Pricing](https://openai.com/pricing) for current rates.

## Using GitHub Copilot

Access GPT-4 and other OpenAI models using your existing GitHub Copilot subscription via the copilot-api proxy.

### Prerequisites

1. **GitHub Copilot Subscription** - You need an active GitHub Copilot subscription (individual, business, or enterprise)
2. **Node.js or Bun** - To run the copilot-api proxy server

### Setup

#### Step 1: Initialize the copilot-api submodule

The copilot-api proxy is included as a git submodule:

```bash
# Initialize and update the submodule
git submodule update --init --recursive
```

#### Step 2: Install copilot-api dependencies

**Option A: Using Bun (recommended)**

```bash
# Install Bun if not already installed
curl -fsSL https://bun.sh/install | bash

# Navigate to the copilot-api directory
cd external/copilot-api

# Install dependencies
bun install
```

**Option B: Using npx**

No installation needed - npx will download and run copilot-api automatically.

#### Step 3: Start the copilot-api proxy server

**Option A: Using the startup script (recommended)**

```bash
# From the project root
./scripts/start_copilot_api.sh

# Or with options
./scripts/start_copilot_api.sh --port 4141 --verbose
```

**Option B: Manually using Bun**

```bash
cd external/copilot-api
bun run start
```

**Option C: Using npx**

```bash
npx copilot-api@latest start
```

The first time you run the server, it will guide you through GitHub authentication:
1. Open the provided URL in your browser
2. Enter the device code shown
3. Authorize the application

#### Step 4: Install Python dependencies

```bash
# The Copilot provider uses the OpenAI library
pip install openai
```

### Configuration

Add to your `config.yml`:

```yaml
ai_provider: copilot

providers:
  copilot:
    url: http://localhost:4141  # copilot-api server URL
    model: gpt-4.1              # or gpt-4o, gpt-4-turbo, gpt-3.5-turbo
    api_key: dummy              # copilot-api uses dummy auth
    system_prompt: nami
```

### Available Models

The copilot-api proxy provides access to these models through your Copilot subscription:

- `gpt-4.1` - Latest GPT-4 model (recommended)
- `gpt-4o` - GPT-4 optimized
- `gpt-4-turbo` - GPT-4 Turbo
- `gpt-3.5-turbo` - GPT-3.5 Turbo (faster, lower quality)

### Benefits

- ✅ **Cost effective** - Use existing Copilot subscription
- ✅ **High quality** - Access to GPT-4 models
- ✅ **No additional API costs** - Included with Copilot
- ✅ **Tool support** - Full function calling
- ✅ **Streaming support** - Real-time responses

### Limitations

- ❌ **Requires active subscription** - GitHub Copilot subscription needed
- ❌ **Requires internet** - Proxy connects to GitHub
- ❌ **Requires proxy server** - Must run copilot-api
- ❌ **Rate limits** - Subject to GitHub Copilot usage limits

### Usage Tips

To avoid hitting GitHub Copilot's rate limits, you can:

1. **Manual approval** - Enable manual approval for each request:
   ```bash
   ./scripts/start_copilot_api.sh --manual
   ```

2. **Rate limiting** - Set a minimum time between requests:
   ```bash
   ./scripts/start_copilot_api.sh --rate-limit 30
   ```

3. **Wait on limit** - Wait instead of erroring when rate limited:
   ```bash
   ./scripts/start_copilot_api.sh --rate-limit 30 --wait
   ```

### Troubleshooting

**Copilot-api not running:**
```bash
# Check if the server is running
curl http://localhost:4141/v1/models

# Start the server
./scripts/start_copilot_api.sh
```

**Authentication failed:**
- Re-run the authentication flow: `npx copilot-api@latest auth`
- Ensure your GitHub Copilot subscription is active
- Check you're using the correct account type (individual/business/enterprise)

**Connection refused:**
- Verify the proxy server is running
- Check the URL in your config matches the server (default: http://localhost:4141)
- Ensure no firewall is blocking port 4141

**Models not available:**
- Ensure you're using a supported model name (gpt-4.1, gpt-4o, etc.)
- Some models may not be available with all Copilot subscription types

### Advanced Configuration

**Using with Business/Enterprise Copilot:**

```bash
# For business accounts
./scripts/start_copilot_api.sh --account-type business

# For enterprise accounts
./scripts/start_copilot_api.sh --account-type enterprise
```

**Using Docker:**

```bash
cd external/copilot-api

# Build the Docker image
docker build -t copilot-api .

# Run with persistent storage
mkdir -p ./copilot-data
docker run -p 4141:4141 -v $(pwd)/copilot-data:/root/.local/share/copilot-api copilot-api
```

**Environment Variables:**

You can set these environment variables to configure the startup script:

```bash
export COPILOT_PORT=4141
export COPILOT_VERBOSE=true
export COPILOT_ACCOUNT_TYPE=individual

./scripts/start_copilot_api.sh
```

### Links

- [copilot-api GitHub Repository](https://github.com/ericc-ch/copilot-api)
- [copilot-api Documentation](https://github.com/ericc-ch/copilot-api#readme)

## Using Anthropic Claude (Template)

### Setup

```bash
# Install Anthropic library
pip install anthropic

# Get API key from https://console.anthropic.com/
```

### Configuration

```yaml
ai_provider: anthropic

providers:
  anthropic:
    api_key: sk-ant-your-api-key
    model: claude-3-opus-20240229
    system_prompt: nami
```

**Note:** The Anthropic provider is a template. You'll need to implement it following the pattern in `lib/ai_providers/openai_provider.py`.

## Creating a Custom Provider

### 1. Create Provider Class

Create `lib/ai_providers/my_provider.py`:

```python
"""
My Custom AI Provider
"""
import logging
from typing import List, Dict, Any, Optional, AsyncIterator
from .base_provider import AIProvider, Message, ChatResponse


class MyProvider(AIProvider):
    """Custom AI provider implementation."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.default_model = config.get('model', 'default-model')
        self.base_url = config.get('url', 'https://api.example.com')

        # Initialize your client here
        # self.client = YourClient(api_key=self.api_key)

        logging.info(f"Initialized MyProvider with model: {self.default_model}")

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion."""
        model = kwargs.get('model', self.default_model)

        # Convert messages to your API format
        api_messages = [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]

        # Call your API
        # response = await self.client.create_completion(
        #     model=model,
        #     messages=api_messages,
        #     tools=tools
        # )

        # For this example, return a mock response
        return ChatResponse(
            content="This is a mock response from MyProvider",
            tool_calls=None,
            model=model,
            finish_reason="stop"
        )

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming chat completion."""
        # Implement streaming
        yield "This "
        yield "is "
        yield "streaming "
        yield "response"

    def list_models(self) -> List[str]:
        """List available models."""
        return ["model-1", "model-2", "model-3"]

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "my_provider"
```

### 2. Register Provider

Edit `lib/ai_providers/__init__.py`:

```python
from .my_provider import MyProvider

# Register the provider
ProviderRegistry.register_provider("my_provider", MyProvider)
```

### 3. Configure

Add to `config.yml`:

```yaml
ai_provider: my_provider

providers:
  my_provider:
    api_key: your-api-key
    url: https://api.example.com
    model: model-1
    system_prompt: nami
```

### 4. Test

```bash
python api_server.py
```

That's it! Your custom provider is now integrated.

## Provider Comparison

### Cost Comparison (Approximate)

| Provider | Cost | Setup Time | Quality |
|----------|------|------------|---------|
| Ollama | Free | 10 min | Good |
| OpenAI GPT-4 | $0.03/1K tokens | 2 min | Excellent |
| OpenAI GPT-3.5 | $0.001/1K tokens | 2 min | Good |
| Copilot | Included w/ subscription | 5-10 min | Excellent |
| Anthropic Claude | $0.015/1K tokens | 2 min | Excellent |
| Custom | Varies | 1-4 hours | Varies |

### Performance Comparison

| Provider | Latency | Throughput | Reliability |
|----------|---------|------------|-------------|
| Ollama (local) | Low (50-500ms) | High | Very High |
| OpenAI | Medium (500-2000ms) | Medium | High |
| Copilot | Medium (500-2000ms) | Medium | High |
| Anthropic | Medium (500-2000ms) | Medium | High |
| Custom | Varies | Varies | Varies |

### Feature Comparison

| Feature | Ollama | OpenAI | Copilot | Anthropic | Custom |
|---------|--------|--------|---------|-----------|--------|
| **Tool Calling** | ✅ | ✅ | ✅ | ✅ | Depends |
| **Streaming** | ✅ | ✅ | ✅ | ✅ | Depends |
| **Local** | ✅ | ❌ | ❌ | ❌ | Depends |
| **Free** | ✅ | ❌ | w/ subscription | ❌ | Depends |
| **Privacy** | ✅ | ❌ | ❌ | ❌ | Depends |
| **Quality** | Good | Excellent | Excellent | Excellent | Varies |

## Switching Providers

### At Runtime (Config Change)

1. Edit `config.yml`:
```yaml
ai_provider: openai  # Change from ollama to openai
```

2. Restart server:
```bash
python api_server.py
```

### Programmatically

```python
from lib.ai_providers import ProviderRegistry

# Get a provider
provider = ProviderRegistry.get_provider(
    "ollama",
    {"url": "http://localhost:11434", "model": "llama2"}
)

# Use it
response = await provider.chat(messages=[...])
```

## Best Practices

### For Development

Use **Ollama** for:
- Fast iteration
- No API costs
- Testing locally

### For Production

Use **OpenAI/Anthropic** for:
- Best quality responses
- Reliable uptime
- No infrastructure management

Use **Ollama** for:
- Privacy requirements
- High volume (cost savings)
- Offline capability

### Hybrid Approach

Run both:
- **Ollama** for development/testing
- **OpenAI** for production

```yaml
providers:
  ollama:
    url: http://localhost:11434
    model: llama2
  openai:
    api_key: sk-...
    model: gpt-4

# Switch with environment variable
ai_provider: ${AI_PROVIDER:-ollama}
```

## Troubleshooting

### Ollama Issues

**Model not found:**
```bash
ollama pull llama2
```

**Ollama not running:**
```bash
ollama serve
```

**GPU not detected:**
- Check CUDA/ROCm installation
- Ollama will fall back to CPU (slower)

### OpenAI Issues

**Authentication failed:**
- Verify API key is correct
- Check key hasn't expired
- Ensure billing is set up

**Rate limited:**
- Reduce request frequency
- Upgrade plan
- Add exponential backoff

### General Issues

**Provider not found:**
```python
# Check registered providers
from lib.ai_providers import ProviderRegistry
print(ProviderRegistry.list_providers())
```

**Import errors:**
```bash
# Install missing dependencies
pip install openai  # For OpenAI
pip install anthropic  # For Anthropic
```

## Advanced Usage

### Multiple Providers Simultaneously

You can use different providers for different purposes:

```python
# Use Ollama for cheap tasks
cheap_provider = ProviderRegistry.get_provider("ollama", {...})

# Use GPT-4 for important tasks
smart_provider = ProviderRegistry.get_provider("openai", {...})

# Route based on task
if task.complexity > 0.8:
    response = await smart_provider.chat(messages)
else:
    response = await cheap_provider.chat(messages)
```

### Fallback Chain

Implement provider fallbacks:

```python
providers = ['ollama', 'openai', 'anthropic']

for provider_name in providers:
    try:
        provider = ProviderRegistry.get_provider(provider_name, config)
        response = await provider.chat(messages)
        break
    except Exception as e:
        logging.warning(f"{provider_name} failed: {e}")
        continue
```

## See Also

- [API Reference](api.md) - Complete API documentation
- [Quick Start](../guides/quickstart.md) - Get started quickly
- [Memory System](../memory/overview.md) - Memory configuration

---

Need help? [Open an issue](https://github.com/oOHiyoriOo/nami_ai/issues) on GitHub.
