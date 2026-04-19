#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime

from weiyun_sdk.client import WeiyunClient


def format_size(n: int) -> str:
    """将字节数转为人类可读的字符串，如 1.2M、830.4K。"""
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            if unit == "B":
                return f"{n}{unit}"
            return f"{n:.1f}{unit}"
        n /= 1024


def format_ts(ms: int) -> str:
    """将毫秒时间戳转为 'YYYY-MM-DD HH:MM' 字符串。"""
    if not ms:
        return "-" * 16
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def print_ls(res: dict) -> None:
    """以类似 ls -lh 的格式打印目录列表（目录在前，文件在后）。"""
    rows = []

    for d in sorted(res.get("dir_list", []), key=lambda x: x.get("dir_name", "")):
        rows.append(("d", format_ts(d.get("dir_mtime", 0)), "-", d.get("dir_name", "") + "/"))

    for f in sorted(res.get("file_list", []), key=lambda x: x.get("filename", "")):
        rows.append(("-", format_ts(f.get("file_mtime", 0)), format_size(f.get("file_size", 0)), f.get("filename", "")))

    if not rows:
        return

    # 对齐：size 列右对齐，取最大宽度
    size_width = max(len(r[2]) for r in rows)
    for type_char, ts, size, name in rows:
        print(f"{type_char}  {ts}  {size:>{size_width}}  {name}")


def resolve_path_to_dir(client: WeiyunClient, path: str):
    """
    将语义路径（如 '/文档/项目A'）逐级解析为 (dir_key, pdir_key)。

    返回值:
        (dir_key, pdir_key):
          - dir_key:  目标目录的 dir_key
          - pdir_key: 列举目标目录时 list 响应的顶层 pdir_key，
                      可直接用于 delete/share 操作
    """
    if not path or path == '/':
        return None, None

    parts = [p for p in path.split('/') if p]
    current_dir_key = None
    current_pdir_key = None

    for i, part in enumerate(parts):
        offset = 0
        limit = 50
        found = False
        target_dir_key = None
        next_level_pdir_key = None

        while True:
            res = client.list(get_type=1, offset=offset, limit=limit,
                              dir_key=current_dir_key, pdir_key=current_pdir_key)

            if 'error' in res:
                raise RuntimeError(f"Error listing directory: {res['error']}")

            if next_level_pdir_key is None:
                next_level_pdir_key = res.get("pdir_key")

            for d in res.get("dir_list", []):
                if d.get("dir_name") == part:
                    target_dir_key = d.get("dir_key")
                    found = True
                    break

            if found or res.get("finish_flag", True):
                break

            offset += limit

        if not found:
            current_path = '/' + '/'.join(parts[:i]) if i > 0 else '/'
            raise ValueError(f"Directory '{part}' not found in '{current_path}'")

        current_dir_key = target_dir_key
        current_pdir_key = next_level_pdir_key

    return current_dir_key, current_pdir_key


def resolve_path_to_dir_key(client: WeiyunClient, path: str) -> str:
    """仅返回 dir_key（供 upload --path 兼容调用）。"""
    dir_key, _ = resolve_path_to_dir(client, path)
    return dir_key


def resolve_entry(client: WeiyunClient, id_or_key: str, pdir_key: str = None):
    """
    自动识别 id_or_key 是文件还是目录，返回 (entry_type, resolved_id, resolved_pdir_key)。

    - id_or_key 以 '/' 开头时视为路径，在父目录中同时搜索文件和目录。
    - 否则视为 ID/KEY，需提供 --pdir_key，通过列举父目录来判断类型。

    返回:
        entry_type:       "file" 或 "dir"
        resolved_id:      file_id 或 dir_key
        resolved_pdir_key: 用于 delete/share 的 pdir_key
    """
    if id_or_key.startswith('/'):
        # 路径模式：导航到父目录，同时搜索文件和目录
        path = id_or_key.rstrip('/')
        slash_pos = path.rfind('/')
        parent_path = path[:slash_pos] or '/'
        name = path[slash_pos + 1:]

        if not name:
            raise ValueError(f"Invalid path (appears to be root): {id_or_key}")

        if parent_path == '/':
            parent_dir_key = None
            parent_pdir_key = None
        else:
            parent_dir_key, parent_pdir_key = resolve_path_to_dir(client, parent_path)

        offset = 0
        limit = 50
        list_pdir_key = None

        while True:
            res = client.list(get_type=0, offset=offset, limit=limit,
                              dir_key=parent_dir_key, pdir_key=parent_pdir_key)

            if 'error' in res:
                raise RuntimeError(f"Error listing directory: {res['error']}")

            if list_pdir_key is None:
                list_pdir_key = res.get("pdir_key")

            for d in res.get("dir_list", []):
                if d.get("dir_name") == name:
                    return "dir", d.get("dir_key"), list_pdir_key

            for f in res.get("file_list", []):
                if f.get("filename") == name:
                    return "file", f.get("file_id"), list_pdir_key

            if res.get("finish_flag", True):
                break

            offset += limit

        raise ValueError(f"'{name}' not found in '{parent_path}'")

    else:
        # ID/KEY 模式：在父目录中搜索该 ID，以判断类型
        if not pdir_key:
            raise ValueError("--pdir_key is required when id_or_key is not a path")

        offset = 0
        limit = 50

        while True:
            # pdir_key 即为父目录的 dir_key，用于列举父目录内容
            res = client.list(get_type=0, offset=offset, limit=limit, dir_key=pdir_key)

            if 'error' in res:
                raise RuntimeError(f"Error listing directory: {res['error']}")

            for d in res.get("dir_list", []):
                if d.get("dir_key") == id_or_key:
                    return "dir", id_or_key, pdir_key

            for f in res.get("file_list", []):
                if f.get("file_id") == id_or_key:
                    return "file", id_or_key, pdir_key

            if res.get("finish_flag", True):
                break

            offset += limit

        raise ValueError(f"ID/Key '{id_or_key}' not found in the specified directory")


