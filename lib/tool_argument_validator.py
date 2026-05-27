"""
Tool Argument Validator

Provides secure validation and sanitization of tool arguments from LLM responses
to prevent injection attacks and ensure type safety.
"""

import logging
from typing import Any, Dict, List, Set


class ToolArgumentValidationError(Exception):
    """Raised when tool argument validation fails"""
    pass


class ToolArgumentValidator:
    """
    Validates and sanitizes tool arguments against their schema definition.

    Security features:
    - Type validation against JSON Schema
    - Required parameter validation
    - Prevents extra/unexpected parameters
    - String length limits
    - Numeric range validation
    - Recursive validation for nested objects/arrays
    """

    # Security limits
    MAX_STRING_LENGTH = 50000  # Maximum length for string arguments
    MAX_ARRAY_LENGTH = 1000    # Maximum number of items in arrays
    MAX_OBJECT_KEYS = 100      # Maximum number of keys in objects
    MAX_NESTING_DEPTH = 10     # Maximum nesting depth for objects/arrays

    def __init__(self, tool_schema: Dict[str, Any]):
        """
        Initialize validator with tool schema.

        Args:
            tool_schema: The tool's function schema containing parameters definition
        """
        self.tool_name = tool_schema.get('name', 'unknown')
        self.parameters_schema = tool_schema.get('parameters', {})
        self.properties = self.parameters_schema.get('properties', {})
        self.required = set(self.parameters_schema.get('required', []))

        if not isinstance(self.properties, dict):
            raise ValueError(f"Invalid schema for tool '{self.tool_name}': properties must be a dict")

    def validate(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and sanitize tool arguments.

        Args:
            arguments: The arguments dict from LLM response

        Returns:
            Validated and sanitized arguments dict

        Raises:
            ToolArgumentValidationError: If validation fails
        """
        if not isinstance(arguments, dict):
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': arguments must be a dict, got {type(arguments).__name__}"
            )

        # Check for required parameters
        missing_required = self.required - set(arguments.keys())
        if missing_required:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': missing required parameters: {missing_required}"
            )

        # Check for unexpected parameters
        unexpected = set(arguments.keys()) - set(self.properties.keys())
        if unexpected:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': unexpected parameters: {unexpected}"
            )

        # Validate each parameter
        validated = {}
        for param_name, param_value in arguments.items():
            param_schema = self.properties.get(param_name, {})
            try:
                validated[param_name] = self._validate_value(
                    param_value,
                    param_schema,
                    param_name,
                    depth=0
                )
            except ToolArgumentValidationError:
                raise
            except Exception as e:
                raise ToolArgumentValidationError(
                    f"Tool '{self.tool_name}': error validating parameter '{param_name}': {str(e)}"
                )

        return validated

    def _validate_value(self, value: Any, schema: Dict[str, Any], path: str, depth: int) -> Any:
        """
        Validate a single value against its schema.

        Args:
            value: The value to validate
            schema: The JSON Schema for this value
            path: The path to this value (for error messages)
            depth: Current nesting depth

        Returns:
            Validated and sanitized value
        """
        if depth > self.MAX_NESTING_DEPTH:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': maximum nesting depth exceeded at '{path}'"
            )

        expected_type = schema.get('type')

        if expected_type is None:
            # No type specified, return as-is but log warning
            logging.warning(f"Tool '{self.tool_name}': no type specified for parameter '{path}'")
            return value

        # Validate type and apply type-specific rules
        if expected_type == 'string':
            return self._validate_string(value, schema, path)
        elif expected_type == 'number' or expected_type == 'integer':
            return self._validate_number(value, schema, path, expected_type)
        elif expected_type == 'boolean':
            return self._validate_boolean(value, path)
        elif expected_type == 'array':
            return self._validate_array(value, schema, path, depth)
        elif expected_type == 'object':
            return self._validate_object(value, schema, path, depth)
        elif expected_type == 'null':
            if value is not None:
                raise ToolArgumentValidationError(
                    f"Tool '{self.tool_name}': parameter '{path}' must be null"
                )
            return None
        else:
            logging.warning(f"Tool '{self.tool_name}': unknown type '{expected_type}' for parameter '{path}'")
            return value

    def _validate_string(self, value: Any, schema: Dict[str, Any], path: str) -> str:
        """Validate string type and apply length limits"""
        if not isinstance(value, str):
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be string, got {type(value).__name__}"
            )

        # Check length constraints
        min_length = schema.get('minLength', 0)
        max_length = schema.get('maxLength', self.MAX_STRING_LENGTH)

        # Apply security limit
        max_length = min(max_length, self.MAX_STRING_LENGTH)

        if len(value) < min_length:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be at least {min_length} characters"
            )

        if len(value) > max_length:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' exceeds maximum length of {max_length} characters"
            )

        # Check pattern if specified
        pattern = schema.get('pattern')
        if pattern:
            import re
            if not re.match(pattern, value):
                raise ToolArgumentValidationError(
                    f"Tool '{self.tool_name}': parameter '{path}' does not match required pattern"
                )

        # Check enum if specified
        enum_values = schema.get('enum')
        if enum_values and value not in enum_values:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be one of {enum_values}"
            )

        return value

    def _validate_number(self, value: Any, schema: Dict[str, Any], path: str, expected_type: str) -> float:
        """Validate numeric type and apply range limits"""
        # Auto-coerce bool → int/float since LLMs frequently pass
        # booleans for numeric fields (True→1, False→0)
        if isinstance(value, bool):
            value = int(value) if expected_type == 'integer' else float(value)
        elif expected_type == 'integer':
            if not isinstance(value, int):
                raise ToolArgumentValidationError(
                    f"Tool '{self.tool_name}': parameter '{path}' must be integer, got {type(value).__name__}"
                )
        else:
            if not isinstance(value, (int, float)):
                raise ToolArgumentValidationError(
                    f"Tool '{self.tool_name}': parameter '{path}' must be number, got {type(value).__name__}"
                )

        # Check range constraints
        minimum = schema.get('minimum')
        maximum = schema.get('maximum')

        if minimum is not None and value < minimum:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be >= {minimum}"
            )

        if maximum is not None and value > maximum:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be <= {maximum}"
            )

        return value

    def _validate_boolean(self, value: Any, path: str) -> bool:
        """Validate boolean type"""
        if not isinstance(value, bool):
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be boolean, got {type(value).__name__}"
            )
        return value

    def _validate_array(self, value: Any, schema: Dict[str, Any], path: str, depth: int) -> List[Any]:
        """Validate array type and validate items"""
        if not isinstance(value, list):
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be array, got {type(value).__name__}"
            )

        # Check length constraints
        min_items = schema.get('minItems', 0)
        max_items = schema.get('maxItems', self.MAX_ARRAY_LENGTH)

        # Apply security limit
        max_items = min(max_items, self.MAX_ARRAY_LENGTH)

        if len(value) < min_items:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must have at least {min_items} items"
            )

        if len(value) > max_items:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' exceeds maximum of {max_items} items"
            )

        # Validate items if schema specified
        items_schema = schema.get('items', {})
        if items_schema:
            validated_items = []
            for i, item in enumerate(value):
                validated_items.append(
                    self._validate_value(item, items_schema, f"{path}[{i}]", depth + 1)
                )
            return validated_items

        return value

    def _validate_object(self, value: Any, schema: Dict[str, Any], path: str, depth: int) -> Dict[str, Any]:
        """Validate object type and validate properties"""
        if not isinstance(value, dict):
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' must be object, got {type(value).__name__}"
            )

        # Security limit on object keys
        if len(value) > self.MAX_OBJECT_KEYS:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' exceeds maximum of {self.MAX_OBJECT_KEYS} keys"
            )

        # Validate properties if schema specified
        properties_schema = schema.get('properties', {})
        required_props = set(schema.get('required', []))

        # Check required properties
        missing = required_props - set(value.keys())
        if missing:
            raise ToolArgumentValidationError(
                f"Tool '{self.tool_name}': parameter '{path}' missing required properties: {missing}"
            )

        # Validate each property
        validated_obj = {}
        for key, val in value.items():
            prop_schema = properties_schema.get(key, {})
            validated_obj[key] = self._validate_value(
                val, prop_schema, f"{path}.{key}", depth + 1
            )

        return validated_obj


def validate_tool_arguments(tool_schema: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function to validate tool arguments.

    Args:
        tool_schema: The tool's function schema
        arguments: The arguments from LLM response

    Returns:
        Validated arguments

    Raises:
        ToolArgumentValidationError: If validation fails
    """
    validator = ToolArgumentValidator(tool_schema)
    return validator.validate(arguments)
