#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import resource
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weiyun_sdk.upload import BLOCK_SIZE, calc_upload_params
from weiyun_sdk.openssl_sha1 import OpenSSLSHA1, is_available as openssl_sha1_available


def _left_rotate(n, b):
    return ((n << b) | (n >> (32 - b))) & 0xFFFFFFFF


class LegacySHA1:
    """Reference implementation matching the pre-optimization code path."""

    def __init__(self):
        self.h0 = 0x67452301
        self.h1 = 0xEFCDAB89
        self.h2 = 0x98BADCFE
        self.h3 = 0x10325476
        self.h4 = 0xC3D2E1F0
        self._message_byte_length = 0
        self._unprocessed = b""

    def update(self, data):
        self._unprocessed += data
        self._message_byte_length += len(data)
        while len(self._unprocessed) >= 64:
            self._process_chunk(self._unprocessed[:64])
            self._unprocessed = self._unprocessed[64:]

    def _process_chunk(self, chunk):
        assert len(chunk) == 64
        w = [0] * 80
        for i in range(16):
            w[i] = struct.unpack(">I", chunk[i * 4:(i + 1) * 4])[0]
        for i in range(16, 80):
            w[i] = _left_rotate(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1)
        a, b, c, d, e = self.h0, self.h1, self.h2, self.h3, self.h4
        for i in range(80):
            if 0 <= i <= 19:
                f = (b & c) | ((~b) & d)
                k = 0x5A827999
            elif 20 <= i <= 39:
                f = b ^ c ^ d
                k = 0x6ED9EBA1
            elif 40 <= i <= 59:
                f = (b & c) | (b & d) | (c & d)
                k = 0x8F1BBCDC
            else:
                f = b ^ c ^ d
                k = 0xCA62C1D6
            temp = (_left_rotate(a, 5) + f + e + k + w[i]) & 0xFFFFFFFF
            e = d
            d = c
            c = _left_rotate(b, 30)
            b = a
            a = temp
        self.h0 = (self.h0 + a) & 0xFFFFFFFF
        self.h1 = (self.h1 + b) & 0xFFFFFFFF
        self.h2 = (self.h2 + c) & 0xFFFFFFFF
        self.h3 = (self.h3 + d) & 0xFFFFFFFF
        self.h4 = (self.h4 + e) & 0xFFFFFFFF

    def get_state(self):
        if self._unprocessed:
            raise AssertionError("legacy get_state requires empty buffer")
        result = b""
        for h in (self.h0, self.h1, self.h2, self.h3, self.h4):
            result += struct.pack("<I", h)
        return result.hex()

    def hexdigest(self):
        message_byte_length = self._message_byte_length
        unprocessed = self._unprocessed
        h0, h1, h2, h3, h4 = self.h0, self.h1, self.h2, self.h3, self.h4
        unprocessed += b"\x80"
        unprocessed += b"\x00" * ((56 - len(unprocessed) % 64) % 64)
        unprocessed += struct.pack(">Q", message_byte_length * 8)
        tmp = LegacySHA1.__new__(LegacySHA1)
        tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4 = h0, h1, h2, h3, h4
        tmp._unprocessed = b""
        tmp._message_byte_length = message_byte_length
        while len(unprocessed) >= 64:
            tmp._process_chunk(unprocessed[:64])
            unprocessed = unprocessed[64:]
        return "{:08x}{:08x}{:08x}{:08x}{:08x}".format(
            tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4
        )


def legacy_calc_upload_params(file_path):
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    last_block_size = file_size % BLOCK_SIZE
    if last_block_size == 0:
        last_block_size = BLOCK_SIZE
    check_block_size = last_block_size % 128
    if check_block_size == 0:
        check_block_size = 128
    before_block_size = file_size - last_block_size

    block_sha_list = []
    sha1 = LegacySHA1()
    md5 = hashlib.md5()

    with open(file_path, "rb") as f:
        for _offset in range(0, before_block_size, BLOCK_SIZE):
            data = f.read(BLOCK_SIZE)
            sha1.update(data)
            md5.update(data)
            block_sha_list.append(sha1.get_state())

        between_data = f.read(last_block_size - check_block_size)
        sha1.update(between_data)
        md5.update(between_data)
        check_sha = sha1.get_state()

        check_data_bytes = f.read(check_block_size)
        sha1.update(check_data_bytes)
        md5.update(check_data_bytes)
        file_sha = sha1.hexdigest()
        check_data = base64.b64encode(check_data_bytes).decode("utf-8")

        block_sha_list.append(file_sha)

    return {
        "filename": filename,
        "file_size": file_size,
        "file_sha": file_sha,
        "file_md5": md5.hexdigest(),
        "block_sha_list": block_sha_list,
        "check_sha": check_sha,
        "check_data": check_data,
    }


