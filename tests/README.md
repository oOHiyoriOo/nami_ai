# Tests

This directory contains automated tests for the Nami AI project.

## Running Tests

### Run All Tests

From the project root directory:

```bash
python run_tests.py
```

This will automatically discover and run all test files in the `tests/` directory.

### Run Individual Tests

You can also run individual test files:

```bash
python tests/test_tool_validator.py
python tests/test_config.py
python tests/test_system_prompt.py
python tests/test_mcp_tool_builder.py
```

## Test Files

### `test_tool_validator.py`
Tests the tool argument validation system that prevents unsafe deserialization attacks.

**Test Coverage:**
- Valid argument validation
- Missing required parameter detection
- Unexpected parameter rejection
- Type mismatch detection
- String length enforcement
- Numeric range validation
- Nested object validation
- Array validation

**Dependencies:** None (uses only standard library)

### `test_config.py`
Tests the configuration file handling system.

**Test Coverage:**
- Loading valid YAML configuration
- Saving configuration to file
- Accessing nested configuration values

**Dependencies:** `PyYAML`

### `test_system_prompt.py`
Tests the system prompt parser that handles dynamic placeholders.

**Test Coverage:**
- Loading prompt files
- Time placeholder replacement ({{TIME}})
- Date placeholder replacement ({{DATE}})
- Multiple placeholders
- Prompts without placeholders

**Dependencies:** `pytz`

### `test_memory_extractor.py`
Tests the `MemoryExtractor._parse_response` method that parses raw AI responses into structured memory objects.

**Test Coverage:**
- Valid JSON list of memory objects → returns valid ExtractedMemory
- Think blocks (`<think>...</think>`) stripped before parsing
- Code fences (```json ... ```) stripped
- Both think blocks and code fences stripped together
- Non-list JSON (dict) → returns empty list
- Missing memory_type → excluded by is_valid()
- Mix of valid and invalid items → only valid ones returned
- Empty response → raises JSONDecodeError

**Dependencies:** None (uses only standard library + project code)

### `test_mcp_tool_builder.py`
Tests the MCP (Model Context Protocol) tool builder.

**Test Coverage:**
- Building tools from valid configuration
- Handling empty configurations
- Building multiple tools
- Tools with additional parameters
- Skipping tools with missing essential fields

**Dependencies:** `aiohttp`

## Dependency Handling

Tests automatically skip if required dependencies are not installed. To run all tests, install dependencies:

```bash
pip install -r requirements.txt
```

If a test's dependencies are missing, it will output:

```
[SKIP] Required dependencies not available: No module named 'xyz'
Install dependencies with: pip install -r requirements.txt
```

Skipped tests are treated as passing by the test runner.

## Adding New Tests

To add a new test file:

1. Create a file named `test_<feature>.py` in the `tests/` directory
2. Add the path setup at the top:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).parent.parent))
   ```
3. Add dependency checking if needed:
   ```python
   try:
       from lib.your_module import YourClass
       DEPENDENCIES_AVAILABLE = True
   except ImportError as e:
       DEPENDENCIES_AVAILABLE = False
       MISSING_DEPENDENCY = str(e)
   ```
4. Implement your test functions
5. Add a main block that runs all tests:
   ```python
   if __name__ == "__main__":
       if not DEPENDENCIES_AVAILABLE:
           print(f"[SKIP] Required dependencies not available: {MISSING_DEPENDENCY}")
           exit(0)

       # Run tests...
       exit(0 if all_passed else 1)
   ```

The test runner will automatically discover and run your new test file.

## Test Output

Tests use a consistent output format:

- `[PASS]` - Test passed successfully
- `[FAIL]` - Test failed
- `[SKIP]` - Test skipped due to missing dependencies

## Exit Codes

- `0` - All tests passed or were skipped
- `1` - One or more tests failed

This allows integration with CI/CD pipelines.
