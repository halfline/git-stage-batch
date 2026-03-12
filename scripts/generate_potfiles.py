#!/usr/bin/env python3
"""Generate POTFILES.in by finding all Python files with translatable strings."""

import os
import re
from pathlib import Path

def has_translatable_strings(file_path):
    """Check if a Python file contains translatable strings (_() calls)."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Look for _() or _( or ngettext() calls
            return bool(re.search(r'\b_\s*\(|ngettext\s*\(', content))
    except (OSError, UnicodeDecodeError):
        return False

def find_translatable_files(src_dir):
    """Find all Python files with translatable strings."""
    translatable_files = []
    src_path = Path(src_dir).resolve()

    for py_file in sorted(src_path.rglob('*.py')):
        # Skip __pycache__ and test files
        if '__pycache__' in str(py_file) or '/tests/' in str(py_file):
            continue

        if has_translatable_strings(py_file):
            # Make path relative to src_dir parent (project root)
            rel_path = py_file.relative_to(src_path.parent.parent)
            translatable_files.append(str(rel_path))

    return translatable_files

def main():
    """Generate POTFILES.in."""
    src_dir = 'src/git_stage_batch'

    if not os.path.exists(src_dir):
        print(f"Error: {src_dir} not found")
        return 1

    files = find_translatable_files(src_dir)

    # Write POTFILES.in
    with open('po/POTFILES.in', 'w') as f:
        f.write('# Auto-generated list of files with translatable strings\n')
        f.write('# Run scripts/generate_potfiles.py to regenerate\n')
        for file in files:
            f.write(f'{file}\n')

    print(f"Generated POTFILES.in with {len(files)} files")
    return 0

if __name__ == '__main__':
    exit(main())
