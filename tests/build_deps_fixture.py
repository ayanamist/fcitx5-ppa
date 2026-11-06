"""Pinned sid metadata and local Packages fixtures for the nine tracked sources."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / 'tests/fixtures/debian-sid/ppa-build-deps.json').read_text())


def local_packages(series):
    entries = []
    for source, fields in SOURCES.items():
        version = f'{fields["Version"]}~{series}1~ppa1'
        for binary in fields['Binary'].split(', '):
            entries.append(f'Package: {binary}\nSource: {source}\n'
                           f'Version: {version}\nArchitecture: amd64\n\n')
    return ''.join(entries)


def source_control(package):
    fields = SOURCES[package]
    return (f'Source: {package}\nMaintainer: Test <test@example.org>\n'
            + ''.join(f'{name}: {value}\n' for name, value in fields.items()
                      if name.startswith('Build-Depends'))
            + '\nPackage: fixture\nArchitecture: any\nDescription: fixture\n')
