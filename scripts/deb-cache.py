#!/usr/bin/env python3
"""Restore a complete, verified set of debs; exit 3 when local rebuild is needed."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urljoin


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fields(text):
    result = {}
    key = None
    for line in text.splitlines():
        if line.startswith((' ', '\t')) and key:
            result[key] += '\n' + line.strip()
        elif ':' in line:
            key, value = line.split(':', 1)
            result[key] = value.strip()
    return result


def validate(path, package, version, arch):
    control = fields(subprocess.check_output(['dpkg-deb', '-f', str(path)], text=True))
    # Binary packages can have independent versions (e.g. librime plugins).
    # Debian records their source version in Source: name (version); when
    # omitted, it is the same as the binary Version.
    source = re.fullmatch(r'([^\s()]+)(?:\s+\(([^\s()]+)\))?',
                          control.get('Source', control['Package']))
    source_version = (source.group(2) or control['Version']) if source else None
    if (not source or source.group(1) != package or source_version != version
            or control['Architecture'] not in (arch, 'all')):
        raise ValueError(f'Wrong package/version/architecture: {path.name}')


def manifest(directory, names, package, version, arch):
    files = []
    for name in names:
        path = directory / name
        validate(path, package, version, arch)
        files.append({'name': name, 'sha256': digest(path), 'size': path.stat().st_size})
    if not files:
        raise ValueError('No deb artifacts')
    return {'package': package, 'version': version, 'arch': arch, 'files': files}


def safe_name(name):
    return Path(name).name == name and name not in ('.', '..')


def cached(directory, package, version, arch):
    try:
        data = json.loads((directory / '.manifest.json').read_text())
        if (data['package'], data['version'], data['arch']) != (package, version, arch) or not data['files']:
            return False
        if {p.name for p in directory.glob('*.deb')} != {entry['name'] for entry in data['files']}:
            return False
        for entry in data['files']:
            if not safe_name(entry['name']):
                return False
            path = directory / entry['name']
            if path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
                return False
            validate(path, package, version, arch)
        return True
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return False


def fetch(url, path):
    subprocess.run(['curl', '-fsSL', '--retry', '3', '-H', 'Cache-Control: no-cache',
                    '-o', str(path), url], check=True)


def restore(directory, package, version, arch, builds):
    if cached(directory, package, version, arch):
        print('Complete local deb cache verified')
        return True
    for build in builds:
        if build.get('buildstate') != 'Successfully built' or build.get('arch_tag') != arch:
            continue
        url = build.get('changesfile_url')
        if not url:
            continue
        # Stage everything before replacing cache files or publishing the manifest.
        with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
            staging = Path(tmp)
            fetch(url, staging / 'build.changes')
            changes = fields((staging / 'build.changes').read_text())
            if changes.get('Source', '').split()[0] != package or changes.get('Version') != version:
                raise ValueError('Wrong source/version in changes file')
            names = []
            for line in changes['Checksums-Sha256'].splitlines():
                if not line.strip():
                    continue
                sha, size, name = line.split()
                if not name.endswith('.deb'):
                    continue
                if not safe_name(name) or not re.fullmatch(r'[0-9a-f]{64}', sha) or name in names:
                    raise ValueError('Invalid changes manifest')
                destination = staging / name
                local = directory / name
                if local.is_file() and local.stat().st_size == int(size) and digest(local) == sha:
                    destination.write_bytes(local.read_bytes())
                else:
                    fetch(urljoin(url, quote(name)), destination)
                if destination.stat().st_size != int(size) or digest(destination) != sha:
                    raise ValueError(f'Checksum mismatch: {name}')
                names.append(name)
            data = manifest(staging, names, package, version, arch)
            directory.mkdir(exist_ok=True)
            for old in directory.glob('*.deb'):
                old.unlink()
            for name in names:
                os.replace(staging / name, directory / name)
            (directory / '.manifest.json').write_text(json.dumps(data))
            print(f'Restored {len(names)} debs from Launchpad')
            return True
    return False


def main():
    mode, package, version, arch, location = sys.argv[1:]
    directory = Path(location)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'record':
        data = manifest(directory, sorted(p.name for p in directory.glob('*.deb')), package, version, arch)
        (directory / '.manifest.json').write_text(json.dumps(data))
    elif not restore(directory, package, version, arch, json.load(sys.stdin).get('builds', [])):
        sys.exit(3)


if __name__ == '__main__':
    main()
