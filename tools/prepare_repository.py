"""Fill publishing metadata using the actual, confirmed GitHub repository.

Run before first push:
  python3 tools/prepare_repository.py OWNER/REPOSITORY --maintainer USERNAME
This does not create a GitHub repository or publish any files.
"""
import argparse
import json
from pathlib import Path
import re


def prepare(root: Path, repository: str, maintainer: str):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Expected a real GitHub OWNER/REPOSITORY')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', maintainer):
        raise ValueError('Expected a real GitHub maintainer username')
    path = root / 'custom_components/irtrans/manifest.json'
    manifest = json.loads(path.read_text())
    url = 'https://github.com/' + repository
    manifest.update(documentation=url, issue_tracker=url+'/issues', codeowners=['@'+maintainer])
    ordered = {key: manifest[key] for key in ('domain','name')}
    ordered.update({key:manifest[key] for key in sorted(manifest) if key not in ordered})
    path.write_text(json.dumps(ordered,indent=2)+'\n')
    return url


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repository')
    parser.add_argument('--maintainer', required=True)
    args = parser.parse_args()
    print(prepare(Path(__file__).resolve().parents[1],args.repository,args.maintainer))
