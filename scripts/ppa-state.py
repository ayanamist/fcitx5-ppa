#!/usr/bin/env python3
"""Classify one Debian version using complete Launchpad publication/build history."""
import json
import re
import subprocess
import sys
from urllib.parse import urlencode


def collection(url):
    entries = []
    while url:
        response = json.loads(subprocess.check_output(
            ['curl', '-fsSL', '-H', 'Cache-Control: no-cache', url], text=True))
        entries.extend(response['entries'])
        url = response.get('next_collection_link')
    return entries


def classify(publication):
    status = publication['status']
    if status not in ('Pending', 'Published'):
        return 'unknown'
    builds = collection(publication['self_link'] + '?ws.op=getBuilds')
    states = [build['buildstate'] for build in builds]
    running = {'Needs building', 'Currently building', 'Uploading build',
               'Gathering build output', 'Dependency wait'}
    known = running | {'Successfully built', 'Failed to build', 'Failed to upload'}
    if any(state not in known for state in states):
        return 'unknown'
    if not states or any(state in running for state in states):
        return 'pending'
    if any(state in ('Failed to build', 'Failed to upload') for state in states):
        return 'failed'
    return 'success' if status == 'Published' else 'pending'


def main():
    owner, ppa, package, series, version = sys.argv[1:6]
    archive = f'https://api.launchpad.net/1.0/~{owner}/+archive/ubuntu/{ppa}'
    publications = collection(archive + '?' + urlencode({
        'ws.op': 'getPublishedSources', 'source_name': package, 'exact_match': 'true'}))
    pattern = re.compile(re.escape(f'{version}~{series}1~ppa') + r'([0-9]+)$')
    matches = [(int(m[1]), p) for p in publications
               if (m := pattern.fullmatch(p['source_package_version']))]
    max_n = max((n for n, _ in matches), default=0)
    candidates = [(n, p) for n, p in matches
                  if p['distro_series_link'] == f'https://api.launchpad.net/1.0/ubuntu/{series}']
    decisions = [(n, p, classify(p)) for n, p in candidates]
    # A successful revision satisfies this Debian version even if another attempt failed.
    chosen = next((max((d for d in decisions if d[2] == state), key=lambda d: d[0])
                   for state in ('success', 'pending') if any(d[2] == state for d in decisions)), None)
    if chosen is None and decisions:
        chosen = max(decisions, key=lambda d: d[0])
    state = chosen[2] if chosen else ('unknown' if matches else 'absent')
    active = chosen[1]['source_package_version'] if state in ('success', 'pending') else ''
    print(json.dumps({'max_n': str(max_n) if max_n else '',
                      'active_version': active, 'state': state,
                      'builds': collection(chosen[1]['self_link'] + '?ws.op=getBuilds') if active else []}))


if __name__ == '__main__':
    main()
