"""Full project check: original unittest regression plus the joint in-place replacement verification."""
import json
import sys
import unittest
from pathlib import Path

from receive.joint import joint_verification

ROOT = Path(__file__).resolve().parent


def run_regression() -> bool:
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT))
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
    return successful


def run_joint() -> bool:
    try:
        outcome = joint_verification(ROOT / 'samples')
    except AssertionError as error:
        print(f'JOINT FAILURE {error}', file=sys.stderr)
        return False
    print('JOINT VERIFICATION ' + json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return True


def main() -> int:
    regression_ok = run_regression()
    joint_ok = run_joint()
    return 0 if regression_ok and joint_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
