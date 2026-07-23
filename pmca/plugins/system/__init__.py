"""System diagnostics plugin.

Self-contained add-on that adds the System tab and host diagnostics to the
web GUI. Deleting this directory cleanly removes the System tab and all
diagnostic checks from the application.
"""

from pmca.resources import get_bundle_resource_path

from . import web

WEB_ID = 'system'
WEB_METHODS = {
    'run': web.run,
}


def get_frontend_script():
    """Return the JS that registers the System tab, or None if unavailable."""
    try:
        path = get_bundle_resource_path('pmca/plugins/system/frontend/system.js')
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except (OSError, ValueError):
        return None


__all__ = ['WEB_ID', 'WEB_METHODS', 'get_frontend_script']
