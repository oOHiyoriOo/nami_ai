"""Pre-import modules that test fixtures may mock globally.

Some test files (test_api_server.py, test_app_initializer.py) replace
sys.modules entries with MagicMock during their module-scoped fixtures.
Pre-importing these here ensures the real modules are cached before any
fixture can mock them.
"""
import numpy  # noqa: F401
import sklearn  # noqa: F401
import sklearn.cluster  # noqa: F401
