#!/usr/bin/env python
from __future__ import print_function

import fnmatch
import json
import os
import zipfile


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSION_DIR = os.path.join(REPO_ROOT, "BIM3DNA.extension")
VERSION_FILE = os.path.join(EXTENSION_DIR, "BIM3DNA.version.json")
DIST_DIR = os.path.join(REPO_ROOT, "dist")
ZIP_PATH = os.path.join(DIST_DIR, "BIM3DNA.extension.zip")

EXCLUDED_DIR_NAMES = set([
    ".git",
    "__pycache__",
    "logs",
    "Results",
    "output",
    "dist",
])

EXCLUDED_FILE_NAMES = set([
    ".DS_Store",
    "create_print_set_pdf_config.json",
    "hvac_vertical_duct_tagging_config.json",
    "scanner_config.json",
])

EXCLUDED_FILE_PATTERNS = [
    "*.pyc",
    "*_config.json",
    "*.user.json",
]

INCLUDED_FILE_NAMES = set([
    "update_config.json",
])


def read_version():
    with open(VERSION_FILE, "r") as fp:
        data = json.load(fp)
    return data.get("version", "unknown")


def should_exclude_dir(dir_name):
    return dir_name in EXCLUDED_DIR_NAMES


def should_exclude_file(file_name):
    if file_name in INCLUDED_FILE_NAMES:
        return False
    if file_name in EXCLUDED_FILE_NAMES:
        return True
    for pattern in EXCLUDED_FILE_PATTERNS:
        if fnmatch.fnmatch(file_name, pattern):
            return True
    return False


def iter_package_files():
    for root, dirs, files in os.walk(EXTENSION_DIR):
        dirs[:] = [d for d in dirs if not should_exclude_dir(d)]
        for file_name in files:
            if should_exclude_file(file_name):
                continue
            source_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(source_path, os.path.dirname(EXTENSION_DIR))
            yield source_path, rel_path.replace(os.sep, "/")


def create_package():
    if not os.path.isdir(EXTENSION_DIR):
        raise RuntimeError("Extension folder not found: {0}".format(EXTENSION_DIR))
    if not os.path.exists(VERSION_FILE):
        raise RuntimeError("Version file not found: {0}".format(VERSION_FILE))
    if not os.path.isdir(DIST_DIR):
        os.makedirs(DIST_DIR)

    file_count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for source_path, archive_path in iter_package_files():
            zf.write(source_path, archive_path)
            file_count += 1

    return file_count


def validate_package():
    required_prefix = "BIM3DNA.extension/BIM3DNA.tab/"
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        names = zf.namelist()
    return any(name.startswith(required_prefix) for name in names)


def main():
    version = read_version()
    file_count = create_package()
    if not validate_package():
        raise RuntimeError("ZIP validation failed: missing BIM3DNA.extension/BIM3DNA.tab/")
    size_bytes = os.path.getsize(ZIP_PATH)
    print("Package path: {0}".format(ZIP_PATH))
    print("Version: {0}".format(version))
    print("File count: {0}".format(file_count))
    print("Package size bytes: {0}".format(size_bytes))


if __name__ == "__main__":
    main()
