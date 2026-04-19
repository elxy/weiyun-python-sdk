import base64
import json
import os
import requests
from typing import Dict, Any, List, Optional

from .upload import calc_upload_params


class WeiyunClient:
    """
    Weiyun Python SDK Client, wrapping the 6 MCP tools via HTTP JSON-RPC.
    """

    def __init__(self, token: str, mcp_url: str = "https://www.weiyun.com/api/v3/mcpserver", env_id: Optional[str] = None):
        self.mcp_url = mcp_url
        self.token = token
        self.env_id = env_id
        self._request_id = 0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "WyHeader": f"mcp_token={self.token}",
        }
        if self.env_id:
            headers["Cookie"] = f"env_id={self.env_id}"
        return headers

    def _mcp_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
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
            
        resp = requests.post(self.mcp_url, headers=headers, json=payload, timeout=120)
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

    def upload(self, file_path: str, pdir_key: Optional[str] = None, max_rounds: int = 50) -> Dict[str, str]:
        """
        weiyun.upload - Two-phase upload (Pre-upload + Chunk-upload).
        Returns a dict with {"file_id": "...", "filename": "..."}
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)

        # Phase 1: Calculate parameters
        params = calc_upload_params(file_path)
        
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

        with open(file_path, "rb") as f:
            file_data = f.read()

        # Phase 2 & 3: Upload loop
        round_num = 0
        while round_num < max_rounds:
            round_num += 1
            pre_rsp = self._mcp_call("weiyun.upload", pre_upload_args)
            
            if pre_rsp.get("error"):
                raise RuntimeError(f"Upload error (pre-upload): {pre_rsp['error']}")
                
            # Fast upload (file_exist == true)
            if pre_rsp.get("file_exist", False):
                return {
                    "file_id": pre_rsp.get("file_id", ""),
                    "filename": pre_rsp.get("filename", filename)
                }
                
            ch_list = pre_rsp.get("channel_list", [])
            uk = pre_rsp.get("upload_key", "")
            ex = pre_rsp.get("ex", "")
            
            ch = None
            for c in ch_list:
                if int(c.get("len", 0)) > 0:
                    ch = c
                    break
                    
            if ch is None:
                state = int(pre_rsp.get("upload_state", 0))
                if state == 2:
                    return {
                        "file_id": pre_rsp.get("file_id", ""),
                        "filename": pre_rsp.get("filename", filename)
                    }
                raise RuntimeError(f"No available channel, upload_state={state}")
                
            offset = int(ch["offset"])
            length = int(ch["len"])
            channel_id = int(ch["id"])
            actual_len = min(length, len(file_data) - offset)
            
            chunk = file_data[offset:offset + actual_len]
            chunk_b64 = base64.b64encode(chunk).decode("utf-8")
            
            cl = [{"id": int(c["id"]), "offset": int(c["offset"]), "len": int(c["len"])} 
                  for c in ch_list]
                  
            up_rsp = self._mcp_call("weiyun.upload", {
                "filename": filename,
                "file_size": file_size,
                "file_sha": params["file_sha"],
                "block_sha_list": [],
                "check_sha": params["check_sha"],
                "upload_key": uk,
                "channel_list": cl,
                "channel_id": channel_id,
                "ex": ex,
                "file_data": chunk_b64,
            })
            
            if up_rsp.get("error"):
                raise RuntimeError(f"Upload error (chunk): {up_rsp['error']}")
                
            state = int(up_rsp.get("upload_state", 0))
            if state == 2:
                return {
                    "file_id": up_rsp.get("file_id", ""),
                    "filename": up_rsp.get("filename", filename)
                }
                
        raise RuntimeError(f"Exceeded maximum upload rounds ({max_rounds})")
