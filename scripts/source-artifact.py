#!/usr/bin/env python3
"""Prepare and validate a complete signed source upload between Actions jobs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def fields(path):
    text = path.read_text()
    if not text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n'):
        raise ValueError(f'Unsigned source metadata: {path.name}')
    text = text.split('\n\n', 1)[1].split('\n-----BEGIN PGP SIGNATURE-----', 1)[0]
    result = {}
    key = None
    for line in text.splitlines():
        if line.startswith('- '):
            line = line[2:]
        if line.startswith((' ', '\t')) and key:
            result[key] += '\n' + line.strip()
        elif ':' in line:
            key, value = line.split(':', 1)
            result[key] = value.strip()
    return result


def checksums(data):
    result = {}
    for line in data['Checksums-Sha256'].splitlines():
        if not line.strip():
            continue
        sha, size, name = line.split()
        if (Path(name).name != name or name in ('.', '..') or name in result
                or not re.fullmatch(r'[0-9a-f]{64}', sha) or not size.isdecimal()):
            raise ValueError('Invalid source checksum entry')
        result[name] = {'sha256': sha, 'size': int(size)}
    if not result:
        raise ValueError('Empty source checksum manifest')
    return result


def fingerprint(path):
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'size': path.stat().st_size}


def validate(directory, package, series):
    directory = Path(directory)
    data = json.loads((directory / 'source-upload.json').read_text())
    if data['package'] != package or data['series'] != series:
        raise ValueError('Wrong source package or series')
    if not re.fullmatch(re.escape(data['debian_version'] + f'~{series}1~ppa') + r'[1-9][0-9]*',
                        data['version']):
        raise ValueError('Invalid PPA source version')
    if data['upload_required'] is False:
        return data
    if data['upload_required'] is not True:
        raise ValueError('Invalid upload decision')
    for name, expected in data['files'].items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid artifact path')
        if fingerprint(directory / name) != expected:
            raise ValueError(f'Source artifact checksum mismatch: {name}')
    changes_name = data['changes']
    if changes_name not in data['files'] or not changes_name.endswith('_source.changes'):
        raise ValueError('Missing source changes file')
    changes = fields(directory / changes_name)
    if (changes['Source'] != package or changes['Version'] != data['version']
            or changes['Distribution'] != series or changes['Architecture'] != 'source'):
        raise ValueError('Wrong source upload metadata')
    entries = checksums(changes)
    if set(data['files']) != set(entries) | {changes_name, 'signing-key.asc'}:
        raise ValueError('Incomplete source upload artifact')
    if not (directory / 'signing-key.asc').read_bytes().startswith(
            b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('Expected a public signing key')
    if any(name.endswith('.deb') for name in entries):
        raise ValueError('Binary uploads are not permitted')
    for name, expected in entries.items():
        if data['files'][name] != expected:
            raise ValueError('Source checksum differs from signed changes')
    dsc_names = [name for name in entries if name.endswith('.dsc')]
    if len(dsc_names) != 1:
        raise ValueError('Expected one source descriptor')
    dsc = fields(directory / dsc_names[0])
    if dsc['Source'] != package or dsc['Version'] != data['version']:
        raise ValueError('Wrong source descriptor')
    for name, expected in checksums(dsc).items():
        if entries.get(name) != expected:
            raise ValueError(f'Missing original source input: {name}')
    return data


def prepare(directory, package, series, debian_version, version, changes_path):
    directory, changes_path = Path(directory), Path(changes_path)
    directory.mkdir(parents=True, exist_ok=True)
    entries = checksums(fields(changes_path))
    for name in entries:
        shutil.copyfile(changes_path.parent / name, directory / name)
    shutil.copyfile(changes_path, directory / changes_path.name)
    public_key = subprocess.check_output(['gpg', '--batch', '--armor', '--export',
                                          os.environ['GPG_KEY_ID']])
    if not public_key.startswith(b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('Cannot export public signing key')
    (directory / 'signing-key.asc').write_bytes(public_key)
    data = {'package': package, 'series': series, 'debian_version': debian_version,
            'upload_required': True,
            'version': version, 'changes': changes_path.name,
            'files': {name: fingerprint(directory / name)
                      for name in [*entries, changes_path.name, 'signing-key.asc']}}
    (directory / 'source-upload.json').write_text(json.dumps(data))
    return validate(directory, package, series)


def skip(directory, package, series, debian_version, version):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data = {'package': package, 'series': series, 'debian_version': debian_version,
            'version': version, 'upload_required': False}
    (directory / 'source-upload.json').write_text(json.dumps(data))
    return validate(directory, package, series)


def plan(directory, series, stage, packages):
    packages = json.loads(packages)
    if not isinstance(packages, list) or not packages or len(set(packages)) != len(packages):
        raise ValueError('Expected unique stage packages')
    result = []
    for package in packages:
        if not isinstance(package, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]+', package):
            raise ValueError('Invalid source package name')
        source = Path(directory) / f'source-{series}-stage{stage}-{package}'
        data = validate(source, package, series)
        if data['upload_required']:
            result.append({'directory': str(source), **data})
    return result


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare(*sys.argv[2:])
    elif sys.argv[1] == 'validate':
        print(json.dumps(validate(*sys.argv[2:])))
    elif sys.argv[1] == 'skip':
        skip(*sys.argv[2:])
    elif sys.argv[1] == 'plan':
        print(json.dumps(plan(*sys.argv[2:])))
    else:
        raise SystemExit('Expected prepare, validate, skip or plan')
