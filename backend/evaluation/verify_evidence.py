"""Recompute historical metrics from public excerpts without model calls."""
from __future__ import annotations

import json
import re
from pathlib import Path

from evaluation.run import judge


def main() -> None:
    root = Path(__file__).parent / 'evidence' / '2026-10-20'
    for name in ('r0-live', 'r0-edge', 'r1-scope', 'r2-confirm'):
        folder = root / name
        read = lambda path: json.loads(path.read_text(encoding='utf8'))
        specs = {c['id']: c for c in read(folder / 'cases.v1.json')['cases']}
        gold = read(folder / 'gold.json')
        expected = {c['id']: c for c in read(folder / 'audited-summary.json')['cases']}
        results = []
        recalled = required = annotated = 0
        for path in sorted((folder / 'cases').glob('*.json')):
            raw = read(path)
            case = specs[raw['id']]
            result = raw['result']
            if case['kind'].startswith('guard_') or raw.get('exception'):
                passed = raw['passed']  # Preserved historical assertion, not rerun here.
            elif raw.get('category') == 'dependency':
                passed = False
            else:
                passed, _ = judge(case, result, gold, raw.get('setup_result'))
                if result.get('workflow_mode') == 'route_fallback':
                    passed = False
            assert bool(passed) == expected[case['id']]['passed'], case['id']
            results.append((case['kind'], bool(passed)))
            fields = set(case.get('required_fields') or [])
            if fields:
                annotated += 1
                hits = {h['doc_id'] for h in (result.get('retrieval') or {}).get('hits', [])}
                recalled += len(fields & hits)
                required += len(fields)
        query = [p for kind, p in results if kind == 'query']
        print(json.dumps({'round': name, 'recorded_tasks': len(results),
                          'query_passed': sum(query), 'query_total': len(query),
                          'annotated_executed_cases': annotated,
                          'required_field_hits': recalled, 'required_field_total': required}, ensure_ascii=False))
        if name == 'r1-scope':
            assert (sum(query), len(query), recalled, required) == (17, 19, 45, 49)
    log = (root / 'full-regression-after.txt').read_text(encoding='utf8')
    assert re.search(r'Ran 71 tests', log) and re.search(r'\bOK\s*$', log)
    print('Historical unittest log: 71 tests, OK. This is not a current regression run.')


if __name__ == '__main__':
    main()
