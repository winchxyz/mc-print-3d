"""Minimal, dependency-free NBT reader/writer.

Supports Java edition (big-endian) and Bedrock edition (little-endian, non-network) NBT,
with automatic gzip/zlib/raw detection.  Arrays are returned as numpy arrays for speed.

Typed scalar wrappers (:class:`Byte`, :class:`Short`, ...) subclass ``int``/``float`` so
values can be used directly while still round-tripping through :func:`dumps`.
"""
from __future__ import annotations

import gzip
import struct
import zlib
from typing import Any, Iterator, Optional, Union

import numpy as np

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG, TAG_FLOAT, TAG_DOUBLE = 0, 1, 2, 3, 4, 5, 6
TAG_BYTE_ARRAY, TAG_STRING, TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = 7, 8, 9, 10, 11, 12

TAG_NAMES = {
    0: "End", 1: "Byte", 2: "Short", 3: "Int", 4: "Long", 5: "Float", 6: "Double",
    7: "ByteArray", 8: "String", 9: "List", 10: "Compound", 11: "IntArray", 12: "LongArray",
}


class NBTError(ValueError):
    pass


# --------------------------------------------------------------------------------------
# Typed values
# --------------------------------------------------------------------------------------
class Byte(int):
    tag_id = TAG_BYTE


class Short(int):
    tag_id = TAG_SHORT


class Int(int):
    tag_id = TAG_INT


class Long(int):
    tag_id = TAG_LONG


class Float(float):
    tag_id = TAG_FLOAT


class Double(float):
    tag_id = TAG_DOUBLE


class String(str):
    tag_id = TAG_STRING


class ByteArray(np.ndarray):
    tag_id = TAG_BYTE_ARRAY


class IntArray(np.ndarray):
    tag_id = TAG_INT_ARRAY


class LongArray(np.ndarray):
    tag_id = TAG_LONG_ARRAY


class List(list):
    """NBT list; ``tag_type`` is the element tag id."""

    tag_id = TAG_LIST

    def __init__(self, items=(), tag_type: int = TAG_END):
        super().__init__(items)
        self.tag_type = tag_type


class Compound(dict):
    """NBT compound with typed convenience getters."""

    tag_id = TAG_COMPOUND

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def get_str(self, key: str, default: str = "") -> str:
        v = self.get(key)
        return str(v) if v is not None else default

    def get_compound(self, key: str) -> "Compound":
        v = self.get(key)
        return v if isinstance(v, Compound) else Compound()

    def get_list(self, key: str) -> "List":
        v = self.get(key)
        return v if isinstance(v, List) else List()

    def get_array(self, key: str) -> Optional[np.ndarray]:
        v = self.get(key)
        return v if isinstance(v, np.ndarray) else None

    def find(self, *keys: str, default: Any = None) -> Any:
        """Return the first present key among ``keys`` (case-sensitive first, then case-insensitive)."""
        for k in keys:
            if k in self:
                return self[k]
        lower = {k.lower(): k for k in self.keys()}
        for k in keys:
            if k.lower() in lower:
                return self[lower[k.lower()]]
        return default


