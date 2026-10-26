"""Synthetic portable-cache proof; no competition files are read or modified."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import os
import sys
import tempfile
import unittest

from migration import install_verified_cache_adapter


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.data=self.root/'synthetic.dat';self.data.write_bytes(b'synthetic')
        self.legacy={'path':r'C:\old-root\synthetic.dat','bytes':9,'mtime_ns':123}
        self.entry={'path':'synthetic.dat','sha256':hashlib.sha256(b'synthetic').hexdigest(),'legacy_state':self.legacy}
        self.manifest=self.root/'migration.json'
        self.module=SimpleNamespace(file_state=lambda path:{'unmapped':str(path)})

    def install(self,entry=None):
        self.manifest.write_text(json.dumps({'files':[entry or self.entry]}))
        with patch.dict(sys.modules,{'workflow_io':self.module}):
            install_verified_cache_adapter(self.root,self.manifest)

    def test_verified_bytes_adapt_legacy_state_and_preserve_source_file(self):
        before=self.data.read_bytes();self.install()
        self.assertEqual(self.module.file_state(self.legacy['path']),self.legacy)
        self.assertEqual(self.data.read_bytes(),before)
        self.assertEqual(self.module.file_state('unlisted'),{'unmapped':'unlisted'})

    def test_wrong_content_hash_fails_even_if_size_matches(self):
        self.data.write_bytes(b'FAKEBYTES')
        with self.assertRaises(ValueError):self.install()

    def test_path_escape_is_rejected(self):
        entry=dict(self.entry,path='../outside.dat')
        with self.assertRaises(ValueError):self.install(entry)

    def test_target_modified_after_verification_is_rejected(self):
        self.install()
        stamp=self.data.stat().st_mtime_ns
        os.utime(self.data,ns=(stamp,stamp+1000000))
        with self.assertRaises(ValueError):self.module.file_state(self.legacy['path'])


if __name__=='__main__':unittest.main(verbosity=2)
