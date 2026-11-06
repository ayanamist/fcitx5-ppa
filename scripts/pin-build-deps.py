#!/usr/bin/env python3
"""Raise internal Build-Depends to the upstream versions handed off by this run."""
import json
from pathlib import Path
import re
import subprocess
import sys


BUILD_FIELD = re.compile(
    r'^(Build-Depends(?:-Arch|-Indep)?):([^\n]*(?:\n[ \t][^\n]*)*)', re.M | re.I)
ATOM = re.compile(
    r'(?P<leading>\s*)(?P<name>[a-z0-9][a-z0-9+.-]*)'
    r'(?P<qualifier>:[a-z0-9][a-z0-9-]*)?'
    r'(?:\s*\((?P<relation><<|<=|=|>=|>>)\s*(?P<version>[^\s()]+)\s*\))?'
    r'(?P<suffix>(?:\s*\[[^\]]+\])?(?:\s*<[^<>]+>)*\s*)')


def compare(left, relation, right):
    result = subprocess.run(['dpkg', '--compare-versions', left, relation, right])
    if result.returncode not in (0, 1):
        raise ValueError(f'Invalid version comparison: {left} {relation} {right}')
    return result.returncode == 0


def mapping(package):
    for line in Path(__file__).with_name('deps.map').read_text().splitlines():
        words = line.split('#', 1)[0].split()
        if words and words[0] == package:
            return dict(item.split(':', 1) for item in words[1:])
    return {}


def package_fields(block):
    fields = {}
    for line in block.splitlines():
        if line and not line[0].isspace() and ':' in line:
            key, value = line.split(':', 1)
            fields[key] = value.strip()
    return fields


def resolve_versions(packages, dependencies, series, arch):
    """Use exact binary metadata, including epochs, rather than source versions."""
    versions = {}
    for block in re.split(r'\n\s*\n', packages):
        fields = package_fields(block)
        name = fields.get('Package')
        if name not in dependencies or fields.get('Architecture') not in (arch, 'all'):
            continue
        version = fields['Version']
        source = re.fullmatch(r'([^\s()]+)(?:\s+\(([^\s()]+)\))?',
                              fields.get('Source', name))
        source_version = (source[2] or version) if source else ''
        if (not source or source[1] != dependencies[name]
                or not re.search(r'~' + re.escape(series) + r'1~ppa[1-9][0-9]*(?:\+b[0-9]+)?$',
                                 source_version)):
            raise ValueError(f'{name}: wrong source or series in local artifact')
        if name not in versions or compare(version, 'gt', versions[name]):
            versions[name] = version
    missing = dependencies.keys() - versions.keys()
    if missing:
        raise ValueError('Missing internal dependency artifacts: ' + ', '.join(sorted(missing)))
    return versions


def upstream_version(version):
    # Native versions have no Debian revision; remove our PPA suffix first.
    version = re.sub(r'~[a-z][a-z0-9]*1~ppa[1-9][0-9]*(?:\+b[0-9]+)?$', '', version)
    # Debian's final hyphen separates the packaging revision. Keep the epoch and
    # upstream version, including prereleases and +ds/+dfsg repack identifiers.
    return version.rsplit('-', 1)[0] if '-' in version else version


def pin_control(control, versions):
    # Only source Build-Depends fields; binary Depends and all other text stay intact.
    source_end = re.search(r'\n[ \t]*\n', control)
    end = source_end.start() if source_end else len(control)
    expected = {}

    def pin_field(field):
        atoms = re.split(r'([,|])', field[2])
        for index in range(0, len(atoms), 2):
            if not atoms[index].strip():  # A trailing comma is valid in debian/control.
                continue
            atom = ATOM.fullmatch(atoms[index])
            if not atom:
                raise ValueError(f'Unsupported dependency syntax: {atoms[index]!r}')
            name = atom['name']
            if name not in versions:
                continue
            floor = upstream_version(versions[name])
            relation, old = atom['relation'], atom['version']
            if relation in ('>=', '>>', '=') and compare(old, 'ge', floor):
                floor = old  # Keep an upstream requirement that is already stronger.
            elif relation in ('=', '<<', '<='):
                raise ValueError(f'{name}: cannot raise {relation} {old} to >= {floor}')
            else:
                atoms[index] = (atom['leading'] + name + (atom['qualifier'] or '')
                                + f' (>= {floor})' + atom['suffix'])
            if name not in expected or compare(floor, 'gt', expected[name]):
                expected[name] = floor
        return field[1] + ':' + ''.join(atoms)

    pinned = BUILD_FIELD.sub(pin_field, control[:end]) + control[end:]
    return pinned, expected


def main():
    package, series, control_path, repo, expected_path = sys.argv[1:]
    control_path = Path(control_path)
    control = control_path.read_text()
    dependencies = mapping(package)
    # Do not invent Build-Depends when a deps.map entry no longer exists upstream.
    declared = set()
    source = re.split(r'\n[ \t]*\n', control, maxsplit=1)[0]
    for field in BUILD_FIELD.finditer(source):
        for token in re.split(r'[,|]', field[2]):
            if token.strip():
                atom = ATOM.fullmatch(token)
                if not atom:
                    raise ValueError(f'Unsupported dependency syntax: {token!r}')
                declared.add(atom['name'])
    for name in dependencies.keys() - declared:
        print(f'::warning::{name} from deps.map is not in Build-Depends; skip', file=sys.stderr)
    dependencies = {name: src for name, src in dependencies.items() if name in declared}
    expected = {}
    if dependencies:
        if not repo:
            raise ValueError('LOCAL_REPO_DIR is required for internal dependency versions')
        arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
        versions = resolve_versions((Path(repo) / 'Packages').read_text(), dependencies, series, arch)
        control, expected = pin_control(control, versions)
    # Resolve and validate everything before changing the source or publishing the expectations.
    control_path.write_text(control)
    Path(expected_path).write_text(json.dumps(expected, sort_keys=True))
    for name, version in sorted(expected.items()):
        print(f'Pinned Build-Depends: {name} >= {version}')


if __name__ == '__main__':
    main()
