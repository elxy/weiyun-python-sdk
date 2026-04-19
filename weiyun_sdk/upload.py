import base64
import hashlib
import os
import struct

from .openssl_sha1 import OpenSSLSHA1, is_available as openssl_sha1_available

BLOCK_SIZE = 524288  # 512KB

def _left_rotate(n, b):
    """32-bit left rotation"""
    return ((n << b) | (n >> (32 - b))) & 0xFFFFFFFF


class SHA1:
    """Pure Python SHA1 implementation supporting internal state extraction (no finalization)."""

    def __init__(self):
        # SHA1 initial h0-h4
        self.h0 = 0x67452301
        self.h1 = 0xEFCDAB89
        self.h2 = 0x98BADCFE
        self.h3 = 0x10325476
        self.h4 = 0xC3D2E1F0
        self._message_byte_length = 0
        self._unprocessed = b""

    def update(self, data):
        """Append data to the SHA1 object"""
        if not data:
            return

        self._message_byte_length += len(data)

        if self._unprocessed:
            needed = 64 - len(self._unprocessed)
            if len(data) < needed:
                self._unprocessed += data
                return

            chunk = self._unprocessed + data[:needed]
            self._process_chunk(chunk)
            self._unprocessed = b""
            data = data[needed:]

        full_len = len(data) - (len(data) % 64)
        view = memoryview(data)
        for offset in range(0, full_len, 64):
            self._process_chunk(view[offset:offset + 64])

        if full_len < len(data):
            self._unprocessed = bytes(view[full_len:])

    def _process_chunk(self, chunk):
        """Process a 64-byte SHA1 block"""
        assert len(chunk) == 64
        w = list(struct.unpack(">16I", chunk)) + [0] * 64
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
            elif 60 <= i <= 79:
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
        """
        Get SHA1 internal state (h0-h4), little-endian 20 bytes hex string.
        Does not perform padding/finalization.
        """
        assert len(self._unprocessed) == 0, \
            f"get_state requires empty unprocessed buffer, currently {len(self._unprocessed)} bytes unprocessed"
        result = b""
        for h in (self.h0, self.h1, self.h2, self.h3, self.h4):
            result += struct.pack("<I", h)  # Little endian
        return result.hex()

    def hexdigest(self):
        """Return standard SHA1 digest (big endian, consistent with hashlib.sha1)"""
        message_byte_length = self._message_byte_length
        unprocessed = self._unprocessed
        h0, h1, h2, h3, h4 = self.h0, self.h1, self.h2, self.h3, self.h4
        # padding
        unprocessed += b"\x80"
        unprocessed += b"\x00" * ((56 - len(unprocessed) % 64) % 64)
        unprocessed += struct.pack(">Q", message_byte_length * 8)
        # Temp object to process remaining chunks
        tmp = SHA1.__new__(SHA1)
        tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4 = h0, h1, h2, h3, h4
        tmp._unprocessed = b""
        tmp._message_byte_length = message_byte_length
        while len(unprocessed) >= 64:
            tmp._process_chunk(unprocessed[:64])
            unprocessed = unprocessed[64:]
        return "{:08x}{:08x}{:08x}{:08x}{:08x}".format(
            tmp.h0, tmp.h1, tmp.h2, tmp.h3, tmp.h4)


def create_sha1():
    if openssl_sha1_available():
        return OpenSSLSHA1()
    return SHA1()


def get_sha1_backend_name() -> str:
    if openssl_sha1_available():
        return "OpenSSL libcrypto"
    return "Pure Python"


def calc_upload_params(file_path):
    """
    Calculate Weiyun upload parameters:
    - block_sha_list
    - file_sha
    - file_md5
    - check_sha
    - check_data
    """
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
    sha1 = create_sha1()
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
        
    file_md5 = md5.hexdigest()
    return {
        "filename": filename,
        "file_size": file_size,
        "file_sha": file_sha,
        "file_md5": file_md5,
        "block_sha_list": block_sha_list,
        "check_sha": check_sha,
        "check_data": check_data,
    }
