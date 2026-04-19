import ctypes
import ctypes.util
import threading


SHA1_DIGEST_LENGTH = 20


class SHA1State(ctypes.Structure):
    _fields_ = [
        ("h0", ctypes.c_uint32),
        ("h1", ctypes.c_uint32),
        ("h2", ctypes.c_uint32),
        ("h3", ctypes.c_uint32),
        ("h4", ctypes.c_uint32),
        ("Nl", ctypes.c_uint32),
        ("Nh", ctypes.c_uint32),
        ("data", ctypes.c_uint32 * 16),
        ("num", ctypes.c_uint32),
    ]


_thread_local = threading.local()


def _load_libcrypto():
    cached = getattr(_thread_local, "libcrypto", None)
    if cached is not None:
        return cached

    path = ctypes.util.find_library("crypto")
    if not path:
        return None

    try:
        lib = ctypes.CDLL(path)
    except OSError:
        return None

    lib.SHA1_Init.argtypes = [ctypes.POINTER(SHA1State)]
    lib.SHA1_Init.restype = ctypes.c_int
    lib.SHA1_Update.argtypes = [ctypes.POINTER(SHA1State), ctypes.c_void_p, ctypes.c_size_t]
    lib.SHA1_Update.restype = ctypes.c_int
    lib.SHA1_Final.argtypes = [ctypes.c_void_p, ctypes.POINTER(SHA1State)]
    lib.SHA1_Final.restype = ctypes.c_int

    _thread_local.libcrypto = lib
    return lib


def is_available():
    return _load_libcrypto() is not None


class OpenSSLSHA1:
    """SHA1 wrapper over libcrypto that exposes the internal register state."""

    def __init__(self):
        self._lib = _load_libcrypto()
        if self._lib is None:
            raise RuntimeError("OpenSSL libcrypto is not available")

        self._state = SHA1State()
        if self._lib.SHA1_Init(ctypes.byref(self._state)) != 1:
            raise RuntimeError("SHA1_Init failed")
        self._message_byte_length = 0

    def update(self, data):
        if not data:
            return

        mv = memoryview(data)
        self._message_byte_length += len(mv)
        if mv.readonly:
            buf = ctypes.create_string_buffer(mv.tobytes())
            ptr = ctypes.cast(buf, ctypes.c_void_p)
        else:
            ptr = ctypes.c_void_p(ctypes.addressof(ctypes.c_char.from_buffer(mv)))

        if self._lib.SHA1_Update(ctypes.byref(self._state), ptr, len(mv)) != 1:
            raise RuntimeError("SHA1_Update failed")

    def get_state(self):
        if self._message_byte_length % 64 != 0:
            raise AssertionError(
                "get_state requires the processed length to align to 64-byte SHA1 blocks"
            )

        words = (self._state.h0, self._state.h1, self._state.h2, self._state.h3, self._state.h4)
        return b"".join(word.to_bytes(4, "little") for word in words).hex()

    def hexdigest(self):
        tmp = SHA1State()
        ctypes.pointer(tmp)[0] = self._state
        digest = (ctypes.c_ubyte * SHA1_DIGEST_LENGTH)()
        if self._lib.SHA1_Final(digest, ctypes.byref(tmp)) != 1:
            raise RuntimeError("SHA1_Final failed")
        return bytes(digest).hex()
