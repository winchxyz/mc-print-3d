"""mc-print-3d: turn Minecraft schematics into 3D-printable, multi-color models.

Package layout
--------------
- ``mcprint.nbt``         : NBT reader/writer (Java big-endian and Bedrock little-endian).
- ``mcprint.schematics``  : loaders for .schematic / .schem / .litematic / .nbt / .mcstructure / .bp
- ``mcprint.assets``      : locating Minecraft installs, reading jars/mods/resource packs,
                            resolving blockstates -> models -> textures.
- ``mcprint.voxel``       : texture-aware voxelizer, chunked grid assembly, greedy mesher.
- ``mcprint.color``       : quantization, filament library, filament catalogs.
- ``mcprint.export``      : STL / OBJ / 3MF writers, bed-fit splitting.
- ``mcprint.printers``    : printer database, USB + network detection, filament sync.
- ``mcprint.pipeline``    : the end-to-end conversion driver shared by CLI and GUI.
- ``mcprint.cli``         : command line interface.
- ``mcprint.app``/``gui`` : PySide6 desktop application.
"""

__version__ = "1.0.0"
APP_NAME = "mc-print-3d"
