"""Private backup directories using the operating system's access controls."""
from os import name as os_name
from sys import version_info


def _require_windows_acl_filesystem(path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    parent = path.parent.resolve()
    while not parent.exists():
        if parent == parent.parent:
            raise OSError('Cannot verify Windows ACL support on the backup volume.')
        parent = parent.parent
    volume, flags = ctypes.create_unicode_buffer(32768), wintypes.DWORD()
    if not kernel.GetVolumePathNameW(str(parent), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None,
                                        ctypes.byref(flags), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not flags.value & 0x00000008:  # FILE_PERSISTENT_ACLS
        raise OSError('Private Windows backups require a filesystem supporting Windows ACLs, such as NTFS.')


def create_private_directory(path):
    # Python 3.12 gained this protection in 3.12.4; it is also in 3.13+.
    # 0700 creates a protected DACL with inheritable access for the owner,
    # SYSTEM and administrators. Files inherit that ACL from this new directory.
    # chmod(0600) alone only changes a Windows read-only attribute.
    if os_name == 'nt' and version_info < (3, 12, 4):
        raise OSError('Private Windows backups require Python 3.12.4 or later '
                      'and a filesystem supporting Windows ACLs.')
    if os_name == 'nt':
        _require_windows_acl_filesystem(path)
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
