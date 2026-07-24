import unittest
from unittest.mock import patch

from pmca import plugins


class PluginTest(unittest.TestCase):
 def tearDown(self):
  plugins._loaded = None

 def test_builtin_system_plugin_does_not_depend_on_directory_scanning(self):
  plugins._loaded = None
  with patch.object(plugins.pkgutil, 'iter_modules', return_value=[]):
   discovered = plugins._discover()
  self.assertIn('pmca.plugins.system', [module.__name__ for module in discovered])


if __name__ == '__main__':
 unittest.main()