# --------------------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------------------
class _Reader:
    __slots__ = ("data", "pos", "le", "_s_byte", "_s_short", "_s_int", "_s_long", "_s_float", "_s_double", "_s_ushort")

    def __init__(self, data: bytes, little_endian: bool):
        self.data = data
        self.pos = 0
        self.le = little_endian
        e = "<" if little_endian else ">"
        self._s_byte = struct.Struct(e + "b")
        self._s_short = struct.Struct(e + "h")
        self._s_ushort = struct.Struct(e + "H")
        self._s_int = struct.Struct(e + "i")
        self._s_long = struct.Struct(e + "q")
        self._s_float = struct.Struct(e + "f")
        self._s_double = struct.Struct(e + "d")

    def _unpack(self, s: struct.Struct):
        try:
            v = s.unpack_from(self.data, self.pos)[0]
        except struct.error as exc:
            raise NBTError(f"truncated NBT data at offset {self.pos}") from exc
        self.pos += s.size
        return v

    def read_byte(self) -> int:
        return self._unpack(self._s_byte)

    def read_ubyte(self) -> int:
        if self.pos >= len(self.data):
            raise NBTError("truncated NBT data")
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_string(self) -> str:
        n = self._unpack(self._s_ushort)
        raw = self.data[self.pos:self.pos + n]
        if len(raw) < n:
            raise NBTError("truncated NBT string")
        self.pos += n
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            # Java "modified UTF-8" can encode NUL as C0 80 and use CESU-8 surrogates.
            return _decode_modified_utf8(raw)

    def read_array(self, dtype: np.dtype, cls) -> np.ndarray:
        n = self._unpack(self._s_int)
        if n < 0:
            raise NBTError("negative array length")
        nbytes = n * dtype.itemsize
        raw = self.data[self.pos:self.pos + nbytes]
        if len(raw) < nbytes:
            raise NBTError("truncated NBT array")
        self.pos += nbytes
        arr = np.frombuffer(raw, dtype=dtype).copy()
        # normalise to native byte order for downstream code
        arr = arr.astype(dtype.newbyteorder("="), copy=False)
        return arr.view(cls)

    def read_payload(self, tag: int) -> Any:
        if tag == TAG_BYTE:
            return Byte(self._unpack(self._s_byte))
        if tag == TAG_SHORT:
            return Short(self._unpack(self._s_short))
        if tag == TAG_INT:
            return Int(self._unpack(self._s_int))
        if tag == TAG_LONG:
            return Long(self._unpack(self._s_long))
        if tag == TAG_FLOAT:
            return Float(self._unpack(self._s_float))
        if tag == TAG_DOUBLE:
            return Double(self._unpack(self._s_double))
        if tag == TAG_BYTE_ARRAY:
            return self.read_array(np.dtype("int8"), ByteArray)
        if tag == TAG_STRING:
            return String(self.read_string())
        if tag == TAG_LIST:
            et = self.read_ubyte()
            n = self._unpack(self._s_int)
            if n < 0:
                raise NBTError("negative list length")
            if et == TAG_END and n > 0:
                raise NBTError("list of TAG_End with elements")
            lst = List(tag_type=et)
            append = lst.append
            for _ in range(n):
                append(self.read_payload(et))
            return lst
        if tag == TAG_COMPOUND:
            comp = Compound()
            while True:
                t = self.read_ubyte()
                if t == TAG_END:
                    return comp
                name = self.read_string()
                comp[name] = self.read_payload(t)
        if tag == TAG_INT_ARRAY:
            return self.read_array(np.dtype("<i4" if self.le else ">i4"), IntArray)
        if tag == TAG_LONG_ARRAY:
            return self.read_array(np.dtype("<i8" if self.le else ">i8"), LongArray)
        raise NBTError(f"unknown tag id {tag} at offset {self.pos}")


def _decode_modified_utf8(raw: bytes) -> str:
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == 0xC0 and i + 1 < n and raw[i + 1] == 0x80:
            out.append(0)
            i += 2
        else:
            out.append(b)
            i += 1
    return out.decode("utf-8", errors="replace")


def detect_compression(data: bytes) -> str:
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return "gzip"
    if len(data) >= 2 and data[0] == 0x78 and data[1] in (0x01, 0x5E, 0x9C, 0xDA):
        return "zlib"
    return "none"


def decompress(data: bytes) -> bytes:
    kind = detect_compression(data)
    if kind == "gzip":
        return gzip.decompress(data)
    if kind == "zlib":
        return zlib.decompress(data)
    return data


def loads(data: bytes, little_endian: Optional[bool] = None) -> tuple[str, Compound]:
    """Parse NBT bytes (compressed or not).  Returns (root name, root compound).

    ``little_endian=None`` autodetects: Java files start with 0x0A 0x00 0x00 (root name length 0)
    or 0x0A followed by a short name; Bedrock files have the same tag byte but the name length is
    little-endian.  We try big-endian first and fall back to little-endian on failure.
    """
    raw = decompress(data)
    if not raw:
        raise NBTError("empty NBT data")
    if little_endian is None:
        # Heuristic: Bedrock .mcstructure files are uncompressed and start with TAG_Compound
        # followed by an LE short name length.
        try:
            return _parse_root(raw, False)
        except NBTError:
            return _parse_root(raw, True)
    return _parse_root(raw, little_endian)


