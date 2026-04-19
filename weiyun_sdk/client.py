import base64
import concurrent.futures
import json
import os
import threading
import time
import requests
from typing import Dict, Any, Callable, List, Optional, Tuple

from .upload import BLOCK_SIZE, calc_upload_params, get_sha1_backend_name


ProgressCallback = Callable[[Dict[str, Any]], None]


class WeiyunClient:
    """
    Weiyun Python SDK Client, wrapping the 6 MCP tools via HTTP JSON-RPC.
    """

    def __init__(self, token: str, mcp_url: str = "https://www.weiyun.com/api/v3/mcpserver", env_id: Optional[str] = None):
        self.mcp_url = mcp_url
        self.token = token
        self.env_id = env_id
        self._request_id = 0
        self._request_id_lock = threading.Lock()
        self._session = requests.Session()

    def _next_request_id(self) -> int:
        with self._request_id_lock:
            self._request_id += 1
            return self._request_id

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "WyHeader": f"mcp_token={self.token}",
        }
        if self.env_id:
            headers["Cookie"] = f"env_id={self.env_id}"
        return headers

    def _mcp_call_with_session(self, session: requests.Session, tool_name: str,
                               arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        # Optional tool specific headers, although general headers work for check_skill_update too.
        headers = self._get_headers()
        if tool_name == "check_skill_update":
            # This tool doesn't strictly need a token, but sending it doesn't hurt.
            pass
            
        resp = session.post(self.mcp_url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        
        # Parse MCP response structure
        content = result.get("result", {}).get("content", [])
        for item in content:
            if item.get("type") == "text":
                return json.loads(item["text"])
                
        # Fallback
        if "error" in result:
            raise RuntimeError(f"MCP Error: {result['error']}")
        return result

    def _mcp_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self._mcp_call_with_session(self._session, tool_name, arguments)

    def _upload_chunk_request(self, filename: str, file_size: int, file_sha: str, check_sha: str,
                              upload_key: str, channel_list: List[Dict[str, int]], channel_id: int,
                              ex: str, chunk: bytes) -> Dict[str, Any]:
        with requests.Session() as session:
            return self._mcp_call_with_session(session, "weiyun.upload", {
                "filename": filename,
                "file_size": file_size,
                "file_sha": file_sha,
                "block_sha_list": [],
                "check_sha": check_sha,
                "upload_key": upload_key,
                "channel_list": channel_list,
                "channel_id": channel_id,
                "ex": ex,
                "file_data": base64.b64encode(chunk).decode("utf-8"),
            })

    def _collect_available_channels(self, ch_list: List[Dict[str, Any]]) -> List[Dict[str, int]]:
        channels: List[Dict[str, int]] = []
        for channel in ch_list:
            length = int(channel.get("len", 0))
            if length <= 0:
                continue
            channels.append({
                "id": int(channel["id"]),
                "offset": int(channel["offset"]),
                "len": length,
            })
        return channels

    def _read_chunk(self, file_path: str, offset: int, length: int) -> bytes:
        with open(file_path, "rb") as f:
            f.seek(offset)
            chunk = f.read(length)
        if not chunk:
            raise IOError(f"Unexpected EOF while reading upload chunk for {file_path}")
        return chunk

    def list(self, get_type: int = 0, offset: int = 0, limit: int = 50, 
             order_by: int = 0, asc: bool = False, 
             dir_key: Optional[str] = None, pdir_key: Optional[str] = None) -> Dict[str, Any]:
        """
        weiyun.list - Query directory list.
        """
        args: Dict[str, Any] = {
            "get_type": get_type,
            "offset": offset,
            "limit": limit,
            "order_by": order_by,
            "asc": asc,
        }
        if dir_key is not None:
            args["dir_key"] = dir_key
        if pdir_key is not None:
            args["pdir_key"] = pdir_key
            
        return self._mcp_call("weiyun.list", args)

    def download(self, items: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        weiyun.download - Batch get download links.
        items: list of dict with 'file_id' and 'pdir_key'.
        """
        return self._mcp_call("weiyun.download", {"items": items})

    def delete(self, file_list: Optional[List[Dict[str, str]]] = None, 
               dir_list: Optional[List[Dict[str, str]]] = None, 
               delete_completely: bool = False) -> Dict[str, Any]:
        """
        weiyun.delete - Batch delete files or directories.
        """
        args: Dict[str, Any] = {
            "delete_completely": delete_completely
        }
        if file_list:
            args["file_list"] = file_list
        if dir_list:
            args["dir_list"] = dir_list
            
        if not file_list and not dir_list:
            raise ValueError("Either file_list or dir_list must be provided.")
            
        return self._mcp_call("weiyun.delete", args)

    def gen_share_link(self, file_list: Optional[List[Dict[str, str]]] = None, 
                       dir_list: Optional[List[Dict[str, str]]] = None, 
                       share_name: Optional[str] = None) -> Dict[str, Any]:
        """
        weiyun.gen_share_link - Generate share link.
        """
        args: Dict[str, Any] = {}
        if file_list:
            args["file_list"] = file_list
        if dir_list:
            args["dir_list"] = dir_list
        if share_name:
            args["share_name"] = share_name
            
        if not file_list and not dir_list:
            raise ValueError("Either file_list or dir_list must be provided.")
            
        return self._mcp_call("weiyun.gen_share_link", args)

    def check_skill_update(self, version: str) -> Dict[str, Any]:
        """
        check_skill_update - Check for MCP skill updates.
        """
        return self._mcp_call("check_skill_update", {"version": version})

    def upload(self, file_path: str, pdir_key: Optional[str] = None, max_rounds: Optional[int] = None,
               max_workers: int = 1,
               progress_callback: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        """
        weiyun.upload - Two-phase upload (Pre-upload + Chunk-upload).
        Returns a dict with upload result and timing statistics.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        upload_started_at = time.perf_counter()
        uploaded_bytes = 0
        estimated_rounds = max(1, (file_size + 524287) // 524288)
        effective_max_rounds = max_rounds or max(50, estimated_rounds * 3)
        worker_count = max(1, max_workers)
        hash_started_at = upload_started_at
        hash_elapsed_seconds = 0.0
        transfer_started_at: Optional[float] = None
        transfer_elapsed_seconds = 0.0
        retry_count = 0
        last_uploaded_end = 0

        def report_progress(event: str, **extra: Any) -> None:
            if not progress_callback:
                return
            payload: Dict[str, Any] = {
                "event": event,
                "filename": filename,
                "file_size": file_size,
                "uploaded_bytes": uploaded_bytes,
                "elapsed_seconds": time.perf_counter() - upload_started_at,
                "sha1_backend": get_sha1_backend_name(),
                "max_rounds": effective_max_rounds,
                "retry_count": retry_count,
                "max_workers": worker_count,
            }
            payload.update(extra)
            progress_callback(payload)

        def make_result(file_id: str, resolved_filename: str, *, fast_upload: bool = False) -> Dict[str, Any]:
            total_elapsed_seconds = time.perf_counter() - upload_started_at
            effective_transfer_elapsed = transfer_elapsed_seconds
            if transfer_started_at is not None and effective_transfer_elapsed == 0.0:
                effective_transfer_elapsed = time.perf_counter() - transfer_started_at
            average_speed = uploaded_bytes / effective_transfer_elapsed if effective_transfer_elapsed > 0 else 0.0
            return {
                "file_id": file_id,
                "filename": resolved_filename,
                "file_size": file_size,
                "uploaded_bytes": uploaded_bytes,
                "elapsed_seconds": total_elapsed_seconds,
                "hash_elapsed_seconds": hash_elapsed_seconds,
                "transfer_elapsed_seconds": effective_transfer_elapsed,
                "average_speed_bytes": average_speed,
                "fast_upload": fast_upload,
                "sha1_backend": get_sha1_backend_name(),
                "max_rounds": effective_max_rounds,
                "rounds_used": round_num,
                "retry_count": retry_count,
                "max_workers": worker_count,
            }

        # Phase 1: Calculate parameters
        report_progress("hashing")
        params = calc_upload_params(file_path)
        hash_elapsed_seconds = time.perf_counter() - hash_started_at
        report_progress("hashed")
        
        pre_upload_args = {
            "filename": params["filename"],
            "file_size": params["file_size"],
            "file_sha": params["file_sha"],
            "file_md5": params["file_md5"],
            "block_sha_list": params["block_sha_list"],
            "check_sha": params["check_sha"],
            "check_data": params["check_data"],
        }
        if pdir_key:
            pre_upload_args["pdir_key"] = pdir_key

        # Phase 2 & 3: Upload loop
        round_num = 0
        while round_num < effective_max_rounds:
            round_num += 1
            if transfer_started_at is None:
                transfer_started_at = time.perf_counter()
            pre_rsp = self._mcp_call("weiyun.upload", pre_upload_args)

            if pre_rsp.get("error"):
                raise RuntimeError(f"Upload error (pre-upload): {pre_rsp['error']}")

            if pre_rsp.get("file_exist", False):
                uploaded_bytes = file_size
                if transfer_started_at is not None:
                    transfer_elapsed_seconds = time.perf_counter() - transfer_started_at
                report_progress("completed", fast_upload=True, uploaded_bytes=file_size)
                return make_result(
                    pre_rsp.get("file_id", ""),
                    pre_rsp.get("filename", filename),
                    fast_upload=True,
                )

            ch_list = pre_rsp.get("channel_list", [])
            uk = pre_rsp.get("upload_key", "")
            ex = pre_rsp.get("ex", "")
            available_channels = self._collect_available_channels(ch_list)

            if not available_channels:
                state = int(pre_rsp.get("upload_state", 0))
                if state == 2:
                    uploaded_bytes = file_size
                    if transfer_started_at is not None:
                        transfer_elapsed_seconds = time.perf_counter() - transfer_started_at
                    report_progress("completed", fast_upload=False)
                    return make_result(
                        pre_rsp.get("file_id", ""),
                        pre_rsp.get("filename", filename),
                    )
                if state == 3:
                    report_progress("waiting", round_num=round_num, upload_state=state)
                    continue
                raise RuntimeError(f"No available channel, upload_state={state}")

            selected_channels = available_channels[:worker_count]
            for channel in selected_channels:
                if channel["offset"] < last_uploaded_end:
                    retry_count += 1
                report_progress(
                    "uploading",
                    round_num=round_num,
                    offset=channel["offset"],
                    chunk_size=channel["len"],
                    channel_id=channel["id"],
                )

            cl = [{"id": int(c["id"]), "offset": int(c["offset"]), "len": int(c["len"])}
                  for c in ch_list]
            upload_results: List[Tuple[Dict[str, int], Dict[str, Any], int]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected_channels)) as executor:
                future_map = {}
                for channel in selected_channels:
                    chunk = self._read_chunk(file_path, channel["offset"], channel["len"])
                    future = executor.submit(
                        self._upload_chunk_request,
                        filename,
                        file_size,
                        params["file_sha"],
                        params["check_sha"],
                        uk,
                        cl,
                        channel["id"],
                        ex,
                        chunk,
                    )
                    future_map[future] = (channel, len(chunk))

                for future in concurrent.futures.as_completed(future_map):
                    channel, actual_len = future_map[future]
                    up_rsp = future.result()
                    upload_results.append((channel, up_rsp, actual_len))

            completion_state = None
            completion_rsp = None
            for channel, up_rsp, actual_len in sorted(upload_results, key=lambda item: item[0]["offset"]):
                uploaded_bytes = min(file_size, max(uploaded_bytes, channel["offset"] + actual_len))
                last_uploaded_end = max(last_uploaded_end, channel["offset"] + actual_len)
                if transfer_started_at is not None:
                    transfer_elapsed_seconds = time.perf_counter() - transfer_started_at
                report_progress(
                    "uploaded",
                    round_num=round_num,
                    offset=channel["offset"],
                    chunk_size=actual_len,
                    channel_id=channel["id"],
                )

                if up_rsp.get("error"):
                    raise RuntimeError(f"Upload error (chunk): {up_rsp['error']}")

                state = int(up_rsp.get("upload_state", 0))
                if state == 2:
                    completion_state = state
                    completion_rsp = up_rsp

            if completion_state == 2 and completion_rsp is not None:
                uploaded_bytes = file_size
                if transfer_started_at is not None:
                    transfer_elapsed_seconds = time.perf_counter() - transfer_started_at
                report_progress("completed", fast_upload=False)
                return make_result(
                    completion_rsp.get("file_id") or pre_rsp.get("file_id", ""),
                    completion_rsp.get("filename") or pre_rsp.get("filename", filename),
                )
                
        estimated_required_rounds = max(1, (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE)
        raise RuntimeError(
            f"Exceeded maximum upload rounds ({effective_max_rounds}); "
            f"estimated minimum rounds for this file is about {estimated_required_rounds}"
        )
