"""Lightweight, optional plugin system for PMCA.

Each plugin is a self-contained subpackage of ``pmca.plugins`` (a directory
with an ``__init__.py``). Dropping the directory in enables the feature across
the CLI, the interactive service shell and the web GUI; deleting the directory
removes every trace of it and the core app keeps working.

A plugin module may expose any of the following optional hooks:

* ``register_cli(subparsers, drivers)`` -> ``{command_name: handler(args)}``
      Add argparse subcommands for ``pmca-console.py`` and return a mapping of
      command name to a dispatch callable.
* ``register_shell(shell, backend)``
      Add commands to the interactive ``CameraShell`` (service/updater shell).
* ``WEB_ID`` (str) and ``WEB_METHODS`` (``{name: func(api, *args)}``)
      Backend handlers reachable from the web GUI via ``api.plugin_call``.
* ``get_frontend_script()`` -> ``str | None``
      JavaScript that self-registers a tab in the web GUI. Loaded by the
      frontend only when the plugin backend is present.

Discovery and plugin imports are lazy and failure-isolated: a broken plugin is
reported and skipped, never taking the host application down with it.
"""

import pkgutil
import traceback

_loaded = None


def _discover():
    """Import and cache every plugin subpackage. Failures are isolated."""
    global _loaded
    if _loaded is not None:
        return _loaded

    _loaded = []
    for finder, name, is_pkg in pkgutil.iter_modules(__path__):
        if not is_pkg or name.startswith('_'):
            continue
        try:
            module = __import__('%s.%s' % (__name__, name), fromlist=['__name__'])
        except Exception:
            traceback.print_exc()
            continue
        _loaded.append(module)
    return _loaded


def register_cli_commands(subparsers, drivers):
    """Let plugins add CLI subcommands. Returns ``{command: handler(args)}``."""
    handlers = {}
    for module in _discover():
        hook = getattr(module, 'register_cli', None)
        if hook is None:
            continue
        try:
            handlers.update(hook(subparsers, drivers) or {})
        except Exception:
            traceback.print_exc()
    return handlers


def register_shell_commands(shell, backend):
    """Let plugins contribute interactive service-shell commands."""
    for module in _discover():
        hook = getattr(module, 'register_shell', None)
        if hook is None:
            continue
        try:
            hook(shell, backend)
        except Exception:
            traceback.print_exc()


def call_web(api, plugin_id, method, args):
    """Dispatch a web GUI call to ``plugin_id``'s ``method`` handler."""
    for module in _discover():
        if getattr(module, 'WEB_ID', None) != plugin_id:
            continue
        handler = getattr(module, 'WEB_METHODS', {}).get(method)
        if handler is not None:
            return handler(api, *args)
    return None


def get_web_plugins():
    """Return ``[{'id', 'js'}]`` for plugins that provide a frontend script."""
    plugins = []
    for module in _discover():
        plugin_id = getattr(module, 'WEB_ID', None)
        get_script = getattr(module, 'get_frontend_script', None)
        if not plugin_id or get_script is None:
            continue
        try:
            script = get_script()
        except Exception:
            traceback.print_exc()
            continue
        if script:
            plugins.append({'id': plugin_id, 'js': script})
    return plugins
