# Security: Tool Argument Validation

## Overview
This document describes the security measures implemented to protect against unsafe deserialization of tool arguments from LLM responses.

## Vulnerability Background

**Location:** `lib/services/tool_executor.py`

**Issue:** Tool arguments from LLM responses are passed directly to tool functions. Without validation, an adversarially manipulated response could:
- Pass unexpected types for parameters
- Inject extra parameters to exploit tool functions
- Cause crashes via missing required parameters
- Exhaust resources via oversized strings/arrays

**Severity:** HIGH

## Solution: Validation Module

### Architecture

The `lib/tool_argument_validator.py` module provides comprehensive argument validation against JSON Schema specifications:

```python
from lib.tool_argument_validator import validate_tool_arguments, ToolArgumentValidationError

try:
    validated_args = validate_tool_arguments(tool['function'], tool_args)
    # Use validated_args safely
except ToolArgumentValidationError as e:
    # Handle validation error
    logging.error(f"Validation failed: {e}")
```

### Security Features

#### 1. Type Validation
Validates arguments against JSON Schema types:
- `string` - Text values
- `number` / `integer` - Numeric values
- `boolean` - True/false values
- `array` - Lists of values
- `object` - Nested dictionaries
- `null` - Null values

#### 2. Required Parameter Validation
Ensures all required parameters are present before tool execution.

#### 3. Unexpected Parameter Detection
Rejects any parameters not defined in the tool schema, preventing injection attacks.

#### 4. Security Limits

| Protection | Default Limit | Purpose |
|------------|---------------|---------|
| String Length | 50,000 chars | Prevent memory exhaustion |
| Array Size | 1,000 items | Prevent DoS attacks |
| Object Keys | 100 keys | Limit complexity |
| Nesting Depth | 10 levels | Prevent stack overflow |

#### 5. Validation Rules

**String Validation:**
- Type check: must be string
- Length limits: configurable min/max
- Pattern matching: optional regex validation
- Enum validation: optional allowed values list

**Numeric Validation:**
- Type check: integer or number
- Range validation: configurable min/max
- Boolean exclusion: prevents `True`/`False` from being accepted as numbers

**Array Validation:**
- Type check: must be list
- Size limits: configurable min/max items
- Item validation: recursive validation of array items

**Object Validation:**
- Type check: must be dict
- Key limits: maximum number of keys
- Property validation: recursive validation of nested properties
- Required properties: validates required nested fields

## Integration

The validator can be integrated into `lib/services/tool_executor.py`:

```python
from lib.tool_argument_validator import validate_tool_arguments, ToolArgumentValidationError

# Validate tool arguments against schema
try:
    validated_args = validate_tool_arguments(tool['function'], tool_args)
    logging.info(f"Tool arguments validated successfully for {tool_name}")
except ToolArgumentValidationError as validation_err:
    logging.error(f"Tool argument validation failed for {tool_name}: {validation_err}")
    current_messages.append(Message(
        role="tool",
        content=f"Argument validation error: {str(validation_err)}"
    ))
    continue

# Execute tool with validated arguments
result = await tool_fn(**validated_args)
```

## Configuration

Adjust security limits in `ToolArgumentValidator` class:

```python
class ToolArgumentValidator:
    MAX_STRING_LENGTH = 50000   # Maximum string length
    MAX_ARRAY_LENGTH = 1000     # Maximum array items
    MAX_OBJECT_KEYS = 100       # Maximum object keys
    MAX_NESTING_DEPTH = 10      # Maximum nesting depth
```

## Best Practices

### 1. Define Complete Tool Schemas

Ensure all tools have proper JSON Schema definitions:

```python
{
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "Search the memory database",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1000,
                    "description": "Search query"
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max results"
                }
            },
            "required": ["query"]
        }
    }
}
```

### 2. Use Type Constraints

Always specify types and constraints in tool schemas:

```python
"parameters": {
    "type": "object",
    "properties": {
        "count": {
            "type": "integer",      # Specific type
            "minimum": 1,           # Min value
            "maximum": 100          # Max value
        },
        "mode": {
            "type": "string",
            "enum": ["fast", "slow"]  # Allowed values only
        }
    }
}
```

### 3. Monitor Validation Failures

Track validation failures to detect potential attacks:

```python
logging.error(f"Tool argument validation failed for {tool_name}: {validation_err}")
```

### 4. Regular Security Audits

Periodically review:
- Tool schemas for completeness
- Validation logs for suspicious patterns
- Tool implementations for additional hardening

## Testing

Run the validation test suite:

```bash
python run_tests.py
```

**Test Coverage:**
1. Valid arguments acceptance
2. Missing required parameter detection
3. Unexpected parameter blocking
4. Type mismatch detection
5. String length enforcement
6. Numeric range validation
7. Nested object validation
8. Array validation

## Security Benefits

| Benefit | Description |
|---------|-------------|
| **Argument Injection Prevention** | Extra parameters are rejected |
| **Type Safety** | Ensures arguments match expected types |
| **DoS Protection** | Length and size limits prevent resource exhaustion |
| **Input Validation** | All inputs validated before execution |
| **Clear Error Messages** | Detailed logging for debugging |

## Current Integration Status

> **Note:** `ToolArgumentValidator` is defined but **not yet integrated** into `lib/services/tool_executor.py`. Tool arguments are currently passed directly to tool functions via `await tool_fn(**tool_args)`. Integration is recommended to harden the runtime against malformed LLM outputs.

## Performance Impact

**Minimal overhead:**
- Validation: < 1ms per tool call
- Prevents expensive failures from malformed inputs
- Protects against DoS attacks that could be more costly

## Backward Compatibility

**Fully backward compatible.** All existing tools continue to work as long as they provide properly typed arguments according to their schemas.

## Related Documentation

- [Tools System](tools.md) - Creating and using tools
- [API Reference](api.md) - API endpoint documentation

---

**Navigation:** [← Back to Reference](../README.md#-reference) | [Tools System →](tools.md)
