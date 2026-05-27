# Memory Extraction Model Configuration

## Problem

Previously, memory extraction used the same model as the main chat provider, which could be expensive or slow for background memory processing tasks.

## Solution

Added dedicated configuration for memory extraction model, allowing you to use a smaller/cheaper model for memory extraction while using a more powerful model for chat.

## Configuration

```yaml
memory:
  # Embedding settings (unchanged)
  embedding_model: all-MiniLM-L6-v2
  embedding_dimension: 384
  similarity_threshold: 0.65
  
  # NEW: Memory extraction AI settings
  extraction_provider: ollama        # Which provider to use
  extraction_model: llama3.2         # Which model to use (use smaller/cheaper models)
  extraction_batch_size: 10
  extraction_batch_interval: 5.0
```

## Model Selection Logic

The `MemoryExtractor` now follows this priority:

1. **Explicit model** passed to `MemoryExtractor()` constructor
2. **`memory.extraction_model`** from config (NEW)
3. **Provider's default model** from `providers.<provider>.default_model`
4. **Hardcoded fallback**: `llama3.2`

## Example Use Cases

### Use Case 1: Cost Optimization (OpenAI)

```yaml
# Main chat uses GPT-4 (expensive)
default_provider: openai
default_model: gpt-4

providers:
  openai:
    api_key: "${OPENAI_API_KEY}"
    default_model: gpt-4

# Memory extraction uses GPT-3.5 (cheaper)
memory:
  extraction_provider: openai
  extraction_model: gpt-3.5-turbo
```

**Savings**: ~10x cost reduction for memory extraction

### Use Case 2: Speed Optimization (Local + Cloud)

```yaml
# Main chat uses Claude Opus (high quality)
default_provider: anthropic
default_model: claude-opus-4

providers:
  anthropic:
    api_key: "${ANTHROPIC_API_KEY}"
  
  ollama:
    url: http://localhost:11434

# Memory extraction uses local Ollama (fast, free)
memory:
  extraction_provider: ollama
  extraction_model: llama3.2
```

**Benefits**: No API costs for memory extraction, faster background processing

### Use Case 3: Model Specialization

```yaml
# Main chat uses general-purpose model
default_provider: ollama
default_model: qwen3:32b

# Memory extraction uses smaller, instruction-tuned model
memory:
  extraction_provider: ollama
  extraction_model: llama3.2:latest  # Better at structured output
```

**Benefits**: Optimized for JSON generation and fact extraction

## Migration

### Before (old config location)

```yaml
# Old location (no longer used)
memory_db:
  provider: ollama
  extraction_model: llama3.2
  batch_size: 10
```

### After (new config location)

```yaml
# New location
memory:
  extraction_provider: ollama
  extraction_model: llama3.2
  extraction_batch_size: 10
  extraction_batch_interval: 5.0
```

## Code Changes

### `lib/services/app_initializer.py`

```python
# Changed from:
extractor_config = self.config.data.get('memory_db', {})

# To:
memory_config = self.config.data.get('memory', {})
```

### `lib/services/memory_extractor.py`

```python
# Updated model selection logic:
model = self.model_name
if not model:
    memory_config = cfg.data.get('memory', {})
    model = memory_config.get('extraction_model')
if not model:
    model = provider_config.get('default_model')
```

## Testing

```python
# Verify memory extraction uses correct model
import logging
logging.basicConfig(level=logging.DEBUG)

# Look for log line:
# "Memory extraction using ollama/llama3.2"
```

## Related Files

- `config.yml.example` - Example configuration
- `lib/services/app_initializer.py` - Initializes MemoryExtractor with config
- `lib/services/memory_extractor.py` - Uses configured model
- `.github/copilot-instructions.md` - Documentation updated

## Benefits

1. **Cost Optimization** - Use cheaper models for background tasks
2. **Speed** - Use faster models for memory extraction
3. **Flexibility** - Mix and match providers (local + cloud)
4. **Resource Management** - Don't waste GPU/API quota on simple tasks
5. **Model Specialization** - Use models optimized for structured output

## Recommendations

For memory extraction, prefer:
- **Small models** (3B-8B parameters)
- **Good at JSON** (instruction-tuned models)
- **Fast inference** (local models if possible)
- **Low cost** (if using API providers)

Good choices:
- `llama3.2` (3B, fast, good JSON)
- `phi-3` (3.8B, excellent instruction following)
- `gemma-2-2b` (2B, very fast)
- `gpt-3.5-turbo` (OpenAI, cheap, reliable)
- `claude-haiku` (Anthropic, fast, cheap)
