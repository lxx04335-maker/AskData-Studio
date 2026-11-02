"""Run existing unittest suites without API credentials or external HTTP requests."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    os.environ['LLM_API_KEY'] = ''
    os.environ['SESSION_ARCHIVE_ENABLED'] = 'false'
    os.environ['SHORT_TERM_SUMMARY_ENABLED'] = 'false'
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.discover(str(backend / 'tests'), top_level_dir=str(backend)),
        loader.loadTestsFromName('evaluation.test_scoring'),
    ])
    with patch('urllib.request.urlopen', side_effect=AssertionError('Offline verification forbids external HTTP')):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
