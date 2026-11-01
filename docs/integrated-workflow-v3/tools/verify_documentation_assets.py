"""Read-only evidence hash verification. Does not import or execute project code."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots', required=True, type=Path)
    args = parser.parse_args()
    roots = json.loads(args.roots.read_text())
    doc_root = Path(__file__).resolve().parents[1]
    results = []
    for filename in ['source_index.json', 'dsh_source_index.json', 'fleet_source_index.json']:
        for row in json.loads((doc_root / 'evidence' / filename).read_text()):
            value = roots.get(row['root_key'])
            result = {'id': row['id'], 'root_key': row['root_key'], 'relative_path': row['relative_path']}
            if not value:
                result['status'] = 'UNKNOWN'
                results.append(result)
                continue
            root = Path(value).resolve()
            relative = Path(row['relative_path'])
            path = (root / relative).resolve()
            if relative.is_absolute() or root not in path.parents:
                raise ValueError('Unsafe relative path in evidence index')
            if not path.is_file():
                result['status'] = 'MISSING'
            else:
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                        digest.update(block)
                result['status'] = 'MATCH' if digest.hexdigest() == row['sha256'] else 'MISMATCH'
            results.append(result)
    failures = [r for r in results if r['status'] != 'MATCH']
    print(json.dumps({'checked': len(results), 'matched': len(results)-len(failures),
                      'failures': failures, 'scope': 'File identity only; not CAD acceptance'},
                     ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
