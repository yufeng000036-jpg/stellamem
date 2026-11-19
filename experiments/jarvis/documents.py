"""Read-only access to ordinary files beneath the single granted directory."""

import ctypes
import os
import ntpath
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath

SUFFIXES = {".md", ".txt", ".csv", ".json"}
DENIED = "路径未获授权"


def reserved_name(name):
    # ntpath.isreserved replaced PurePath.is_reserved in newer Python.
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def parts_for(name):
    parts = name.replace("\\", "/").split("/")
    if (
        PureWindowsPath(name).drive
        or any(
            p in {"", ".", ".."}
            or p.endswith((" ", "."))
            or reserved_name(p)
            or any(c in '<>:"|?*' or ord(c) < 32 for c in p)
            for p in parts
        )
        or Path(parts[-1]).suffix.lower() not in SUFFIXES
    ):
        raise ValueError(DENIED)
    return parts


@contextmanager
def windows_handle(path, directory):
    # Open the reparse object itself. Deny write/delete sharing until the last child is read.
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]

    class Info(ctypes.Structure):
        _fields_ = [
            ("attributes", w.DWORD),
            ("created", w.FILETIME),
            ("access", w.FILETIME),
            ("write", w.FILETIME),
            ("volume", w.DWORD),
            ("sizehi", w.DWORD),
            ("sizelo", w.DWORD),
            ("links", w.DWORD),
            ("idhi", w.DWORD),
            ("idlo", w.DWORD),
        ]

    k.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(Info)]
    handle = k.CreateFileW(
        str(path), 0x80 if directory else 0x80000000, 1, None, 3, 0x00200000 | 0x02000000, None
    )
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = Info()
        if not k.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError(DENIED)
        if not directory and (info.links != 1 or info.sizehi or info.sizelo > 65536):
            raise ValueError("仅支持 64 KiB 以内的普通文本")
        yield handle
    finally:
        k.CloseHandle(handle)


@contextmanager
def directory(home, parts):
    base = home / "documents"
    with ExitStack() as stack:
        if os.name == "nt":
            stack.enter_context(windows_handle(base, True))
            for part in parts:
                base = base / part
                stack.enter_context(windows_handle(base, True))
            yield base
        else:
            flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
            fd = os.open(base, flags)
            stack.callback(os.close, fd)
            for part in parts:
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            yield fd


def read_doc(home, name):
    parts = parts_for(name)
    with directory(home, parts[:-1]) as parent:
        if os.name == "nt":
            from ctypes import wintypes as w

            with windows_handle(parent / parts[-1], False) as h:
                k = ctypes.WinDLL("kernel32", use_last_error=True)
                k.ReadFile.argtypes = [
                    w.HANDLE,
                    w.LPVOID,
                    w.DWORD,
                    ctypes.POINTER(w.DWORD),
                    w.LPVOID,
                ]
                buf = ctypes.create_string_buffer(65537)
                count = w.DWORD()
                if not k.ReadFile(h, buf, len(buf), ctypes.byref(count), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                data = buf.raw[: count.value]
        else:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
                    raise ValueError("仅支持 64 KiB 以内的普通文本")
                data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("文件过大")
    return data.decode("utf-8-sig")


def list_docs(home):
    with directory(home, []) as parent:
        with os.scandir(parent) as entries:
            names = []
            for item in entries:
                if Path(item.name).suffix.lower() not in SUFFIXES or not item.is_file(
                    follow_symlinks=False
                ):
                    continue
                try:
                    if os.name == "nt":
                        # DirEntry.stat has zero st_nlink on Windows. Query the opened file itself.
                        with windows_handle(parent / item.name, False):
                            names.append(item.name)
                    elif item.stat(follow_symlinks=False).st_nlink == 1:
                        names.append(item.name)
                except (OSError, ValueError):
                    continue
            return sorted(names)