def openssl_calc_upload_params(file_path):
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    last_block_size = file_size % BLOCK_SIZE
    if last_block_size == 0:
        last_block_size = BLOCK_SIZE
    check_block_size = last_block_size % 128
    if check_block_size == 0:
        check_block_size = 128
    before_block_size = file_size - last_block_size

    block_sha_list = []
    sha1 = OpenSSLSHA1()
    md5 = hashlib.md5()

    with open(file_path, "rb") as f:
        while f.tell() < before_block_size:
            data = f.read(BLOCK_SIZE)
            if not data:
                raise IOError(f"Unexpected EOF while hashing {file_path}")
            sha1.update(data)
            md5.update(data)
            block_sha_list.append(sha1.get_state())

        between_data = f.read(last_block_size - check_block_size)
        if len(between_data) != last_block_size - check_block_size:
            raise IOError(f"Unexpected EOF while hashing {file_path}")
        sha1.update(between_data)
        md5.update(between_data)
        check_sha = sha1.get_state()

        check_data_bytes = f.read(check_block_size)
        if len(check_data_bytes) != check_block_size:
            raise IOError(f"Unexpected EOF while hashing {file_path}")
        sha1.update(check_data_bytes)
        md5.update(check_data_bytes)
        file_sha = sha1.hexdigest()
        check_data = base64.b64encode(check_data_bytes).decode("utf-8")

        block_sha_list.append(file_sha)

    return {
        "filename": filename,
        "file_size": file_size,
        "file_sha": file_sha,
        "file_md5": md5.hexdigest(),
        "block_sha_list": block_sha_list,
        "check_sha": check_sha,
        "check_data": check_data,
    }


def iter_chunks(file_size, chunk_size):
    for offset in range(0, file_size, chunk_size):
        yield offset, min(chunk_size, file_size - offset)


def legacy_prepare_chunks(file_path, chunk_size):
    with open(file_path, "rb") as f:
        file_data = f.read()

    encoded_bytes = 0
    for offset, length in iter_chunks(len(file_data), chunk_size):
        encoded_bytes += len(base64.b64encode(file_data[offset:offset + length]))
    return encoded_bytes


def current_prepare_chunks(file_path, chunk_size):
    file_size = os.path.getsize(file_path)
    encoded_bytes = 0
    with open(file_path, "rb") as f:
        for offset, length in iter_chunks(file_size, chunk_size):
            f.seek(offset)
            chunk = f.read(length)
            encoded_bytes += len(base64.b64encode(chunk))
    return encoded_bytes


BENCHMARKS = {
    "hash_legacy": lambda path, chunk_size: legacy_calc_upload_params(path)["file_sha"],
    "hash_openssl": lambda path, chunk_size: openssl_calc_upload_params(path)["file_sha"],
    "hash_current": lambda path, chunk_size: calc_upload_params(path)["file_sha"],
    "chunk_legacy": legacy_prepare_chunks,
    "chunk_current": current_prepare_chunks,
}