def _parse_root(raw: bytes, little_endian: bool) -> tuple[str, Compound]:
    r = _Reader(raw, little_endian)
    tag = r.read_ubyte()
    if tag != TAG_COMPOUND:
        raise NBTError(f"root tag is {TAG_NAMES.get(tag, tag)}, expected Compound")
    name = r.read_string()
    root = r.read_payload(TAG_COMPOUND)
    return name, root


def load(path: str, little_endian: Optional[bool] = None) -> tuple[str, Compound]:
    with open(path, "rb") as fh:
        return loads(fh.read(), little_endian)


# --------------------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------------------
def _tag_of(value: Any) -> int:
    tid = getattr(value, "tag_id", None)
    if tid is not None:
        return tid
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, np.ndarray):
        if value.dtype == np.int8 or value.dtype == np.uint8:
            return TAG_BYTE_ARRAY
        if value.dtype == np.int32:
            return TAG_INT_ARRAY
        if value.dtype == np.int64:
            return TAG_LONG_ARRAY
        raise NBTError(f"unsupported array dtype {value.dtype}")
    if isinstance(value, (list, tuple)):
        return TAG_LIST
    raise NBTError(f"cannot serialise {type(value)}")


class _Writer:
    def __init__(self, little_endian: bool):
        self.le = little_endian
        self.e = "<" if little_endian else ">"
        self.out = bytearray()

    def w(self, fmt: str, *vals):
        self.out += struct.pack(self.e + fmt, *vals)

    def write_string(self, s: str):
        b = s.encode("utf-8")
        self.w("H", len(b))
        self.out += b

    def write_payload(self, tag: int, value: Any):
        if tag == TAG_BYTE:
            self.w("b", int(value))
        elif tag == TAG_SHORT:
            self.w("h", int(value))
        elif tag == TAG_INT:
            self.w("i", int(value))
        elif tag == TAG_LONG:
            self.w("q", int(value))
        elif tag == TAG_FLOAT:
            self.w("f", float(value))
        elif tag == TAG_DOUBLE:
            self.w("d", float(value))
        elif tag == TAG_STRING:
            self.write_string(str(value))
        elif tag in (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY):
            dt = {TAG_BYTE_ARRAY: "i1", TAG_INT_ARRAY: "i4", TAG_LONG_ARRAY: "i8"}[tag]
            arr = np.asarray(value).astype(np.dtype(self.e + dt), copy=False)
            self.w("i", arr.size)
            self.out += arr.tobytes()
        elif tag == TAG_LIST:
            items = list(value)
            et = getattr(value, "tag_type", None)
            if et is None or et == TAG_END:
                et = _tag_of(items[0]) if items else TAG_END
            self.out.append(et)
            self.w("i", len(items))
            for it in items:
                self.write_payload(et, it)
        elif tag == TAG_COMPOUND:
            for k, v in value.items():
                t = _tag_of(v)
                self.out.append(t)
                self.write_string(str(k))
                self.write_payload(t, v)
            self.out.append(TAG_END)
        else:
            raise NBTError(f"unsupported tag {tag}")


def dumps(root: dict, name: str = "", little_endian: bool = False, compression: Optional[str] = "gzip") -> bytes:
    w = _Writer(little_endian)
    w.out.append(TAG_COMPOUND)
    w.write_string(name)
    w.write_payload(TAG_COMPOUND, root)
    data = bytes(w.out)
    if compression == "gzip":
        return gzip.compress(data)
    if compression == "zlib":
        return zlib.compress(data)
    return data


def dump(root: dict, path: str, name: str = "", little_endian: bool = False, compression: Optional[str] = "gzip") -> None:
    with open(path, "wb") as fh:
        fh.write(dumps(root, name, little_endian, compression))


