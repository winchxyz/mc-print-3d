"""Modernize Java Edition block ids/properties from any 1.13+ version to 1.21.

Every schematic format written since the 1.13 "flattening" stores blocks as a
namespaced id plus a property map (``minecraft:oak_log[axis=x]``), so this
module only has to worry about the handful of vanilla renames that happened
*after* the flattening, plus the two splits that need the schematic's
``DataVersion`` to resolve (``stone_slab`` and ``cauldron``).

Anything not covered passes through untouched -- modded blocks included -- so
the result is always safe to look up as
``assets/minecraft/blockstates/<path>.json`` in a 1.20/1.21 client jar (for
vanilla ids) or to hand to a mod-aware resolver.
"""

from __future__ import annotations

__all__ = ["modernize_block", "RENAMES"]

_MC = "minecraft:"

# 18w43a, the first 1.14 snapshot (1.13.2 is 1631).  Below this, a
# ``minecraft:stone_slab`` is the block 1.14 renamed to smooth_stone_slab.
_DV_1_14 = 1901
# 1.20.3 / 23w41a -- the version that renamed grass -> short_grass.  Data at or
# above this already writes short_grass, so the rename is a no-op there.
_DV_1_20_3 = 3698

_WOODS_1_13 = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak")


def _qualify(mapping: dict[str, str]) -> dict[str, str]:
    """Add the ``minecraft:`` namespace to both sides of a rename table."""
    out: dict[str, str] = {}
    for old, new in mapping.items():
        out[old if ":" in old else _MC + old] = new if ":" in new else _MC + new
    return out


# --------------------------------------------------------------------------
# Rename tables
# --------------------------------------------------------------------------

#: Renames that shipped in a *released* version between 1.13 and 1.21.
_RELEASE_RENAMES = _qualify(
    {
        # --- 1.14 (18w43a) -------------------------------------------------
        # The only wooden sign in 1.13 became the oak one when 1.14 added the
        # other five woods.
        "sign": "oak_sign",
        "wall_sign": "oak_wall_sign",
        # --- 1.17 (21w05a) -------------------------------------------------
        "grass_path": "dirt_path",
        # --- 1.20.3 (23w41a) -----------------------------------------------
        # In 1.13-1.20.2 "grass" is the short plant; the grass *block* has been
        # grass_block ever since the flattening, so this is unambiguous here.
        "grass": "short_grass",
        # --- 1.21.9 (copper age) --------------------------------------------
        # The iron chain got a material prefix when copper chains were added.
        "chain": "iron_chain",
    }
)

#: Ids that only ever existed inside a snapshot cycle.  Harmless to keep: none
#: of them is a valid 1.21 id, so they can never shadow a real block.
_SNAPSHOT_RENAMES = _qualify(
    {
        # 1.16 snapshots, before the "soul fire" family was renamed.
        "soul_fire_torch": "soul_torch",
        "soul_fire_wall_torch": "soul_wall_torch",
        "soul_fire_lantern": "soul_lantern",
        "soul_fire_campfire": "soul_campfire",
    }
)

#: Leftovers from partially-flattened 1.12-era exports.  These are Mojang's own
#: DataFixerUpper renames (schemas 1484 / 1490 / 1510); half-broken converters
#: still emit them, none of the old ids exists in 1.13+, so mapping them can
#: only help.
_PRE_FLATTENING_RENAMES: dict[str, str] = {
    "mob_spawner": "spawner",
    "portal": "nether_portal",
    "melon_block": "melon",
    "sea_grass": "seagrass",
    "tall_sea_grass": "tall_seagrass",
}
for _w in _WOODS_1_13:
    _PRE_FLATTENING_RENAMES[_w + "_bark"] = _w + "_wood"
    _PRE_FLATTENING_RENAMES["stripped_" + _w + "_bark"] = "stripped_" + _w + "_wood"
_PRE_FLATTENING_RENAMES = _qualify(_PRE_FLATTENING_RENAMES)

#: Public, flat old -> new table (namespaced on both sides).  Only
#: unconditional renames live here; the data-version-dependent one
#: (``stone_slab``) and the one that also moves properties around
#: (``cauldron``) are applied by :func:`modernize_block`.
RENAMES: dict[str, str] = {}
RENAMES.update(_PRE_FLATTENING_RENAMES)
RENAMES.update(_SNAPSHOT_RENAMES)
RENAMES.update(_RELEASE_RENAMES)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _clean_props(props, lower: bool = True) -> dict[str, str]:
    """Copy ``props`` into a plain ``{str: str}`` dict.  Never raises."""
    out: dict[str, str] = {}
    if not props:
        return out
    try:
        items = list(props.items())
    except AttributeError:
        return out
    for key, value in items:
        try:
            k = str(key).strip()
            if lower:
                k = k.lower()
            # unwrap nbt library tag objects (they expose ``.value``)
            inner = value.value if hasattr(value, "value") else value
            if inner is True:
                v = "true"
            elif inner is False:
                v = "false"
            else:
                v = str(inner).strip()
                if lower:
                    v = v.lower()
        except Exception:  # pragma: no cover - defensive
            continue
        if k:
            out[k] = v
    return out


