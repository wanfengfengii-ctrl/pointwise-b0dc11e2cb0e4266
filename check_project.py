"""Run this project's full unittest suite and the dual-block business checks."""
import sys
import tempfile
import unittest
from pathlib import Path


def run_suite() -> int:
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


def run_block_checks() -> int:
    from block_recording import verify_all

    try:
        with tempfile.TemporaryDirectory() as folder:
            verify_all(Path(folder))
    except BaseException as exc:  # noqa: BLE001 - report any business-check failure
        print(f'BLOCK CAPTURE FAIL {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print('BLOCK CAPTURE PASS')
    return 0


def main():
    suite = run_suite()
    blocks = run_block_checks()
    return 0 if suite == 0 and blocks == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
