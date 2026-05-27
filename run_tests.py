#!/usr/bin/env python3
"""
Test Runner - Runs all Python test files in the tests/ directory
"""

import os
import sys
import subprocess
from pathlib import Path


def find_test_files(test_dir: Path) -> list:
    """Find all Python test files in the tests directory"""
    test_files = []
    for file in test_dir.glob("test_*.py"):
        test_files.append(file)
    return sorted(test_files)


def run_test_file(test_file: Path) -> dict:
    """
    Run a single test file via pytest and return the result.

    Uses ``python -m pytest`` so that pytest handles sys.path setup
    automatically — avoids ModuleNotFoundError when test files import
    from the project root (``lib.*``, ``OllamaTools.*``, etc.).

    Returns:
        dict with keys: 'file', 'passed', 'output', 'error'
    """
    print(f"\n{'='*70}")
    print(f"Running: {test_file.name}")
    print(f"{'='*70}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=60
        )

        # Print output
        if result.stdout:
            print(result.stdout)

        # Check if test passed (exit code 0)
        passed = result.returncode == 0

        if not passed and result.stderr:
            print(f"\nERROR OUTPUT:")
            print(result.stderr)

        return {
            'file': test_file.name,
            'passed': passed,
            'output': result.stdout,
            'error': result.stderr
        }

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Test exceeded 60 second timeout")
        return {
            'file': test_file.name,
            'passed': False,
            'output': '',
            'error': 'Test timeout'
        }
    except Exception as e:
        print(f"[ERROR] Failed to run test: {e}")
        return {
            'file': test_file.name,
            'passed': False,
            'output': '',
            'error': str(e)
        }


def main():
    """Main test runner"""
    print("="*70)
    print("Test Runner - Running all tests in tests/ directory")
    print("="*70)

    # Find tests directory
    script_dir = Path(__file__).parent
    tests_dir = script_dir / "tests"

    if not tests_dir.exists():
        print(f"[ERROR] Tests directory not found: {tests_dir}")
        return 1

    # Find all test files
    test_files = find_test_files(tests_dir)

    if not test_files:
        print(f"[WARNING] No test files found in {tests_dir}")
        print("Test files should be named: test_*.py")
        return 0

    print(f"\nFound {len(test_files)} test file(s):\n")
    for test_file in test_files:
        print(f"  - {test_file.name}")

    # Run all tests
    results = []
    for test_file in test_files:
        result = run_test_file(test_file)
        results.append(result)

    # Print summary
    print(f"\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}\n")

    passed_count = sum(1 for r in results if r['passed'])
    failed_count = len(results) - passed_count

    for result in results:
        status = "[PASS]" if result['passed'] else "[FAIL]"
        print(f"{status} {result['file']}")
        if not result['passed'] and result['error']:
            print(f"       Error: {result['error'][:100]}")

    print(f"\n{'='*70}")
    print(f"Results: {passed_count}/{len(results)} test files passed")
    print(f"{'='*70}\n")

    if failed_count > 0:
        print(f"[FAILURE] {failed_count} test file(s) failed")
        return 1
    else:
        print("[SUCCESS] All tests passed!")
        return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
