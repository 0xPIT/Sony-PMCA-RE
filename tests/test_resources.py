import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pmca.commands import usb


class ResourcePathTest(unittest.TestCase):
 def test_install_app_resolves_certificate_from_bundle(self):
  certificate = str(Path('bundle', 'certs', 'localtest.me.pem'))
  with mock.patch.object(
    usb, 'get_bundle_resource_path', return_value=certificate
  ) as resolver, mock.patch.object(
    usb, 'LocalMarketServer', side_effect=RuntimeError('stop after resolution')
  ) as server:
   with self.assertRaisesRegex(RuntimeError, 'stop after resolution'):
    usb.installApp(object())

  resolver.assert_called_once_with('certs/localtest.me.pem')
  server.assert_called_once_with(certificate)

 def test_fdat_paths_are_joined_from_the_bundle_resource(self):
  with tempfile.TemporaryDirectory(prefix='PMCA fdat ') as bundle:
   fdat_root = Path(bundle, 'updatershell', 'fdat')
   directory = fdat_root / 'device-family'
   directory.mkdir(parents=True)
   (fdat_root / 'device-family.dat').write_bytes(b'payload')
   (directory / 'ILCE-TEST.hdr').write_bytes(b'header')

   with mock.patch.object(
     usb, 'get_bundle_resource_path', return_value=str(fdat_root)
   ) as resolver:
    files = dict(usb.getFdats())

  resolver.assert_called_once_with('updatershell/fdat')
  self.assertEqual(
   files['ILCE-TEST'],
   (str(directory / 'ILCE-TEST.hdr'), str(fdat_root / 'device-family.dat')),
  )


if __name__ == '__main__':
 unittest.main()
