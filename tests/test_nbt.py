import numpy as np
import pytest

from mcprint import nbt


def _sample_root():
    return nbt.Compound({
        "byte": nbt.Byte(-3),
        "short": nbt.Short(1234),
        "int": nbt.Int(-70000),
        "long": nbt.Long(1 << 40),
        "float": nbt.Float(1.5),
        "double": nbt.Double(2.25),
        "str": nbt.String("héllo"),
        "bytes": np.array([1, 2, -3], dtype=np.int8).view(nbt.ByteArray),
        "ints": np.array([1, -2, 300000], dtype=np.int32).view(nbt.IntArray),
        "longs": np.array([1, -2, 1 << 50], dtype=np.int64).view(nbt.LongArray),
        "list": nbt.List([nbt.Int(1), nbt.Int(2)], nbt.TAG_INT),
        "comp": nbt.Compound({"a": nbt.String("b")}),
        "clist": nbt.List([nbt.Compound({"x": nbt.Int(1)}), nbt.Compound({"x": nbt.Int(2)})], nbt.TAG_COMPOUND),
        "empty": nbt.List([], nbt.TAG_END),
    })


@pytest.mark.parametrize("little", [False, True])
@pytest.mark.parametrize("compression", ["gzip", "zlib", None])
def test_roundtrip(little, compression):
    root = _sample_root()
    data = nbt.dumps(root, name="root", little_endian=little, compression=compression)
    name, back = nbt.loads(data, little_endian=little)
    assert name == "root"
    assert back["byte"] == -3 and isinstance(back["byte"], nbt.Byte)
    assert back["short"] == 1234
    assert back["int"] == -70000
    assert back["long"] == 1 << 40
    assert back["float"] == 1.5
    assert back["double"] == 2.25
    assert back["str"] == "héllo"
    assert list(back["bytes"]) == [1, 2, -3]
    assert list(back["ints"]) == [1, -2, 300000]
    assert list(back["longs"]) == [1, -2, 1 << 50]
    assert list(back["list"]) == [1, 2]
    assert back["list"].tag_type == nbt.TAG_INT
    assert back["comp"]["a"] == "b"
    assert back["clist"][1]["x"] == 2
    assert back["empty"] == []


def test_autodetect_endianness():
    root = _sample_root()
    be = nbt.dumps(root, compression=None)
    le = nbt.dumps(root, little_endian=True, compression=None)
    assert nbt.loads(be)[1]["int"] == -70000
    assert nbt.loads(le, little_endian=True)[1]["int"] == -70000


def test_truncated_raises():
    data = nbt.dumps(_sample_root(), compression=None)
    with pytest.raises(nbt.NBTError):
        nbt.loads(data[:-10])


def test_unpack_bits_padded_and_tight():
    rng = np.random.default_rng(1)
    for bits in (2, 4, 5, 7, 9, 12):
        vals = rng.integers(0, 1 << bits, size=1000)
        for padded in (True, False):
            packed = nbt.pack_bits(vals, bits, padded)
            out = nbt.unpack_bits(packed, bits, 1000, padded)
            assert np.array_equal(out, vals), (bits, padded)


def test_varint_array():
    raw = bytes([0x00, 0x01, 0x7F, 0x80, 0x01, 0xFF, 0x7F, 0x80, 0x80, 0x01])
    out = nbt.read_varint_array(np.frombuffer(raw, dtype=np.uint8))
    assert list(out) == [0, 1, 127, 128, 16383, 16384]
    assert list(nbt.read_varint_array(np.frombuffer(bytes([1, 2, 3]), dtype=np.uint8), count=2)) == [1, 2]


def test_compound_helpers():
    c = nbt.Compound({"Width": nbt.Short(5), "Name": nbt.String("x"), "L": nbt.List([nbt.Int(1)], nbt.TAG_INT)})
    assert c.get_int("Width") == 5
    assert c.get_int("Missing", 7) == 7
    assert c.get_str("Name") == "x"
    assert c.get_list("L") == [1]
    assert c.find("width") == 5
