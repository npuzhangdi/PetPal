"""PE 资源写入工具（纯 ctypes，不依赖任何编译器）。

用途
----
PetPal.exe 的常驻进程本质是 .venv\\Scripts\\PetPal.exe（Python 解释器启动器的
副本）。它的图标和版本信息默认是 Python 的，会让任务管理器 / 资源管理器显示成
Python 原生图标。本模块用 Win32 的 BeginUpdateResource / UpdateResource API
就地改写 exe 内的资源，把图标与版本信息换成 PetPal 自己的。

为什么不用编译器
----------------
改写已有 exe 的资源不需要编译：UpdateResource 是 Windows 自带的资源编辑 API，
Python 通过 ctypes 就能调用，因此 first_run.bat 无需捆绑 gcc / rc 等工具链。

支持的能力
----------
  set_app_icon(exe, ico)        替换图标（RT_GROUP_ICON + RT_ICON）
  set_version_info(exe, **kw)   写入版本信息（RT_VERSION）

两者都要求目标 exe 当前没有被运行。
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from pathlib import Path

# ----------------------------------------------------------------------
# Win32 常量
# ----------------------------------------------------------------------
RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16

LOAD_LIBRARY_AS_DATAFILE = 0x00000002

# VS_FIXEDFILEINFO
VS_FFI_SIGNATURE = 0xFEEF04BD
VS_FFI_STRUCVERSION = 0x00010000
VS_FFI_FILEFLAGSMASK = 0x0000003F
VOS_NT_WINDOWS32 = 0x00040004
VFT_APP = 0x00000001

#: 资源语言：0 = 语言中性。写新资源时使用，读取时以实际枚举到的语言为准。
LANG_NEUTRAL = 0x0000

# ----------------------------------------------------------------------
# kernel32 绑定
#
# 注意：lpType / lpName 既可能是整数 ID（MAKEINTRESOURCE），也可能是字符串
# 指针，所以统一按 c_void_p 声明，避免 ctypes 对 c_wchar_p 做多余的字符串转换。
# ----------------------------------------------------------------------
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_BeginUpdateResourceW = _kernel32.BeginUpdateResourceW
_BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
_BeginUpdateResourceW.restype = wintypes.HANDLE

_UpdateResourceW = _kernel32.UpdateResourceW
_UpdateResourceW.argtypes = [
    wintypes.HANDLE,    # hUpdate
    ctypes.c_void_p,    # lpType
    ctypes.c_void_p,    # lpName
    wintypes.WORD,      # wLanguage
    ctypes.c_void_p,    # lpData
    wintypes.DWORD,     # cbData
]
_UpdateResourceW.restype = wintypes.BOOL

_EndUpdateResourceW = _kernel32.EndUpdateResourceW
_EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
_EndUpdateResourceW.restype = wintypes.BOOL

_LoadLibraryExW = _kernel32.LoadLibraryExW
_LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
_LoadLibraryExW.restype = wintypes.HMODULE

_FreeLibrary = _kernel32.FreeLibrary
_FreeLibrary.argtypes = [wintypes.HMODULE]
_FreeLibrary.restype = wintypes.BOOL

_ENUMRESNAMEPROCW = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_EnumResourceNamesW = _kernel32.EnumResourceNamesW
_EnumResourceNamesW.argtypes = [
    wintypes.HMODULE, ctypes.c_void_p, _ENUMRESNAMEPROCW, ctypes.c_void_p
]
_EnumResourceNamesW.restype = wintypes.BOOL

_ENUMRESLANGPROCW = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.WORD, ctypes.c_void_p,
)
_EnumResourceLanguagesW = _kernel32.EnumResourceLanguagesW
_EnumResourceLanguagesW.argtypes = [
    wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
    _ENUMRESLANGPROCW, ctypes.c_void_p,
]
_EnumResourceLanguagesW.restype = wintypes.BOOL


class ResourceError(RuntimeError):
    """资源写入失败。"""


def _rid(value: int) -> ctypes.c_void_p:
    """把整数资源 ID 包装为 MAKEINTRESOURCE 指针（低位 16 位即 ID）。"""
    return ctypes.c_void_p(value)


def _with_data(blob: bytes) -> tuple[ctypes.Array, ctypes.c_void_p, int]:
    """把 bytes 放进 ctypes 缓冲区，返回 (缓冲区, 数据指针, 长度)。

    缓冲区必须由调用方持有到 API 调用结束，否则指针会悬空。
    """
    buf = ctypes.create_string_buffer(blob, len(blob))
    return buf, ctypes.cast(buf, ctypes.c_void_p), len(blob)


# ----------------------------------------------------------------------
# .ico 解析
# ----------------------------------------------------------------------
def _parse_ico(blob: bytes) -> list[dict]:
    """解析 .ico，返回每个图像条目的信息。"""
    if len(blob) < 6:
        raise ResourceError("图标文件过小，不是有效的 .ico")
    reserved, ico_type, count = struct.unpack_from("<HHH", blob, 0)
    if reserved != 0 or ico_type != 1 or count <= 0:
        raise ResourceError("不是有效的 .ico 文件（ICONDIR 头部异常）")

    entries: list[dict] = []
    for index in range(count):
        offset = 6 + index * 16
        if offset + 16 > len(blob):
            raise ResourceError("图标目录项越界")
        width, height, colors, _reserved, planes, bpp, size, data_off = struct.unpack_from(
            "<BBBBHHII", blob, offset
        )
        if data_off + size > len(blob):
            raise ResourceError("图标图像数据越界")
        entries.append(
            {
                "width": width,
                "height": height,
                "colors": colors,
                "planes": planes,
                "bpp": bpp,
                "size": size,
                "data": blob[data_off: data_off + size],
            }
        )
    return entries


def _build_group_icon(entries: list[dict], first_id: int) -> bytes:
    """构造 RT_GROUP_ICON 需要的 GRPICONDIR 结构。"""
    out = struct.pack("<HHH", 0, 1, len(entries))
    for index, entry in enumerate(entries):
        out += struct.pack(
            "<BBBBHHIH",
            entry["width"],
            entry["height"],
            entry["colors"],
            0,
            entry["planes"],
            entry["bpp"],
            entry["size"],
            first_id + index,
        )
    return out


# ----------------------------------------------------------------------
# 版本信息（VS_VERSIONINFO）构造
# ----------------------------------------------------------------------
def _pad4(blob: bytes) -> bytes:
    """补齐到 4 字节边界。"""
    return blob + b"\x00" * ((4 - len(blob) % 4) % 4)


def _version_node(key: str, value: bytes, value_len: int, node_type: int,
                  children: list[bytes]) -> bytes:
    """构造一个 VS_VERSIONINFO 节点。

    严格按 winver.h 的内存布局对齐：

        WORD wLength; WORD wValueLength; WORD wType;   // 6 字节 header
        WCHAR szKey[];                                 // 含结尾 NUL
        WORD Padding1[];     // 让 Value / Children 落在 DWORD 边界
        <Value>; WORD Padding2[];
        <Children>;

    注意 Padding1 必须按「6 字节 header + szKey」的整体长度对齐——这是最初
    版本读不出 FileDescription 的原因。节点尾部也补到 4 字节，保证兄弟节点对齐。
    """
    key_bytes = key.encode("utf-16-le") + b"\x00\x00"
    key_bytes += b"\x00" * ((4 - (6 + len(key_bytes)) % 4) % 4)

    body = key_bytes
    if value:
        body += value
        body += b"\x00" * ((4 - len(value) % 4) % 4)
    for child in children:
        body += child

    total = 6 + len(body)
    tail = (4 - total % 4) % 4
    return struct.pack("<HHH", total + tail, value_len, node_type) + body + b"\x00" * tail


def _string_node(key: str, text: str) -> bytes:
    """构造 String 节点。wValueLength 是字符数（含结尾 NUL）。"""
    value = text.encode("utf-16-le") + b"\x00\x00"
    return _version_node(key, value, len(text) + 1, 1, [])


def _build_version_resource(fields: dict[str, str], lang_cp: int = 0x040904B0,
                            file_version: tuple[int, int, int, int] = (0, 0, 0, 0),
                            product_version: tuple[int, int, int, int] | None = None
                            ) -> bytes:
    """构造完整的 RT_VERSION 资源数据。"""
    if product_version is None:
        product_version = file_version

    string_table = _version_node(
        "%08X" % lang_cp, b"", 0, 1,
        [_string_node(k, v) for k, v in fields.items()],
    )
    string_file_info = _version_node("StringFileInfo", b"", 0, 1, [string_table])
    var_file_info = _version_node(
        "VarFileInfo", b"", 0, 0,
        [_version_node("Translation", struct.pack("<I", lang_cp), 4, 0, [])],
    )

    # VS_FIXEDFILEINFO = 13 个 DWORD
    fixed = struct.pack(
        "<13I",
        VS_FFI_SIGNATURE,
        VS_FFI_STRUCVERSION,
        (file_version[0] << 16) | file_version[1],
        (file_version[2] << 16) | file_version[3],
        (product_version[0] << 16) | product_version[1],
        (product_version[2] << 16) | product_version[3],
        VS_FFI_FILEFLAGSMASK,
        0,
        VOS_NT_WINDOWS32,
        VFT_APP,
        0,
        0,
        0,
    )
    return _version_node("VS_VERSION_INFO", fixed, len(fixed), 0,
                         [string_file_info, var_file_info])


# ----------------------------------------------------------------------
# 资源枚举（含真实语言 ID）
# ----------------------------------------------------------------------
def enum_resources(exe: str | Path, res_type: int) -> list[tuple[int, int]]:
    """枚举某类型下所有「整数 ID 资源」，返回 [(id, language), ...]。

    仅考虑整数 ID 资源（图标 / 版本都是），字符串命名的资源会被忽略。
    """
    exe = Path(exe)
    module = _LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not module:
        raise ResourceError("无法以数据文件方式加载 %s（错误码 %d）"
                            % (exe, ctypes.get_last_error()))

    names: list[int] = []

    def _on_name(_module, _type, name, _param):
        value = name or 0
        if value >> 16 == 0:            # IS_INTRESOURCE
            names.append(value & 0xFFFF)
        return True

    results: list[tuple[int, int]] = []
    try:
        _EnumResourceNamesW(module, _rid(res_type), _ENUMRESNAMEPROCW(_on_name), None)
        for res_id in names:
            def _on_lang(_module, _type, _name, lang, _param, _rid=res_id):
                results.append((_rid, lang))
                return True

            _EnumResourceLanguagesW(module, _rid(res_type), _rid(res_id),
                                    _ENUMRESLANGPROCW(_on_lang), None)
    finally:
        _FreeLibrary(module)
    return results


# ----------------------------------------------------------------------
# 公开接口
# ----------------------------------------------------------------------
def set_app_icon(exe: str | Path, ico: str | Path) -> int:
    """把 exe 的图标替换为 ico 的内容，返回写入的图像张数。

    做法：先删除 exe 中已有的 RT_GROUP_ICON / RT_ICON 资源（含各自真实语言），
    再按 .ico 里的每张图像写入 RT_ICON，并写一个指向它们的 RT_GROUP_ICON。
    """
    exe = Path(exe)
    ico = Path(ico)
    if not exe.is_file():
        raise ResourceError("目标 exe 不存在: %s" % exe)
    if not ico.is_file():
        raise ResourceError("图标文件不存在: %s" % ico)

    entries = _parse_ico(ico.read_bytes())
    if not entries:
        raise ResourceError(".ico 中没有图像")

    # 枚举必须在 BeginUpdateResource 之前完成：一旦开始更新，文件即被独占
    old_groups = enum_resources(exe, RT_GROUP_ICON)
    old_icons = enum_resources(exe, RT_ICON)
    group_id = min((gid for gid, _ in old_groups), default=1)
    first_icon_id = 1

    handle = _BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise ResourceError(
            "BeginUpdateResource 失败（错误码 %d）。"
            "请确认该 exe 当前没有被运行。" % ctypes.get_last_error()
        )

    try:
        # 1) 删除旧资源：lpData = NULL 且 cbData = 0 表示删除
        for gid, lang in old_groups:
            _UpdateResourceW(handle, _rid(RT_GROUP_ICON), _rid(gid), lang, None, 0)
        for iid, lang in old_icons:
            _UpdateResourceW(handle, _rid(RT_ICON), _rid(iid), lang, None, 0)

        # 2) 写入新的 RT_ICON（缓冲区需存活到调用返回）
        for index, entry in enumerate(entries):
            _buf, ptr, size = _with_data(entry["data"])
            ok = _UpdateResourceW(handle, _rid(RT_ICON),
                                  _rid(first_icon_id + index), LANG_NEUTRAL, ptr, size)
            if not ok:
                raise ResourceError("写入 RT_ICON 失败（错误码 %d）"
                                    % ctypes.get_last_error())

        # 3) 写入 RT_GROUP_ICON
        group = _build_group_icon(entries, first_icon_id)
        _buf, ptr, size = _with_data(group)
        ok = _UpdateResourceW(handle, _rid(RT_GROUP_ICON), _rid(group_id),
                              LANG_NEUTRAL, ptr, size)
        if not ok:
            raise ResourceError("写入 RT_GROUP_ICON 失败（错误码 %d）"
                                % ctypes.get_last_error())
    except Exception:
        _EndUpdateResourceW(handle, True)   # 丢弃改动，保持原文件可用
        raise

    if not _EndUpdateResourceW(handle, False):
        raise ResourceError("EndUpdateResource 提交失败（错误码 %d）"
                            % ctypes.get_last_error())
    return len(entries)


def set_version_info(exe: str | Path, file_description: str = "PetPal",
                     product_name: str = "PetPal",
                     company_name: str = "PetPal",
                     legal_copyright: str = "PetPal",
                     original_filename: str | None = None,
                     version: tuple[int, int, int, int] = (0, 0, 0, 0),
                     extra: dict[str, str] | None = None) -> None:
    """写入 / 覆盖 exe 的版本信息（RT_VERSION）。

    FileDescription 就是任务管理器「详细信息 → 描述」列显示的文字，
    写入它可以让进程在任务管理器里更明确地显示为 PetPal。
    """
    exe = Path(exe)
    if not exe.is_file():
        raise ResourceError("目标 exe 不存在: %s" % exe)

    fields: dict[str, str] = {
        "CompanyName": company_name,
        "FileDescription": file_description,
        "FileVersion": "%d.%d.%d.%d" % version,
        "InternalName": original_filename or exe.stem,
        "LegalCopyright": legal_copyright,
        "OriginalFilename": original_filename or exe.name,
        "ProductName": product_name,
        "ProductVersion": "%d.%d.%d.%d" % version,
    }
    if extra:
        fields.update(extra)

    data = _build_version_resource(fields, file_version=version)

    # StringTable 的键是 "040904B0"，资源语言必须与之匹配，否则读取工具会忽略
    lang_id = 0x0409

    # 同样：枚举必须在 Begin 之前
    old_versions = enum_resources(exe, RT_VERSION)

    handle = _BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise ResourceError(
            "BeginUpdateResource 失败（错误码 %d）。"
            "请确认该 exe 当前没有被运行。" % ctypes.get_last_error()
        )
    try:
        for vid, lang in old_versions:
            _UpdateResourceW(handle, _rid(RT_VERSION), _rid(vid), lang, None, 0)

        _buf, ptr, size = _with_data(data)
        ok = _UpdateResourceW(handle, _rid(RT_VERSION), _rid(1), lang_id, ptr, size)
        if not ok:
            raise ResourceError("写入 RT_VERSION 失败（错误码 %d）"
                                % ctypes.get_last_error())
    except Exception:
        _EndUpdateResourceW(handle, True)
        raise

    if not _EndUpdateResourceW(handle, False):
        raise ResourceError("EndUpdateResource 提交失败（错误码 %d）"
                            % ctypes.get_last_error())


def describe(exe: str | Path) -> dict:
    """返回 exe 中图标 / 版本资源的概要，便于自检与排错。"""
    exe = Path(exe)
    return {
        "icons": enum_resources(exe, RT_ICON),
        "groups": enum_resources(exe, RT_GROUP_ICON),
        "versions": enum_resources(exe, RT_VERSION),
    }


# ----------------------------------------------------------------------
# 命令行入口（供 first_run.bat 调用）
# ----------------------------------------------------------------------
def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="PetPal PE 资源写入工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_icon = sub.add_parser("icon", help="替换 exe 图标")
    p_icon.add_argument("exe")
    p_icon.add_argument("ico")

    p_ver = sub.add_parser("version", help="写入 exe 版本信息")
    p_ver.add_argument("exe")
    p_ver.add_argument("--description", default="PetPal")
    p_ver.add_argument("--product", default="PetPal")
    p_ver.add_argument("--company", default="PetPal")
    p_ver.add_argument("--version", default="0.0.0.0")

    p_show = sub.add_parser("describe", help="查看资源概要")
    p_show.add_argument("exe")

    args = parser.parse_args(argv)

    if args.command == "icon":
        count = set_app_icon(args.exe, args.ico)
        print("[pe_res] OK 已写入 %d 张图标 -> %s" % (count, args.exe))
    elif args.command == "version":
        parts = tuple(int(x) for x in args.version.split("."))[:4]
        version = (parts + (0, 0, 0, 0))[:4]
        set_version_info(args.exe, file_description=args.description,
                         product_name=args.product, company_name=args.company,
                         version=version)
        print("[pe_res] OK 已写入版本信息 -> %s" % args.exe)
    else:
        for key, value in describe(args.exe).items():
            print("  %-8s %s" % (key, value))
    return 0


if __name__ == "__main__":
    import sys

    try:
        sys.exit(_main(sys.argv[1:]))
    except ResourceError as exc:
        print("[pe_res] ERROR %s" % exc, file=sys.stderr)
        sys.exit(1)
