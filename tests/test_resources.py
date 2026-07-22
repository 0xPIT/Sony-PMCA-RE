import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlparse

from pmca import resources


class ResourcePathTests(unittest.TestCase):
    def test_source_path_is_independent_of_working_directory(self):
        expected = os.path.join(resources.get_bundle_root(), 'assets', 'index.html')
        previous = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            self.assertEqual(
                resources.get_bundle_resource_path('assets/index.html'),
                expected,
            )
        finally:
            os.chdir(previous)

    def test_frozen_path_uses_meipass(self):
        with tempfile.TemporaryDirectory(prefix='PMCA bundle ') as bundle:
            with mock.patch.object(sys, 'frozen', True, create=True), \
                    mock.patch.object(sys, '_MEIPASS', bundle, create=True):
                self.assertEqual(
                    resources.get_bundle_resource_path('assets/index.html'),
                    os.path.join(bundle, 'assets', 'index.html'),
                )

    def test_resource_cannot_escape_bundle(self):
        with self.assertRaises(ValueError):
            resources.get_bundle_resource_path('../outside.txt')
        with self.assertRaises(ValueError):
            resources.get_bundle_resource_path(os.path.abspath('outside.txt'))


class StartupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        module_path = Path(__file__).parents[1] / 'pmca-web.py'
        spec = importlib.util.spec_from_file_location('pmca_web_resources_test', module_path)
        cls.web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.web)

    def test_file_url_handles_spaces_and_non_ascii(self):
        with tempfile.TemporaryDirectory(prefix='PMCA Leerzeichen ä ') as bundle:
            for relative in self.web._REQUIRED_WEB_ASSETS:
                path = Path(bundle, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test', encoding='utf-8')

            def resolve(relative):
                return os.path.join(bundle, relative)

            with mock.patch.object(self.web, 'get_bundle_resource_path', resolve):
                page = self.web.get_startup_page()

            parsed = urlparse(page['url'])
            self.assertEqual(parsed.scheme, 'file')
            self.assertEqual(parsed.netloc, '')
            self.assertEqual(
                os.path.normcase(unquote(parsed.path).lstrip('/').replace('/', os.sep)),
                os.path.normcase(os.path.join(bundle, 'assets', 'index.html')),
            )

    def test_missing_assets_produce_controlled_error_page(self):
        with tempfile.TemporaryDirectory(prefix='PMCA incomplete ') as bundle:
            def resolve(relative):
                return os.path.join(bundle, relative)

            with mock.patch.object(self.web, 'get_bundle_resource_path', resolve):
                page = self.web.get_startup_page()

            self.assertIn('html', page)
            self.assertIn('PMCA could not start', page['html'])
            self.assertIn('assets/index.html', page['html'])
            self.assertIn('assets/icon.png', page['html'])

    def test_windows_does_not_pass_png_as_runtime_icon(self):
        with mock.patch.object(self.web.sys, 'platform', 'win32'), \
                mock.patch.object(self.web, 'get_bundle_resource_path', return_value='missing-icon.png'):
            self.assertEqual(self.web.get_webview_start_options(), {})

    def test_non_windows_preserves_existing_runtime_icon(self):
        with tempfile.NamedTemporaryFile(suffix='.png') as icon, \
                mock.patch.object(self.web.sys, 'platform', 'darwin'), \
                mock.patch.object(self.web, 'get_bundle_resource_path', return_value=icon.name):
            self.assertEqual(self.web.get_webview_start_options(), {'icon': icon.name})

    def test_missing_runtime_icon_is_optional(self):
        with mock.patch.object(self.web.sys, 'platform', 'linux'), \
                mock.patch.object(self.web, 'get_bundle_resource_path', return_value='missing-icon.png'):
            self.assertEqual(self.web.get_webview_start_options(), {})


if __name__ == '__main__':
    unittest.main()
