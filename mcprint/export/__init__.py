"""Mesh export: STL / OBJ / 3MF, bed-fit tiling."""
from .obj import write_obj
from .splitter import Tile, fits_bed, plan_tiles
from .stl import read_stl, write_stl, write_stl_set
from .threemf import read_3mf_summary, write_3mf

EXPORT_FORMATS = {
    "3mf": "3MF, multi-color (one part per filament)",
    "3mf-bambu": "3MF project for Bambu Studio / OrcaSlicer (filaments pre-assigned)",
    "stl": "STL, one file per color",
    "stl-merged": "STL, single merged solid",
    "obj": "OBJ + MTL (colored groups)",
}

__all__ = ["write_obj", "Tile", "fits_bed", "plan_tiles", "read_stl", "write_stl", "write_stl_set",
           "read_3mf_summary", "write_3mf", "EXPORT_FORMATS"]
