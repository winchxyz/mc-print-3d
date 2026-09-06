"""Built-in database of 3D printer specifications.

This module is offline data only: no network, no optional dependencies.  It lets the app show a
printer picker, pre-fill bed sizes for a "manual" printer, and enrich discovery results (a Bambu
SSDP packet only carries a model *code* such as ``N9`` -- :func:`spec_for_code` turns that into a
readable model name and a build volume).

Conventions match :mod:`mcprint.printers.base`: sizes are millimetres, X = left-right,
Y = front-back, Z = build height.  ``slots`` is how many *distinct filaments* one print can use
with the vendor multi-material add-on (Bambu AMS = 4 per unit, dual-nozzle H2 series = 8,
Prusa MMU3 = 5, tool changers = number of tools, Creality CFS / Anycubic ACE = 4).

Build volumes come from vendor spec sheets; where a machine is new or the published figure is
ambiguous the value is a best estimate and is flagged in :attr:`PrinterSpec.notes`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .base import PrinterInfo

__all__ = [
    "PrinterSpec",
    "PRINTER_SPECS",
    "find_spec",
    "spec_for_code",
    "vendors",
    "specs_for_vendor",
    "spec_to_printer_info",
]


@dataclass
class PrinterSpec:
    """Static specification of one printer model."""

    vendor: str
    model: str                                        # display model name, e.g. 'Bambu Lab A2L'
    bed_x: float                                      # mm, printable X (left-right)
    bed_y: float                                      # mm, printable Y (front-back)
    bed_z: float                                      # mm, printable Z (build height)
    nozzle_mm: float = 0.4
    extruders: int = 1                                # physical tool heads
    slots: int = 1                                    # distinct filaments in one print (with the usual add-on)
    kind_hint: str = ""                               # 'bambu'|'moonraker'|'prusalink'|'octoprint'|'duet'|'elegoo'|'serial'
    codes: list[str] = field(default_factory=list)    # vendor model codes seen on the network (Bambu SSDP DevModel)
    aliases: list[str] = field(default_factory=list)  # other names / spellings
    notes: str = ""

    @property
    def label(self) -> str:
        if self.model.lower().startswith(self.vendor.lower()):
            return self.model
        return f"{self.vendor} {self.model}"

    @property
    def bed_text(self) -> str:
        return f"X {self.bed_x:g} x Y {self.bed_y:g} x Z {self.bed_z:g} mm"


def _s(vendor: str, model: str, x: float, y: float, z: float, *, nozzle: float = 0.4,
       extruders: int = 1, slots: int = 1, kind: str = "", codes: Iterable[str] = (),
       aliases: Iterable[str] = (), notes: str = "") -> PrinterSpec:
    """Terse constructor used by the table below."""
    return PrinterSpec(vendor=vendor, model=model, bed_x=float(x), bed_y=float(y), bed_z=float(z),
                       nozzle_mm=float(nozzle), extruders=int(extruders), slots=int(slots),
                       kind_hint=kind, codes=list(codes), aliases=list(aliases), notes=notes)


# =============================================================================================
# The database
# =============================================================================================

PRINTER_SPECS: list[PrinterSpec] = [
    # -- Bambu Lab -----------------------------------------------------------------------------
    # Build volumes and DevModel codes follow Bambu Studio's own machine definitions.
    _s("Bambu Lab", "Bambu Lab X1 Carbon", 256, 256, 256, kind="bambu", slots=4,
       codes=["BL-P001"], aliases=["X1C", "X1 Carbon", "X1-Carbon", "Bambu X1C"]),
    _s("Bambu Lab", "Bambu Lab X1", 256, 256, 256, kind="bambu", slots=4,
       codes=["BL-P002"], aliases=["X1", "Bambu X1"]),
    _s("Bambu Lab", "Bambu Lab X1E", 256, 256, 256, kind="bambu", slots=4,
       codes=["C13"], aliases=["X1E", "Bambu X1E"]),
    _s("Bambu Lab", "Bambu Lab P1P", 256, 256, 256, kind="bambu", slots=4,
       codes=["C11"], aliases=["P1P", "Bambu P1P"]),
    _s("Bambu Lab", "Bambu Lab P1S", 256, 256, 256, kind="bambu", slots=4,
       codes=["C12"], aliases=["P1S", "Bambu P1S"]),
    _s("Bambu Lab", "Bambu Lab P2S", 256, 256, 256, kind="bambu", slots=4,
       codes=["N7"], aliases=["P2S", "Bambu P2S"]),
    _s("Bambu Lab", "Bambu Lab A1 mini", 180, 180, 180, kind="bambu", slots=4,
       codes=["N1"], aliases=["A1 mini", "A1M", "A1-mini", "Bambu A1 mini"]),
    _s("Bambu Lab", "Bambu Lab A1", 256, 256, 256, kind="bambu", slots=4,
       codes=["N2S"], aliases=["A1", "Bambu A1"]),
    _s("Bambu Lab", "Bambu Lab A2L", 330, 320, 325, kind="bambu", slots=4,
       codes=["N9"], aliases=["A2L", "A2 L", "Bambu A2L"]),
    _s("Bambu Lab", "Bambu Lab X2D", 256, 256, 256, kind="bambu", slots=4,
       codes=["N6"], aliases=["X2D", "Bambu X2D"],
       notes="Build volume unconfirmed; 256x256x256 assumed from the rest of the X series."),
    _s("Bambu Lab", "Bambu Lab H2D", 350, 320, 325, kind="bambu", extruders=2, slots=8,
       codes=["O1D"], aliases=["H2D", "Bambu H2D"],
       notes="Dual nozzle plus AMS: up to 8 filaments in one print."),
    _s("Bambu Lab", "Bambu Lab H2D Pro", 350, 320, 325, kind="bambu", extruders=2, slots=8,
       codes=["O1E"], aliases=["H2D Pro", "H2DP", "Bambu H2D Pro"],
       notes="Dual nozzle plus AMS: up to 8 filaments in one print."),
    _s("Bambu Lab", "Bambu Lab H2C", 330, 320, 325, kind="bambu", extruders=2, slots=8,
       codes=["O1C2"], aliases=["H2C", "Bambu H2C"],
       notes="Build volume unconfirmed (330x320x325 assumed); dual nozzle plus AMS."),
    _s("Bambu Lab", "Bambu Lab H2S", 340, 320, 325, kind="bambu", extruders=1, slots=4,
       codes=["O1S"], aliases=["H2S", "Bambu H2S"],
       notes="Build volume unconfirmed (340x320x325 assumed); single nozzle."),

    # -- Prusa Research ------------------------------------------------------------------------
    _s("Prusa", "Prusa MK3S+", 250, 210, 210, kind="octoprint", slots=5,
       aliases=["MK3S+", "MK3S", "MK3", "i3 MK3S+", "Original Prusa i3 MK3S+"],
       notes="No onboard network (OctoPrint or USB); slots=5 assumes the MMU3 add-on."),
    _s("Prusa", "Prusa MK3.5", 250, 210, 210, kind="prusalink", slots=5,
       aliases=["MK3.5", "MK3.5S", "Original Prusa MK3.5"],
       notes="Z listed as 210 mm (MK3 frame); some Prusa pages quote 220 mm."),
    _s("Prusa", "Prusa MK3.9", 250, 210, 220, kind="prusalink", slots=5,
       aliases=["MK3.9", "MK3.9S", "Original Prusa MK3.9"]),
    _s("Prusa", "Prusa MK4", 250, 210, 220, kind="prusalink", slots=5,
       aliases=["MK4", "Original Prusa MK4"]),
    _s("Prusa", "Prusa MK4S", 250, 210, 220, kind="prusalink", slots=5,
       aliases=["MK4S", "Original Prusa MK4S"]),
    _s("Prusa", "Prusa MINI+", 180, 180, 180, kind="prusalink", slots=1,
       aliases=["MINI+", "MINI", "Prusa Mini", "Original Prusa MINI+"]),
    _s("Prusa", "Prusa CORE One", 250, 220, 270, kind="prusalink", slots=5,
       aliases=["Core One", "CORE One", "Original Prusa CORE One"]),
    _s("Prusa", "Prusa XL 1-tool", 360, 360, 360, kind="prusalink", extruders=1, slots=1,
       aliases=["XL", "Prusa XL", "XL 1 tool", "Original Prusa XL"]),
    _s("Prusa", "Prusa XL 2-tool", 360, 360, 360, kind="prusalink", extruders=2, slots=2,
       aliases=["XL 2 tool", "XL2T", "Prusa XL 2T"]),
    _s("Prusa", "Prusa XL 5-tool", 360, 360, 360, kind="prusalink", extruders=5, slots=5,
       aliases=["XL 5 tool", "XL5T", "Prusa XL 5T"]),

    # -- Creality ------------------------------------------------------------------------------
    _s("Creality", "Ender-3", 220, 220, 250, kind="serial",
       aliases=["Ender 3", "Ender3"]),
    _s("Creality", "Ender-3 Pro", 220, 220, 250, kind="serial", aliases=["Ender 3 Pro"]),
    _s("Creality", "Ender-3 V2", 220, 220, 250, kind="serial", aliases=["Ender 3 V2", "Ender3 V2"]),
    _s("Creality", "Ender-3 V2 Neo", 220, 220, 250, kind="serial", aliases=["Ender 3 V2 Neo"]),
    _s("Creality", "Ender-3 Neo", 220, 220, 250, kind="serial", aliases=["Ender 3 Neo"]),
    _s("Creality", "Ender-3 Max Neo", 300, 300, 320, kind="serial", aliases=["Ender 3 Max Neo"]),
    _s("Creality", "Ender-3 S1", 220, 220, 270, kind="serial", aliases=["Ender 3 S1"]),
    _s("Creality", "Ender-3 S1 Pro", 220, 220, 270, kind="serial", aliases=["Ender 3 S1 Pro"]),
    _s("Creality", "Ender-3 S1 Plus", 300, 300, 300, kind="serial", aliases=["Ender 3 S1 Plus"]),
    _s("Creality", "Ender-3 V3 SE", 220, 220, 250, kind="serial", aliases=["Ender 3 V3 SE"]),
    _s("Creality", "Ender-3 V3 KE", 220, 220, 240, kind="moonraker", aliases=["Ender 3 V3 KE"]),
    _s("Creality", "Ender-3 V3", 220, 220, 250, kind="moonraker", aliases=["Ender 3 V3"]),
    _s("Creality", "Ender-3 V3 Plus", 300, 300, 330, kind="moonraker", aliases=["Ender 3 V3 Plus"]),
    _s("Creality", "Ender-5 S1", 220, 220, 280, kind="serial", aliases=["Ender 5 S1"]),
    _s("Creality", "Ender-5 Plus", 350, 350, 400, kind="serial", aliases=["Ender 5 Plus"]),
    _s("Creality", "Ender-6", 250, 250, 400, kind="serial", aliases=["Ender 6"]),
    _s("Creality", "CR-6 SE", 235, 235, 250, kind="serial", aliases=["CR6 SE", "CR-6"]),
    _s("Creality", "CR-10", 300, 300, 400, kind="serial", aliases=["CR10"]),
    _s("Creality", "CR-10 V3", 300, 300, 400, kind="serial", aliases=["CR10 V3"]),
    _s("Creality", "CR-10 Smart Pro", 300, 300, 400, kind="serial", aliases=["CR10 Smart Pro"]),
    _s("Creality", "CR-10 SE", 220, 220, 265, kind="moonraker", aliases=["CR10 SE"]),
    _s("Creality", "CR-10 Max", 450, 450, 470, kind="serial", aliases=["CR10 Max"]),
    _s("Creality", "CR-M4", 450, 450, 470, kind="serial", aliases=["CRM4"]),
    _s("Creality", "K1", 220, 220, 250, kind="moonraker", aliases=["Creality K1"]),
    _s("Creality", "K1C", 220, 220, 250, kind="moonraker", aliases=["K1 C", "Creality K1C"]),
    _s("Creality", "K1 SE", 220, 220, 250, kind="moonraker", aliases=["K1SE"]),
    _s("Creality", "K1 Max", 300, 300, 300, kind="moonraker", aliases=["K1Max"]),
    _s("Creality", "K2 Plus", 350, 350, 350, kind="moonraker", slots=4, aliases=["K2Plus", "K2 Plus Combo"],
       notes="slots=4 with the CFS unit (chainable CFS units allow more)."),
    _s("Creality", "Hi", 260, 260, 300, kind="moonraker", slots=4,
       aliases=["Creality Hi", "Hi Combo"],
       notes="slots=4 with the CFS unit."),
    _s("Creality", "Ender-2 Pro", 165, 165, 180, kind="serial", aliases=["Ender 2 Pro"]),

    # -- Anycubic ------------------------------------------------------------------------------
    _s("Anycubic", "Kobra", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra Neo", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra Go", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra Plus", 300, 300, 350, kind="serial"),
    _s("Anycubic", "Kobra Max", 400, 400, 450, kind="serial"),
    _s("Anycubic", "Kobra 2", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra 2 Neo", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra 2 Pro", 220, 220, 250, kind="serial"),
    _s("Anycubic", "Kobra 2 Plus", 320, 320, 400, kind="serial"),
    _s("Anycubic", "Kobra 2 Max", 420, 420, 500, kind="serial"),
    _s("Anycubic", "Kobra 3", 250, 250, 260, kind="moonraker", slots=4,
       aliases=["Kobra 3 Combo"], notes="slots=4 with the ACE Pro multi-colour unit."),
    _s("Anycubic", "Kobra 3 Max", 420, 420, 500, kind="moonraker", slots=4,
       notes="slots=4 with the ACE Pro multi-colour unit."),
    _s("Anycubic", "Kobra S1", 250, 250, 250, kind="moonraker", slots=4,
       aliases=["Kobra S1 Combo"], notes="slots=4 with the ACE Pro multi-colour unit."),
    _s("Anycubic", "Vyper", 245, 245, 260, kind="serial"),
    _s("Anycubic", "i3 Mega", 210, 210, 205, kind="serial", aliases=["Mega", "i3 Mega S"]),
    _s("Anycubic", "Chiron", 400, 400, 450, kind="serial"),

    # -- Elegoo --------------------------------------------------------------------------------
    _s("Elegoo", "Neptune 2", 220, 220, 250, kind="serial"),
    _s("Elegoo", "Neptune 3", 220, 220, 280, kind="serial"),
    _s("Elegoo", "Neptune 3 Pro", 225, 225, 280, kind="serial"),
    _s("Elegoo", "Neptune 3 Plus", 320, 320, 400, kind="serial"),
    _s("Elegoo", "Neptune 3 Max", 420, 420, 500, kind="serial"),
    _s("Elegoo", "Neptune 4", 225, 225, 265, kind="moonraker"),
    _s("Elegoo", "Neptune 4 Pro", 225, 225, 265, kind="moonraker"),
    _s("Elegoo", "Neptune 4 Plus", 320, 320, 385, kind="moonraker"),
    _s("Elegoo", "Neptune 4 Max", 420, 420, 480, kind="moonraker"),
    _s("Elegoo", "Centauri", 256, 256, 256, kind="elegoo"),
    _s("Elegoo", "Centauri Carbon", 256, 256, 256, kind="elegoo", aliases=["CC"]),

    # -- Sovol ---------------------------------------------------------------------------------
    _s("Sovol", "SV06", 220, 220, 250, kind="serial"),
    _s("Sovol", "SV06 Plus", 300, 300, 340, kind="serial"),
    _s("Sovol", "SV07", 220, 220, 240, kind="moonraker"),
    _s("Sovol", "SV07 Plus", 300, 300, 340, kind="moonraker"),
    _s("Sovol", "SV08", 350, 350, 345, kind="moonraker"),

    # -- Voron (self-built, Klipper) -------------------------------------------------------------
    _s("Voron", "Voron V0.2", 120, 120, 120, kind="moonraker", aliases=["V0.2", "V0", "Voron Zero"]),
    _s("Voron", "Voron Trident 250", 250, 250, 250, kind="moonraker", aliases=["Trident 250"]),
    _s("Voron", "Voron Trident 300", 300, 300, 250, kind="moonraker", aliases=["Trident 300"],
       notes="Trident Z travel is 250 mm on the 300 and 350 sizes."),
    _s("Voron", "Voron Trident 350", 350, 350, 250, kind="moonraker", aliases=["Trident 350"],
       notes="Trident Z travel is 250 mm on the 300 and 350 sizes."),
    _s("Voron", "Voron V2.4 250", 250, 250, 250, kind="moonraker", aliases=["V2.4 250", "Voron 2.4 250"]),
    _s("Voron", "Voron V2.4 300", 300, 300, 300, kind="moonraker", aliases=["V2.4 300", "Voron 2.4 300"]),
    _s("Voron", "Voron V2.4 350", 350, 350, 350, kind="moonraker", aliases=["V2.4 350", "Voron 2.4 350"]),

    # -- Qidi ----------------------------------------------------------------------------------
    _s("Qidi", "X-Plus 3", 280, 280, 270, kind="moonraker", aliases=["XPlus 3", "Qidi X-Plus 3"]),
    _s("Qidi", "X-Max 3", 325, 325, 315, kind="moonraker", aliases=["XMax 3", "Qidi X-Max 3"]),
    _s("Qidi", "X-Smart 3", 175, 180, 170, kind="moonraker", aliases=["XSmart 3", "Qidi X-Smart 3"]),
    _s("Qidi", "Q1 Pro", 245, 245, 245, kind="moonraker", aliases=["Qidi Q1 Pro"]),
    _s("Qidi", "Plus 4", 305, 305, 280, kind="moonraker", aliases=["Qidi Plus 4", "Plus4"]),
    _s("Qidi", "X-CF Pro", 300, 250, 300, kind="serial", aliases=["XCF Pro"]),

    # -- Flashforge ----------------------------------------------------------------------------
    _s("Flashforge", "Adventurer 5M", 220, 220, 220, kind="moonraker", aliases=["AD5M"]),
    _s("Flashforge", "Adventurer 5M Pro", 220, 220, 220, kind="moonraker", aliases=["AD5M Pro"]),
    _s("Flashforge", "AD5X", 220, 220, 220, kind="moonraker", slots=4,
       aliases=["Adventurer 5X"], notes="slots=4 with the IFS four-colour unit."),
    _s("Flashforge", "Adventurer 4", 220, 200, 250, kind="octoprint", aliases=["AD4"]),
    _s("Flashforge", "Adventurer 3", 150, 150, 150, kind="octoprint", aliases=["AD3"]),
    _s("Flashforge", "Creator Pro 2", 200, 148, 150, kind="serial", extruders=2, slots=2,
       notes="IDEX; 200x148x150 is the dual-material usable area."),

    # -- Ultimaker -----------------------------------------------------------------------------
    _s("Ultimaker", "Ultimaker S3", 230, 190, 200, kind="octoprint", extruders=2, slots=2,
       aliases=["S3", "UM S3"], notes="Networked via Ultimaker's own API, not OctoPrint."),
    _s("Ultimaker", "Ultimaker S5", 330, 240, 300, kind="octoprint", extruders=2, slots=2,
       aliases=["S5", "UM S5"], notes="Networked via Ultimaker's own API, not OctoPrint."),
    _s("Ultimaker", "Ultimaker S7", 330, 240, 300, kind="octoprint", extruders=2, slots=2,
       aliases=["S7", "UM S7"], notes="Networked via Ultimaker's own API, not OctoPrint."),
    _s("Ultimaker", "Ultimaker 2+", 223, 223, 205, kind="serial", aliases=["UM2+", "Ultimaker 2 Plus"]),
    _s("Ultimaker", "Ultimaker 3", 215, 215, 200, kind="octoprint", extruders=2, slots=2,
       aliases=["UM3"]),

    # -- Artillery -----------------------------------------------------------------------------
    _s("Artillery", "Sidewinder X2", 300, 300, 400, kind="serial", aliases=["SW X2"]),
    _s("Artillery", "Sidewinder X3 Pro", 300, 300, 400, kind="serial", aliases=["SW X3 Pro"]),
    _s("Artillery", "Sidewinder X4 Pro", 300, 300, 400, kind="moonraker", aliases=["SW X4 Pro"]),
    _s("Artillery", "Sidewinder X4 Plus", 300, 300, 400, kind="moonraker", aliases=["SW X4 Plus"]),
    _s("Artillery", "Genius", 220, 220, 250, kind="serial"),
    _s("Artillery", "Genius Pro", 220, 220, 250, kind="serial"),

    # -- AnkerMake -----------------------------------------------------------------------------
    _s("AnkerMake", "M5", 235, 235, 250, kind="octoprint", aliases=["AnkerMake M5", "Anker M5"]),
    _s("AnkerMake", "M5C", 220, 220, 250, kind="octoprint", aliases=["AnkerMake M5C", "Anker M5C"]),

    # -- Kingroon ------------------------------------------------------------------------------
    _s("Kingroon", "KP3S", 180, 180, 180, kind="serial"),
    _s("Kingroon", "KP3S Pro S1", 200, 200, 200, kind="serial", aliases=["KP3S Pro"]),
    _s("Kingroon", "KP5L", 300, 300, 330, kind="serial"),

    # -- Snapmaker -----------------------------------------------------------------------------
    _s("Snapmaker", "J1", 300, 200, 200, kind="octoprint", extruders=2, slots=2,
       aliases=["Snapmaker J1", "J1s"], notes="IDEX dual extruder."),
    _s("Snapmaker", "Artisan", 400, 400, 400, kind="octoprint", extruders=2, slots=2,
       aliases=["Snapmaker Artisan"], notes="IDEX dual extruder."),
    _s("Snapmaker", "Snapmaker 2.0 A350", 320, 350, 330, kind="octoprint", aliases=["A350", "2.0 A350"]),
    _s("Snapmaker", "Snapmaker 2.0 A250", 230, 250, 235, kind="octoprint", aliases=["A250", "2.0 A250"]),

    # -- Raise3D -------------------------------------------------------------------------------
    _s("Raise3D", "Pro3", 300, 300, 300, kind="octoprint", extruders=2, slots=2,
       aliases=["Raise3D Pro3"], notes="Dual extruder; dual-material area is 295x300x300."),
    _s("Raise3D", "Pro3 Plus", 300, 300, 605, kind="octoprint", extruders=2, slots=2,
       aliases=["Raise3D Pro3 Plus"]),
    _s("Raise3D", "E2", 330, 240, 240, kind="octoprint", extruders=2, slots=2,
       aliases=["Raise3D E2"], notes="IDEX."),

    # -- FLSUN (delta; X/Y are the round bed diameter) --------------------------------------------
    _s("FLSUN", "V400", 300, 300, 410, kind="moonraker", aliases=["FLSUN V400"],
       notes="Delta: 300 mm bed diameter x 410 mm height."),
    _s("FLSUN", "T1", 260, 260, 330, kind="moonraker", aliases=["FLSUN T1"],
       notes="Delta: 260 mm bed diameter x 330 mm height."),
    _s("FLSUN", "T1 Pro", 260, 260, 330, kind="moonraker", aliases=["FLSUN T1 Pro"],
       notes="Delta: 260 mm bed diameter x 330 mm height."),
    _s("FLSUN", "Super Racer", 260, 260, 330, kind="serial", aliases=["SR", "FLSUN SR"],
       notes="Delta: 260 mm bed diameter x 330 mm height."),
    _s("FLSUN", "Q5", 200, 200, 200, kind="serial", aliases=["FLSUN Q5"],
       notes="Delta: 200 mm bed diameter x 200 mm height."),

    # -- Two Trees -----------------------------------------------------------------------------
    _s("Two Trees", "SK1", 256, 256, 256, kind="moonraker", aliases=["Two Trees SK1", "TwoTrees SK1"]),
    _s("Two Trees", "Sapphire Plus SP-5", 300, 300, 350, kind="serial", aliases=["Sapphire Plus", "SP-5"]),
    _s("Two Trees", "Bluer Plus", 300, 300, 400, kind="serial", aliases=["TwoTrees Bluer Plus"]),

    # -- RatRig (self-built, Klipper) ------------------------------------------------------------
    _s("RatRig", "V-Core 3 300", 300, 300, 300, kind="moonraker", aliases=["VCore 3 300", "V-Core3 300"]),
    _s("RatRig", "V-Core 3 400", 400, 400, 400, kind="moonraker", aliases=["VCore 3 400", "V-Core3 400"]),
    _s("RatRig", "V-Core 3 500", 500, 500, 500, kind="moonraker", aliases=["VCore 3 500", "V-Core3 500"]),
    _s("RatRig", "V-Minion", 180, 180, 180, kind="moonraker", aliases=["VMinion"]),

    # -- Generic fallbacks -----------------------------------------------------------------------
    _s("Generic", "Generic 220x220x250", 220, 220, 250, kind="serial",
       aliases=["220x220x250", "Ender sized"]),
    _s("Generic", "Generic 250x250x250", 250, 250, 250, kind="serial", aliases=["250x250x250"]),
    _s("Generic", "Generic 300x300x400", 300, 300, 400, kind="serial", aliases=["300x300x400"]),
    _s("Generic", "Generic 350x350x400", 350, 350, 400, kind="serial", aliases=["350x350x400"]),
]


# =============================================================================================
# Lookup
# =============================================================================================

# Words that carry no identity: they are dropped before matching.
_NOISE_TOKENS = frozenset({
    "3d", "printer", "printers", "nozzle", "hotend", "extruder", "mm", "the", "series",
    "edition", "fdm", "fff", "machine", "model",
})

# Decimal sizes ("0.4", "0.4mm", "0.6 mm") are print settings, not model names.
_RE_SIZE_MM = re.compile(r"(?<![a-z0-9])\d+(?:\.\d+)?\s*mm(?![a-z0-9])")
_RE_DECIMAL = re.compile(r"(?<![a-z0-9.])\d*\.\d+(?![a-z0-9])")
_RE_NONALNUM = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> tuple[str, ...]:
    """Normalize a printer name into comparable tokens (lowercase, punctuation stripped)."""
    if not text:
        return ()
    s = text.lower()
    s = _RE_SIZE_MM.sub(" ", s)
    s = _RE_DECIMAL.sub(" ", s)
    s = _RE_NONALNUM.sub(" ", s)
    return tuple(t for t in s.split() if t and t not in _NOISE_TOKENS)


def _spec_keys(spec: PrinterSpec) -> list[tuple[str, ...]]:
    """All token sequences that should identify this spec."""
    keys: list[tuple[str, ...]] = []
    for text in [spec.model, f"{spec.vendor} {spec.model}", *spec.aliases, *spec.codes]:
        toks = _tokens(text)
        if toks and toks not in keys:
            keys.append(toks)
    return keys


_KEY_INDEX: list[tuple[PrinterSpec, list[tuple[str, ...]], frozenset[str]]] = [
    (spec, _spec_keys(spec), frozenset(_tokens(spec.vendor))) for spec in PRINTER_SPECS
]

_CODE_INDEX: dict[str, PrinterSpec] = {}
for _spec_obj in PRINTER_SPECS:
    for _code in _spec_obj.codes:
        _CODE_INDEX.setdefault(_code.strip().upper(), _spec_obj)
del _spec_obj, _code


def _match_score(query: tuple[str, ...], key: tuple[str, ...]) -> float:
    """0.0 (no relation) .. 1.0 (identical) similarity between two token sequences."""
    if not query or not key:
        return 0.0
    if query == key:
        return 1.0
    qs, ks = set(query), set(key)
    common = qs & ks
    if not common:
        return 0.0
    cover_q = len(common) / len(qs)   # how much of what the user typed we explain
    cover_k = len(common) / len(ks)   # how specific the model is to what they typed
    return 0.6 * cover_q + 0.4 * cover_k


def find_spec(name: str) -> Optional[PrinterSpec]:
    """Fuzzy, case-insensitive lookup of a printer by any name, alias or vendor code.

    Handles vendor prefixes, punctuation and trailing print settings, e.g.
    ``find_spec("bambu lab a2l 0.4 nozzle")`` -> the A2L, ``find_spec("X1C")`` -> the X1 Carbon.
    Returns ``None`` when nothing plausible matches.
    """
    query = _tokens(name or "")
    if not query:
        return None

    best: Optional[PrinterSpec] = None
    best_score = 0.0
    for spec, keys, vendor_tokens in _KEY_INDEX:
        for key in keys:
            score = _match_score(query, key)
            if score <= 0.0:
                continue
            # A hit on the vendor name alone ("bambu lab") is not a model match.
            if not (set(query) & set(key)) - vendor_tokens:
                continue
            # Prefer the shortest/most exact model name among equal scores.
            if score > best_score or (score == best_score and best is not None
                                      and len(spec.model) < len(best.model)):
                best, best_score = spec, score

    if best is None or best_score < 0.55:
        return None
    return best


def spec_for_code(code: str) -> Optional[PrinterSpec]:
    """Exact vendor model-code lookup (e.g. the Bambu SSDP ``DevModel.bambu.net`` value)."""
    if not code:
        return None
    return _CODE_INDEX.get(str(code).strip().upper())


def vendors() -> list[str]:
    """All vendors in the database, sorted."""
    return sorted({spec.vendor for spec in PRINTER_SPECS})


def specs_for_vendor(vendor: str) -> list[PrinterSpec]:
    """Every spec for one vendor (case-insensitive), in database order."""
    want = (vendor or "").strip().lower()
    return [spec for spec in PRINTER_SPECS if spec.vendor.lower() == want]


def spec_to_printer_info(spec: PrinterSpec, name: Optional[str] = None) -> PrinterInfo:
    """Turn a catalog entry into a manually configured :class:`PrinterInfo`."""
    return PrinterInfo(
        id=f"manual:{spec.vendor}:{spec.model}",
        kind="manual",
        name=name or spec.model,
        model=spec.model,
        vendor=spec.vendor,
        bed_x=spec.bed_x,
        bed_y=spec.bed_y,
        bed_z=spec.bed_z,
        nozzle_mm=spec.nozzle_mm,
        extruders=spec.extruders,
        slots=spec.slots,
    )
