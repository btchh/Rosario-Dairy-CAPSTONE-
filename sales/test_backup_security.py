"""Do not confuse POSIX modes with Windows ACL protection."""
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from sales.backups.security import create_private_directory


def windows_security_descriptor(path):
    """Read the native ACL as SDDL, without localized account-name parsing."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer),
        ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [pointer,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    descriptor, text = pointer(), wintypes.LPWSTR()
    # SE_FILE_OBJECT=1, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION=5.
    error = advapi.GetNamedSecurityInfoW(str(path), 1, 5, None, None, None, None,
                                       ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 5, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, pointer))
        kernel.LocalFree(descriptor)


class PrivateBackupDirectoryTests(SimpleTestCase):
    def test_older_windows_runtime_fails_before_creating_a_directory(self):
        path = Mock()
        with patch('sales.backups.security.os_name', 'nt'), \
                patch('sales.backups.security.version_info', (3, 12, 3)):
            with self.assertRaisesMessage(OSError, 'Python 3.12.4'):
                create_private_directory(path)
        path.mkdir.assert_not_called()

    def test_supported_windows_versions_request_a_new_private_directory(self):
        for version in ((3, 12, 4), (3, 13, 0), (3, 14, 0)):
            path = Mock()
            with self.subTest(version=version), \
                    patch('sales.backups.security.os_name', 'nt'), \
                    patch('sales.backups.security.version_info', version), \
                    patch('sales.backups.security._require_windows_acl_filesystem') as verify:
                create_private_directory(path)
            verify.assert_called_once_with(path)
            path.mkdir.assert_called_once_with(parents=True, exist_ok=False, mode=0o700)

    def test_windows_acl_verification_failure_stops_before_directory_creation(self):
        path = Mock()
        with patch('sales.backups.security.os_name', 'nt'), \
                patch('sales.backups.security.version_info', (3, 13)), \
                patch('sales.backups.security._require_windows_acl_filesystem',
                      side_effect=OSError('Windows ACLs unavailable')):
            with self.assertRaisesMessage(OSError, 'Windows ACLs unavailable'):
                create_private_directory(path)
        path.mkdir.assert_not_called()

    @skipUnless(os.name == 'posix', 'POSIX directory mode verification.')
    def test_posix_directory_is_private_and_cannot_reuse_existing_output(self):
        with TemporaryDirectory() as parent:
            path = Path(parent)/'private'
            create_private_directory(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                create_private_directory(path)

    @skipUnless(os.name == 'nt', 'Requires native Windows ACL APIs and an ACL-capable filesystem.')
    def test_windows_private_directory_and_inherited_file_acl(self):
        with TemporaryDirectory() as parent:
            folder = Path(parent)/'private'
            create_private_directory(folder)
            backup = folder/'backup.sql'
            backup.write_text('-- Unicode ₱\n', encoding='utf-8')
            for path in (folder, backup):
                descriptor = windows_security_descriptor(path)
                owner = re.search(r'O:(.*?)(?:D:|$)', descriptor).group(1)
                allowed = {'SY', 'BA', 'OW', owner}
                entries = [entry.split(';') for entry in re.findall(r'\(([^)]+)\)', descriptor)]
                self.assertTrue(entries, descriptor)  # Reject empty/unrestricted DACLs.
                for entry in entries:
                    self.assertEqual(entry[0], 'A', descriptor)
                    self.assertIn(entry[5], allowed, descriptor)
                if path == folder:
                    self.assertIn('P', descriptor.split('D:', 1)[1].split('(', 1)[0])
            self.assertEqual(backup.read_text(encoding='utf-8'), '-- Unicode ₱\n')
