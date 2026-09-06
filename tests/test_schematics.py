import glob
import os

import numpy as np
import pytest

from mcprint import nbt
from mcprint.schematics import BlockState, Schematic, load_schematic, detect_format, PaletteBuilder, AIR
from mcprint.schematics.litematica import save_litematic
from mcprint.schematics.sponge import save_sponge
from mcprint.schematics.structure import save_structure


def make_sample() -> Schematic:
    pb = PaletteBuilder()
    stone = pb.add(BlockState.parse("minecraft:stone"))
    stairs = pb.add(BlockState.parse("minecraft:oak_stairs[facing=east,half=bottom,shape=straight]"))
    grass = pb.add(BlockState.parse("minecraft:short_grass"))
    grid = np.zeros((3, 4, 5), dtype=np.int32)  # y, z, x
    grid[0, :, :] = stone
    grid[1, 1, 2] = stairs
    grid[2, 3, 4] = grass
    return Schematic(width=5, height=3, length=4, palette=pb.states, blocks=grid, name="sample")


def _check(s: Schematic, fmt_prefix: str):
    assert s.format.startswith(fmt_prefix)
    assert (s.width, s.height, s.length) == (5, 3, 4)
    assert s.get(0, 0, 0).name == "minecraft:stone"
    st = s.get(2, 1, 1)
    assert st.name == "minecraft:oak_stairs"
    assert st.properties["facing"] == "east"
    assert s.get(4, 2, 3).name == "minecraft:short_grass"
    assert s.get(1, 1, 1) == AIR
    assert s.block_count == 22


@pytest.mark.parametrize("version", [2, 3])
def test_sponge_roundtrip(tmp_path, version):
    p = tmp_path / "s.schem"
    save_sponge(make_sample(), str(p), version=version)
    s = load_schematic(p)
    _check(s, "sponge")


def test_litematic_roundtrip(tmp_path):
    p = tmp_path / "s.litematic"
    save_litematic(make_sample(), str(p))
    s = load_schematic(p)
    _check(s, "litematica")
    assert s.name == "sample"


def test_structure_roundtrip(tmp_path):
    p = tmp_path / "s.nbt"
    save_structure(make_sample(), str(p))
    s = load_schematic(p)
    _check(s, "structure")


def test_mcedit_numeric(tmp_path):
    # 2x2x2: stone, granite (1:1), red wool (35:14), air
    w, h, l = 2, 2, 2
    blocks = np.zeros(8, dtype=np.int8)
    data = np.zeros(8, dtype=np.int8)
    blocks[0] = 1
    blocks[1] = 1; data[1] = 1
    blocks[2] = 35; data[2] = 14
    blocks[3] = 53; data[3] = 6   # oak stairs facing south, top
    root = nbt.Compound({
        "Width": nbt.Short(w), "Height": nbt.Short(h), "Length": nbt.Short(l),
        "Materials": nbt.String("Alpha"),
        "Blocks": blocks.view(nbt.ByteArray), "Data": data.view(nbt.ByteArray),
    })
    p = tmp_path / "s.schematic"
    nbt.dump(root, str(p), name="Schematic")
    s = load_schematic(p)
    assert s.format == "mcedit"
    assert s.get(0, 0, 0).name == "minecraft:stone"
    assert s.get(1, 0, 0).name == "minecraft:granite"
    assert s.get(0, 0, 1).name == "minecraft:red_wool"
    st = s.get(1, 0, 1)
    assert st.name == "minecraft:oak_stairs" and st.properties["facing"] == "south" and st.properties["half"] == "top"
    assert s.get(0, 1, 0) == AIR