def _cauldron(props: dict[str, str]) -> tuple[str, dict[str, str]]:
    """1.17 split ``cauldron`` into empty / water / lava / powder-snow ones."""
    raw = props.get("level")
    try:
        level = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        level = 0
    if level <= 0:
        # A 1.17+ ``cauldron`` has no properties at all.
        return _MC + "cauldron", {k: v for k, v in props.items() if k != "level"}
    out = dict(props)
    out["level"] = str(min(level, 3))
    return _MC + "water_cauldron", out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def modernize_block(
    name: str,
    props: dict[str, str] | None = None,
    data_version: int | None = None,
) -> tuple[str, dict[str, str]]:
    """Map a Java block name+properties from any 1.13+ version to the current
    (1.21) names/properties.  Unknown names pass through unchanged (namespace
    added if missing).  Never raises.
    """
    try:
        return _modernize(name, props, data_version)
    except Exception:  # pragma: no cover - the function must never raise
        try:
            fallback = str(name).strip().lower()
            if ":" not in fallback:
                fallback = _MC + fallback
        except Exception:
            fallback = _MC + "air"
        return fallback, {}


def _modernize(name, props, data_version) -> tuple[str, dict[str, str]]:
    ident = str(name).strip().lower()
    if not ident:
        return _MC + "air", {}
    if ":" not in ident:
        ident = _MC + ident
    namespace = ident.partition(":")[0]

    # Modded blocks keep their property values verbatim (case included).
    if namespace != "minecraft":
        return ident, _clean_props(props, lower=False)

    out_props = _clean_props(props)

    try:
        dv = int(data_version) if data_version is not None else None
    except (TypeError, ValueError):
        dv = None

    # --- splits that need more than a name swap ---------------------------
    if ident == _MC + "cauldron":
        return _cauldron(out_props)
    if ident == _MC + "stone_slab":
        # 1.13's stone_slab is 1.14's smooth_stone_slab; 1.14+ reused the id
        # for a genuinely different block, so without a DataVersion the only
        # safe answer is "leave it alone".
        if dv is not None and dv < _DV_1_14:
            return _MC + "smooth_stone_slab", out_props
        return ident, out_props

    return RENAMES.get(ident, ident), out_props


# --------------------------------------------------------------------------
# SOURCES OF UNCERTAINTY  (things worth double-checking against a client jar)
# --------------------------------------------------------------------------
# 1. ``stone_slab`` is only resolvable with a DataVersion.  The 1901 cutoff is
#    18w43a; a schematic saved by a third-party editor may carry no DataVersion
#    at all (or a bogus one), in which case we deliberately keep ``stone_slab``
#    and the model comes out as 1.14+ stone rather than smooth stone.
# 2. ``cauldron`` -> ``water_cauldron`` assumes any non-zero level is water.
#    Lava and powder-snow cauldrons are indistinguishable from a pre-1.17
#    palette, so they are lost.  Levels above 3 are clamped.
# 3. ``grass`` -> ``short_grass`` assumes 1.13+ input.  A *pre*-flattening
#    export that still says ``minecraft:grass`` meant the grass BLOCK, and this
#    module will turn it into the plant.  Screen for that upstream with the
#    DataVersion (< 1451) if 1.12 data can reach here.
# 4. The snapshot-only soul-fire ids are from memory of the 1.16 snapshot cycle;
#    the exact snapshot in which each was renamed is not verified.  They are
#    inert for released-version input.
# 5. The pre-flattening leftovers (``*_bark`` / ``portal`` / ``mob_spawner`` /
#    ``melon_block`` / ``sea_grass``) are Mojang DFU renames, but this module is
#    not a full 1.12 -> 1.13 flattener: numeric ids and ``wool:14``-style data
#    values are out of scope (see legacy_ids.py for those).
# 6. Deliberately not modelled, because they are entity/item renames rather
#    than blocks: zombie_pigman -> zombified_piglin, the 1.13 dye/item renames
#    (rose_red -> red_dye, ...), and sign/banner block-entity text formats.
# 7. As far as I know no vanilla block *property* was renamed or removed between
#    1.13 and 1.21 apart from the cauldron level above, so properties are passed
#    through verbatim.  Properties added later (waterlogged on signs, the
#    1.20 sign ``rotation`` split, wall ``low``/``tall`` connections) are simply
#    absent from old data, which is fine: the blockstates file supplies the
#    defaults.
