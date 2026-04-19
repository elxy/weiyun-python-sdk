# 微云 Python SDK & CLI（非官方）

> **声明：本项目为非官方 SDK，与腾讯微云官方无关。**
> 本项目基于微云开放接口实现，仅供学习和参考使用。

参考接口文档：[https://www.weiyun.com/act/openclaw](https://www.weiyun.com/act/openclaw)

## 功能特性

本 SDK 和 CLI 支持 6 项核心操作：
- **列表**：浏览文件和目录。
- **下载**：获取文件下载链接。
- **上传**：将本地文件上传到微云。
- **删除**：删除文件或目录。
- **分享**：生成文件分享链接。
- **检查更新**：检查 MCP 技能版本更新。

## 安装

```bash
cd weiyun-python-sdk
pip install .
```

安装后即可在终端中直接使用 `weiyun` 命令。

也支持开发模式安装（修改代码后无需重新安装）：

```bash
pip install -e .
```

## 命令行工具使用

### 基本语法

```bash
weiyun [全局参数] <子命令> [子命令参数]
```

也可通过模块方式调用：

```bash
python -m weiyun_sdk [全局参数] <子命令> [子命令参数]
```

### 全局参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--token TOKEN` | MCP 认证令牌 | 环境变量 `WEIYUN_MCP_TOKEN` |
| `--mcp_url URL` | MCP 服务器地址 | `https://www.weiyun.com/api/v3/mcpserver` |
| `--env_id ID` | 环境 ID | 环境变量 `WEIYUN_ENV_ID` |

> **注意：** 全局参数必须写在子命令**之前**。除 `check_update` 外，其余命令均需要提供 `--token`。

### 支持的命令

* **列出文件：**
  ```bash
  weiyun --token YOUR_TOKEN list [--get_type 0] [--offset 0] [--limit 50] [--order_by 0] [--asc] [--dir_key KEY] [--pdir_key KEY]
  ```
  - `--get_type`：0-全部，1-仅目录，2-仅文件
  - `--offset` / `--limit`：分页偏移量和每页数量（最大 50）
  - `--order_by`：0-默认，1-按名称，2-按修改时间
  - `--asc`：升序排列
  - `--dir_key` / `--pdir_key`：目录键 / 父目录键

* **获取下载链接：**
  ```bash
  weiyun --token YOUR_TOKEN download <file_id> <pdir_key>
  ```

* **上传文件：**
  ```bash
  # 通过 pdir_key 指定目标目录
  weiyun --token YOUR_TOKEN upload /path/to/file --pdir_key YOUR_PDIR_KEY

  # 或通过语义路径指定目标目录
  weiyun --token YOUR_TOKEN upload /path/to/file --path /Documents/ProjectA

  # 启用多通道并行上传
  weiyun --token YOUR_TOKEN upload /path/to/file --pdir_key YOUR_PDIR_KEY --workers 4
  ```
  - `--pdir_key` 和 `--path` 二选一，不可同时使用
  - `--max_rounds`：最大上传轮次；默认按文件大小自动计算
  - `--workers`：每轮最多并行上传的通道数；默认 1，可按服务端返回的通道数提升到最多 4
  - CLI 会在 stderr 输出当前使用的 SHA1 后端、哈希和分片上传进度
  - 上传完成后会打印 `hash` 耗时、`transfer` 耗时与平均速度/重试次数，以及总耗时

* **删除文件/目录：**
  ```bash
  weiyun --token YOUR_TOKEN delete <file|dir> <id_or_key> <pdir_key> [--completely]
  ```
  - 第一个参数为类型：`file`（文件）或 `dir`（目录）
  - `--completely`：彻底删除（不放入回收站）

* **分享文件/目录：**
  ```bash
  weiyun --token YOUR_TOKEN share <file|dir> <id_or_key> <pdir_key> [--name SHARE_NAME]
  ```
  - 第一个参数为类型：`file`（文件）或 `dir`（目录）
  - `--name`：自定义分享名称

* **检查更新：**
  ```bash
  weiyun check_update <version>
  ```

## Python SDK 使用

你可以在自己的 Python 项目中直接使用 `WeiyunClient`。

```python
from weiyun_sdk import WeiyunClient

# 初始化客户端（token 为必传参数）
client = WeiyunClient(token="YOUR_TOKEN")

# 1. 列出目录中的文件
res = client.list(get_type=0, offset=0, limit=50, dir_key=None, pdir_key=None)
print("文件列表:", res)

# 2. 上传文件
res = client.upload(file_path="./local_file.txt", pdir_key="TARGET_PDIR_KEY")
print("上传结果:", res)

# 3. 获取下载链接
res = client.download(items=[{"file_id": "EXAMPLE_FILE_ID", "pdir_key": "EXAMPLE_PDIR_KEY"}])
print("下载链接:", res)

# 4. 生成分享链接
res = client.gen_share_link(
    file_list=[{"file_id": "EXAMPLE_FILE_ID", "pdir_key": "EXAMPLE_PDIR_KEY"}],
    share_name="我的分享"
)
print("分享链接:", res)

# 5. 检查技能更新
res = client.check_skill_update(version="1.0.0")
print("更新信息:", res)

# 6. 删除文件
res = client.delete(
    file_list=[{"file_id": "EXAMPLE_FILE_ID", "pdir_key": "EXAMPLE_PDIR_KEY"}],
    delete_completely=False
)
print("删除结果:", res)

# 删除目录
res = client.delete(
    dir_list=[{"dir_key": "EXAMPLE_DIR_KEY", "pdir_key": "EXAMPLE_PDIR_KEY"}]
)
print("删除结果:", res)
```

## 项目结构

- `weiyun_sdk/`：核心 Python 模块，包含 `WeiyunClient`、CLI 及 API 接口封装。
  - `client.py`：SDK 客户端。
  - `cli.py`：命令行工具入口。
  - `upload.py`：上传参数计算。
  - `__main__.py`：支持 `python -m weiyun_sdk` 调用。
- `pyproject.toml`：项目配置与依赖声明。

## 性能基准

仓库内提供了一个本地基准脚本，用来量化上传热点路径的 CPU 和内存开销：

```bash
.venv/bin/python3 scripts/benchmark_upload.py --size-mib 128 --runs 3 --warmups 1
```

也可以直接对真实文件做测试：

```bash
.venv/bin/python3 scripts/benchmark_upload.py /path/to/file --runs 5
```

脚本会分别对比四个路径：

- `hash_legacy`：优化前的 `calc_upload_params`
- `hash_current`：当前版本的 `calc_upload_params`
- `chunk_legacy`：优化前整文件读入后再切片 Base64
- `chunk_current`：当前按需读取 chunk 再 Base64

输出会包含平均 wall time、平均 CPU time、峰值 RSS，以及相对提速比。

当前实现会优先尝试通过系统 `libcrypto` 调用 OpenSSL 的 SHA1；如果环境不可用，再回退到纯 Python 实现。