def main():
    parser = argparse.ArgumentParser(
        description="Weiyun MCP Python SDK CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list /Documents/ProjectA
  %(prog)s list --token YOUR_TOKEN
  %(prog)s upload /path/to/file --token YOUR_TOKEN --path /Documents/ProjectA
  %(prog)s download /Documents/ProjectA/file.txt --token YOUR_TOKEN
  %(prog)s download FILE_ID --pdir_key PDIR_KEY --token YOUR_TOKEN
  %(prog)s delete /Documents/ProjectA/file.txt --token YOUR_TOKEN
  %(prog)s delete /Documents/ProjectA --token YOUR_TOKEN
  %(prog)s delete ID_OR_KEY --pdir_key PDIR_KEY --token YOUR_TOKEN
  %(prog)s share /Documents/ProjectA/file.txt --token YOUR_TOKEN
  %(prog)s share /Documents/ProjectA --token YOUR_TOKEN
  %(prog)s share ID_OR_KEY --pdir_key PDIR_KEY --token YOUR_TOKEN
  %(prog)s check_update 1.0.0
"""
    )
    parser.add_argument("--token", default=os.environ.get("WEIYUN_MCP_TOKEN"),
                        help="MCP token (or WEIYUN_MCP_TOKEN env var)")
    parser.add_argument("--mcp_url",
                        default=os.environ.get("WEIYUN_MCP_URL", "https://www.weiyun.com/api/v3/mcpserver"),
                        help="MCP server URL")
    parser.add_argument("--env_id", default=os.environ.get("WEIYUN_ENV_ID"),
                        help="Environment ID (or WEIYUN_ENV_ID env var)")

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # List
    list_parser = subparsers.add_parser("list", help="List directory contents")
    list_parser.add_argument("path", nargs='?', default=None,
                             help="Directory path to list (e.g. /a/b/c/); overrides --dir_key/--pdir_key")
    list_parser.add_argument("--get_type", type=int, default=0, help="0-all, 1-dir only, 2-file only")
    list_parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    list_parser.add_argument("--limit", type=int, default=50, help="Pagination limit (max 50)")
    list_parser.add_argument("--order_by", type=int, default=0, help="0-none, 1-name, 2-mtime")
    list_parser.add_argument("--asc", action="store_true", help="Ascending order")
    list_parser.add_argument("--dir_key", default=None, help="Directory key")
    list_parser.add_argument("--pdir_key", default=None, help="Parent directory key")
    list_parser.add_argument("--format", choices=["json", "ls"], default="json",
                             help="Output format: json (default) or ls (-lh style)")

    # Download
    dl_parser = subparsers.add_parser("download", help="Get download link")
    dl_parser.add_argument("file_id_or_path",
                           help="File ID, or path starting with / (e.g. /a/b/file.txt) to auto-resolve")
    dl_parser.add_argument("--pdir_key", default=None,
                           help="Parent directory key (required when file_id_or_path is not a path)")

    # Delete
    del_parser = subparsers.add_parser("delete", help="Delete file or directory")
    del_parser.add_argument("id_or_key",
                            help="File ID, Dir Key, or path starting with / (e.g. /a/b/file.txt or /a/b/dir)")
    del_parser.add_argument("--pdir_key", default=None,
                            help="Parent directory key (required when id_or_key is not a path)")
    del_parser.add_argument("--completely", action="store_true",
                            help="Delete completely (not to recycle bin)")

    # Upload
    up_parser = subparsers.add_parser("upload", help="Upload a file")
    up_parser.add_argument("file_path", help="Local file path")
    up_group = up_parser.add_mutually_exclusive_group()
    up_group.add_argument("--pdir_key", default=None, help="Parent directory key to upload to")
    up_group.add_argument("--path", default=None,
                          help="Semantic folder path (e.g. /Documents/ProjectA) to upload to")
    up_parser.add_argument("--max_rounds", type=int, default=50, help="Max upload rounds")

    # Gen share link
    share_parser = subparsers.add_parser("share", help="Generate share link")
    share_parser.add_argument("id_or_key",
                              help="File ID, Dir Key, or path starting with / (e.g. /a/b/file.txt or /a/b/dir)")
    share_parser.add_argument("--pdir_key", default=None,
                              help="Parent directory key (required when id_or_key is not a path)")
    share_parser.add_argument("--name", default=None, help="Share name")

    # Check update
    update_parser = subparsers.add_parser("check_update", help="Check for skill update")
    update_parser.add_argument("version", help="Current version")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command != "check_update" and not args.token:
        print("Error: --token is required (or set WEIYUN_MCP_TOKEN environment variable).")
        sys.exit(1)

    client = WeiyunClient(token=args.token, mcp_url=args.mcp_url, env_id=args.env_id)

    try:
        if args.command == "list":
            dir_key = args.dir_key
            pdir_key = args.pdir_key
            if args.path:
                print(f"Resolving path '{args.path}'...", file=sys.stderr)
                dir_key, pdir_key = resolve_path_to_dir(client, args.path)
            res = client.list(args.get_type, args.offset, args.limit, args.order_by, args.asc,
                              dir_key, pdir_key)
            if args.format == "ls":
                print_ls(res)
            else:
                print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "download":
            if args.file_id_or_path.startswith('/'):
                print(f"Resolving path '{args.file_id_or_path}'...", file=sys.stderr)
                entry_type, resolved_id, resolved_pdir_key = resolve_entry(client, args.file_id_or_path)
                if entry_type != "file":
                    print(f"Error: '{args.file_id_or_path}' is a directory, not a file.", file=sys.stderr)
                    sys.exit(1)
                file_id, pdir_key = resolved_id, resolved_pdir_key
            else:
                file_id = args.file_id_or_path
                pdir_key = args.pdir_key
                if not pdir_key:
                    print("Error: --pdir_key is required when file_id_or_path is not a path.",
                          file=sys.stderr)
                    sys.exit(1)
            res = client.download([{"file_id": file_id, "pdir_key": pdir_key}])
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "delete":
            print(f"Resolving '{args.id_or_key}'...", file=sys.stderr)
            entry_type, resolved_id, resolved_pdir_key = resolve_entry(
                client, args.id_or_key, args.pdir_key)
            if entry_type == "file":
                res = client.delete(
                    file_list=[{"file_id": resolved_id, "pdir_key": resolved_pdir_key}],
                    delete_completely=args.completely)
            else:
                res = client.delete(
                    dir_list=[{"dir_key": resolved_id, "pdir_key": resolved_pdir_key}],
                    delete_completely=args.completely)
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "upload":
            print(f"Uploading {args.file_path}...")
            target_pdir_key = args.pdir_key
            if getattr(args, 'path', None):
                print(f"Resolving path '{args.path}'...")
                target_pdir_key = resolve_path_to_dir_key(client, args.path)
            res = client.upload(args.file_path, pdir_key=target_pdir_key, max_rounds=args.max_rounds)
            print("Upload successful!")
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "share":
            print(f"Resolving '{args.id_or_key}'...", file=sys.stderr)
            entry_type, resolved_id, resolved_pdir_key = resolve_entry(
                client, args.id_or_key, args.pdir_key)
            if entry_type == "file":
                res = client.gen_share_link(
                    file_list=[{"file_id": resolved_id, "pdir_key": resolved_pdir_key}],
                    share_name=args.name)
            else:
                res = client.gen_share_link(
                    dir_list=[{"dir_key": resolved_id, "pdir_key": resolved_pdir_key}],
                    share_name=args.name)
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "check_update":
            update_client = WeiyunClient(token=args.token or "", mcp_url=args.mcp_url, env_id=args.env_id)
            res = update_client.check_skill_update(args.version)
            print(json.dumps(res, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