# --------------------------------------------------------------------------------------
# Helpers for packed block state arrays
# --------------------------------------------------------------------------------------
def unpack_bits(longs: np.ndarray, bits: int, count: int, padded: bool) -> np.ndarray:
    """Unpack ``count`` unsigned ``bits``-wide entries from an int64 array.

    ``padded=True`` is the 1.16+ chunk layout (entries never straddle a long);
    ``padded=False`` is the tightly packed layout used by Litematica and pre-1.16 chunks.
    Returns an int64 array of length ``count``.
    """
    longs = np.asarray(longs, dtype=np.int64).astype(np.uint64)
    if bits <= 0:
        return np.zeros(count, dtype=np.int64)
    mask = np.uint64((1 << bits) - 1)
    if padded:
        per_long = 64 // bits
        idx = np.arange(count)
        li = idx // per_long
        shift = ((idx % per_long) * bits).astype(np.uint64)
        if len(longs) < (count + per_long - 1) // per_long:
            raise NBTError("packed array too short")
        return ((longs[li] >> shift) & mask).astype(np.int64)
    # tightly packed: convert to a bit stream via little-endian bytes then extract
    total_bits = count * bits
    needed_longs = (total_bits + 63) // 64
    if len(longs) < needed_longs:
        raise NBTError("packed array too short")
    idx = np.arange(count, dtype=np.int64)
    start = idx * bits
    li = start // 64
    off = (start % 64).astype(np.uint64)
    lo = longs[li] >> off
    # entries crossing into the next long
    cross = (off.astype(np.int64) + bits) > 64
    hi = np.zeros(count, dtype=np.uint64)
    if cross.any():
        li2 = li[cross] + 1
        li2 = np.minimum(li2, len(longs) - 1)
        hi[cross] = longs[li2] << (np.uint64(64) - off[cross])
    return ((lo | hi) & mask).astype(np.int64)


def pack_bits(values: np.ndarray, bits: int, padded: bool) -> np.ndarray:
    """Inverse of :func:`unpack_bits`; returns an int64 array."""
    values = np.asarray(values, dtype=np.uint64)
    count = len(values)
    if bits <= 0:
        return np.zeros(0, dtype=np.int64)
    if padded:
        per_long = 64 // bits
        n = (count + per_long - 1) // per_long
        out = np.zeros(n, dtype=np.uint64)
        idx = np.arange(count)
        li = idx // per_long
        shift = ((idx % per_long) * bits).astype(np.uint64)
        np.bitwise_or.at(out, li, values << shift)
        return out.astype(np.int64)
    n = (count * bits + 63) // 64
    out = np.zeros(n, dtype=np.uint64)
    for i in range(count):  # tightly packed writer only used for tests/fixtures
        v = int(values[i])
        start = i * bits
        li = start // 64
        off = start % 64
        out[li] |= np.uint64((v << off) & 0xFFFFFFFFFFFFFFFF)
        if off + bits > 64:
            out[li + 1] |= np.uint64(v >> (64 - off))
    return out.astype(np.int64)


def read_varint_array(data: np.ndarray | bytes, count: Optional[int] = None) -> np.ndarray:
    """Decode the varint-encoded index stream used by Sponge schematic BlockData."""
    b = np.asarray(data, dtype=np.uint8) if not isinstance(data, (bytes, bytearray)) else np.frombuffer(bytes(data), dtype=np.uint8)
    b = b.astype(np.int64)
    n = len(b)
    # Fast path: no continuation bits at all
    if n and not (b & 0x80).any():
        return b if count is None else b[:count]
    out = []
    i = 0
    val = 0
    shift = 0
    append = out.append
    for i in range(n):
        byte = int(b[i])
        val |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            if shift > 35:
                raise NBTError("varint too long")
        else:
            append(val)
            val = 0
            shift = 0
    arr = np.asarray(out, dtype=np.int64)
    if count is not None:
        if len(arr) < count:
            raise NBTError(f"varint block data too short: {len(arr)} < {count}")
        arr = arr[:count]
    return arr


def iter_compounds(lst: Any) -> Iterator[Compound]:
    if isinstance(lst, list):
        for it in lst:
            if isinstance(it, Compound):
                yield it


Value = Union[Byte, Short, Int, Long, Float, Double, String, ByteArray, IntArray, LongArray, List, Compound]
