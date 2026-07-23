"""Paths for read-only resources bundled with PMCA."""

import os
import sys


def get_bundle_root():
    """Return the source tree or PyInstaller extraction directory."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.abspath(sys._MEIPASS)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_bundle_resource_path(relative_path):
    """Resolve a read-only resource without depending on the working directory."""
    relative_path = os.fspath(relative_path)
    if os.path.isabs(relative_path):
        raise ValueError('Bundle resource path must be relative: %s' % relative_path)

    root = get_bundle_root()
    path = os.path.abspath(os.path.join(root, relative_path))
    if os.path.commonpath((root, path)) != root:
        raise ValueError('Bundle resource path escapes bundle root: %s' % relative_path)
    return path