def make_test_file(path, size_bytes):
    seed = hashlib.sha256(b"weiyun-benchmark-seed").digest()
    block = (seed * (1024 * 1024 // len(seed) + 1))[: 1024 * 1024]
    remaining = size_bytes
    with open(path, "wb") as f:
        while remaining > 0:
            piece = block[: min(len(block), remaining)]
            f.write(piece)
            remaining -= len(piece)


def run_worker(bench_name, file_path, chunk_size):
    func = BENCHMARKS[bench_name]
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    result = func(file_path, chunk_size)
    wall_s = time.perf_counter() - start_wall
    cpu_s = time.process_time() - start_cpu
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({
        "bench": bench_name,
        "wall_s": wall_s,
        "cpu_s": cpu_s,
        "rss_kb": rss_kb,
        "result": str(result),
    }))


def run_subprocess(script_path, bench_name, file_path, chunk_size):
    cmd = [
        sys.executable,
        script_path,
        "--worker",
        bench_name,
        str(file_path),
        "--chunk-size",
        str(chunk_size),
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def summarize(values, key):
    series = [item[key] for item in values]
    return {
        "mean": statistics.mean(series),
        "median": statistics.median(series),
        "min": min(series),
        "max": max(series),
    }


def format_seconds(value):
    return f"{value:.3f}s"


def format_mib_from_kb(value):
    return f"{value / 1024:.1f} MiB"


def print_summary(results):
    print()
    print("Benchmark summary")
    print("name           mean wall   mean cpu    peak rss")
    ordered = ["hash_legacy"]
    if "hash_openssl" in results:
        ordered.append("hash_openssl")
    ordered.extend(["hash_current", "chunk_legacy", "chunk_current"])
    for name in ordered:
        metrics = results[name]
        print(
            f"{name:<14}"
            f"{format_seconds(metrics['wall']['mean']):>10} "
            f"{format_seconds(metrics['cpu']['mean']):>10} "
            f"{format_mib_from_kb(metrics['rss']['max']):>10}"
        )

    hash_speedup = results["hash_legacy"]["wall"]["mean"] / results["hash_current"]["wall"]["mean"]
    chunk_speedup = results["chunk_legacy"]["wall"]["mean"] / results["chunk_current"]["wall"]["mean"]
    rss_saved_kb = results["chunk_legacy"]["rss"]["max"] - results["chunk_current"]["rss"]["max"]

    print()
    print(
        "calc_upload_params speedup: "
        f"{hash_speedup:.2f}x by wall, "
        f"{results['hash_legacy']['cpu']['mean'] / results['hash_current']['cpu']['mean']:.2f}x by CPU"
    )
    if "hash_openssl" in results:
        print(
            "OpenSSL SHA1 speedup: "
            f"{results['hash_legacy']['wall']['mean'] / results['hash_openssl']['wall']['mean']:.2f}x by wall, "
            f"{results['hash_legacy']['cpu']['mean'] / results['hash_openssl']['cpu']['mean']:.2f}x by CPU"
        )
    print(
        "chunk preparation delta: "
        f"{chunk_speedup:.2f}x by wall, "
        f"peak RSS reduced by {format_mib_from_kb(rss_saved_kb)}"
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark weiyun upload hot paths.")
    parser.add_argument("file", nargs="?", help="Existing file to benchmark")
    parser.add_argument("--size-mib", type=int, default=128, help="Generate a temp file of this size when file is omitted")
    parser.add_argument("--runs", type=int, default=3, help="Measured runs per benchmark")
    parser.add_argument("--warmups", type=int, default=1, help="Warmup runs per benchmark")
    parser.add_argument("--chunk-size", type=int, default=BLOCK_SIZE, help="Chunk size for chunk preparation benchmark")
    parser.add_argument("--keep-file", action="store_true", help="Keep the generated temp file")
    parser.add_argument("--worker", choices=sorted(BENCHMARKS), help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    args = parse_args()

    if args.worker:
        if not args.file:
            raise SystemExit("worker mode requires a file path")
        run_worker(args.worker, args.file, args.chunk_size)
        return

    script_path = str(Path(__file__).resolve())
    generated = False
    file_path = args.file
    if not file_path:
        fd, file_path = tempfile.mkstemp(prefix="weiyun-bench-", suffix=".bin")
        os.close(fd)
        make_test_file(file_path, args.size_mib * 1024 * 1024)
        generated = True

    try:
        file_path = str(Path(file_path).resolve())
        size_bytes = os.path.getsize(file_path)
        print(f"Benchmark file: {file_path}")
        print(f"File size: {size_bytes / 1024 / 1024:.1f} MiB")
        print(f"Runs: {args.runs}, warmups: {args.warmups}, chunk size: {args.chunk_size}")

        legacy_params = legacy_calc_upload_params(file_path)
        current_params = calc_upload_params(file_path)
        if legacy_params != current_params:
            raise RuntimeError("hash benchmark sanity check failed: legacy/current params differ")
        if openssl_sha1_available():
            openssl_params = openssl_calc_upload_params(file_path)
            if legacy_params != openssl_params:
                raise RuntimeError("hash benchmark sanity check failed: legacy/openssl params differ")

        legacy_chunks = legacy_prepare_chunks(file_path, args.chunk_size)
        current_chunks = current_prepare_chunks(file_path, args.chunk_size)
        if legacy_chunks != current_chunks:
            raise RuntimeError("chunk benchmark sanity check failed: legacy/current totals differ")

        print("Sanity checks: OK")

        results = {}
        bench_names = ["hash_legacy", "hash_current", "chunk_legacy", "chunk_current"]
        if openssl_sha1_available():
            bench_names.insert(1, "hash_openssl")

        for bench_name in bench_names:
            samples = []
            for run_idx in range(args.warmups + args.runs):
                data = run_subprocess(script_path, bench_name, file_path, args.chunk_size)
                if run_idx >= args.warmups:
                    samples.append(data)
            results[bench_name] = {
                "wall": summarize(samples, "wall_s"),
                "cpu": summarize(samples, "cpu_s"),
                "rss": summarize(samples, "rss_kb"),
            }

        print_summary(results)
    finally:
        if generated and not args.keep_file and os.path.exists(file_path):
            os.remove(file_path)


if __name__ == "__main__":
    main()