def test_mcedit_addblocks_and_mapping(tmp_path):
    w, h, l = 2, 1, 1
    blocks = np.array([0x34, 0x00], dtype=np.int8)      # low bytes
    add = np.array([0x01], dtype=np.int8)                # even index 0 gets high nibble 1 -> id 0x134 = 308
    root = nbt.Compound({
        "Width": nbt.Short(w), "Height": nbt.Short(h), "Length": nbt.Short(l),
        "Blocks": blocks.view(nbt.ByteArray), "Data": np.zeros(2, dtype=np.int8).view(nbt.ByteArray),
        "AddBlocks": add.view(nbt.ByteArray),
        "SchematicaMapping": nbt.Compound({"somemod:fancy_block": nbt.Short(308)}),
    })
    p = tmp_path / "m.schematic"
    nbt.dump(root, str(p), name="Schematic")
    s = load_schematic(p)
    assert s.get(0, 0, 0).name == "somemod:fancy_block"


def test_mcstructure(tmp_path):
    # 2x1x2 bedrock structure: index order is x-major, then y, then z
    w, h, l = 2, 1, 2
    palette = nbt.List([
        nbt.Compound({"name": nbt.String("minecraft:stone"), "states": nbt.Compound({}), "version": nbt.Int(1)}),
        nbt.Compound({"name": nbt.String("minecraft:wool"), "states": nbt.Compound({"color": nbt.String("red")}), "version": nbt.Int(1)}),
    ], nbt.TAG_COMPOUND)
    # (x=0,z=0)=stone, (x=0,z=1)=air(-1), (x=1,z=0)=wool, (x=1,z=1)=stone
    idx = nbt.List([nbt.Int(0), nbt.Int(-1), nbt.Int(1), nbt.Int(0)], nbt.TAG_INT)
    root = nbt.Compound({
        "format_version": nbt.Int(1),
        "size": nbt.List([nbt.Int(w), nbt.Int(h), nbt.Int(l)], nbt.TAG_INT),
        "structure": nbt.Compound({
            "block_indices": nbt.List([idx, nbt.List([nbt.Int(-1)] * 4, nbt.TAG_INT)], nbt.TAG_LIST),
            "entities": nbt.List([], nbt.TAG_COMPOUND),
            "palette": nbt.Compound({"default": nbt.Compound({"block_palette": palette, "block_position_data": nbt.Compound({})})}),
        }),
        "structure_world_origin": nbt.List([nbt.Int(0), nbt.Int(0), nbt.Int(0)], nbt.TAG_INT),
    })
    p = tmp_path / "s.mcstructure"
    nbt.dump(root, str(p), name="", little_endian=True, compression=None)
    s = load_schematic(p)
    assert s.format == "mcstructure"
    assert s.get(0, 0, 0).name == "minecraft:stone"
    assert s.get(0, 0, 1) == AIR
    assert s.get(1, 0, 0).name in ("minecraft:red_wool", "minecraft:wool")
    assert s.get(1, 0, 1).name == "minecraft:stone"


def test_detect_format(tmp_path):
    p = tmp_path / "x.schem"
    save_sponge(make_sample(), str(p))
    assert detect_format(p.read_bytes(), str(p)) == "sponge"


def test_blockstate_parse():
    st = BlockState.parse("oak_stairs[facing=east, half=top]")
    assert st.name == "minecraft:oak_stairs"
    assert st.properties == {"facing": "east", "half": "top"}
    assert str(st) == "minecraft:oak_stairs[facing=east,half=top]"
    assert BlockState.parse("create:cogwheel").namespace == "create"


REAL = glob.glob(os.path.join(os.environ.get("USERPROFILE", ""), "curseforge", "minecraft", "Instances",
                              "All the Mods 10 - ATM10", "kubejs", "assets", "kubejs", "ponder", "*.nbt"))


@pytest.mark.skipif(not REAL, reason="no real structure files on this machine")
def test_real_structure_files():
    for path in REAL[:5]:
        s = load_schematic(path)
        assert s.format == "structure"
        assert s.block_count > 0
        names = {st.name for st in s.unique_states()}
        assert any(":" in n for n in names)
