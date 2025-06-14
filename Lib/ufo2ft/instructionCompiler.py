import array
import logging

from fontTools import ttLib
from fontTools.pens.hashPointPen import HashPointPen
from fontTools.ttLib import newTable
from fontTools.ttLib.tables._g_l_y_f import (
    OVERLAP_COMPOUND,
    ROUND_XY_TO_GRID,
    USE_MY_METRICS,
)

from ufo2ft.constants import (
    OBJECT_LIBS_KEY,
    TRUETYPE_INSTRUCTIONS_KEY,
    TRUETYPE_METRICS_KEY,
    TRUETYPE_OVERLAP_KEY,
    TRUETYPE_ROUND_KEY,
)

logger = logging.getLogger(__name__)


class InstructionCompiler:
    def _check_glyph_hash(self, glyph, ttglyph, glyph_hash, otf):
        """Check if the supplied glyph hash from the ufo matches the current outlines."""
        ttwidth = otf["hmtx"][glyph.name][0]
        hash_pen = HashPointPen(ttwidth, otf.getGlyphSet())
        ttglyph.drawPoints(hash_pen, otf["glyf"])
        if glyph_hash is None:
            # The glyph hash is required
            logger.error(
                f"Glyph hash missing, glyph '{glyph.name}' will have "
                "no instructions in font."
            )
            return False

        if glyph_hash != hash_pen.hash:
            logger.error(
                f"Glyph hash mismatch, glyph '{glyph.name}' will have "
                "no instructions in font."
            )
            return False
        return True

    def _check_tt_data_format(self, ttdata, name):
        """Make sure we understand the format version, currently only version 1
        is supported."""
        formatVersion = ttdata.get("formatVersion", None)
        if not isinstance(formatVersion, str):
            raise TypeError(
                f"Illegal type '{type(formatVersion).__name__}' instead of 'str' for "
                f"formatVersion for instructions in {name}."
            )
        if formatVersion != "1":
            raise NotImplementedError(
                f"Unknown formatVersion {formatVersion} for instructions in {name}."
            )

    def _compile_program(self, key, table_tag):
        """Compile the program for prep or fpgm."""
        assert table_tag in ("prep", "fpgm")
        ttdata = self.ufo.lib.get(TRUETYPE_INSTRUCTIONS_KEY, None)
        if ttdata:
            self._check_tt_data_format(ttdata, f"lib key '{key}'")
            asm = ttdata.get(key, None)
            if asm is None:
                # The optional key is not there, quit right here
                return
            if not asm:
                # If assembly code is empty, don't bother to add the table
                logger.debug(
                    f"Assembly for table '{table_tag}' is empty, "
                    "table not added to font."
                )
                return

            self.otf[table_tag] = table = ttLib.newTable(table_tag)
            table.program = ttLib.tables.ttProgram.Program()
            table.program.fromAssembly(asm)

    def compileGlyphInstructions(self, ttGlyph, name):
        """Compile the glyph instructions from the UFO glyph `name` to bytecode
        and add it to `ttGlyph`."""
        if name not in self.ufo:
            # Skip glyphs that are not in the UFO, e.g. '.notdef'
            logger.info(
                f"Skipping compilation of instructions for glyph '{name}' because it "
                "is not in the input UFO."
            )
            return

        glyph = self.ufo[name]
        ttdata = glyph.lib.get(TRUETYPE_INSTRUCTIONS_KEY, None)
        if ttdata is not None:
            self._compile_tt_glyph_program(glyph, ttGlyph, ttdata)
        if ttGlyph.isComposite():
            # Remove empty glyph programs from composite glyphs
            if hasattr(ttGlyph, "program") and not ttGlyph.program:
                logger.debug(f"Removing empty program from composite glyph '{name}'")
                delattr(ttGlyph, "program")
            self._set_composite_flags(glyph, ttGlyph)

    def _compile_tt_glyph_program(self, glyph, ttglyph, ttdata):
        self._check_tt_data_format(ttdata, f"glyph '{glyph.name}'")
        glyph_hash = ttdata.get("id", None)
        if not self._check_glyph_hash(glyph, ttglyph, glyph_hash, self.otf):
            return

        # Compile the glyph program
        asm = ttdata.get("assembly", None)
        if asm is None:
            # The "assembly" key is required.
            logger.error(
                f"Glyph assembly missing, glyph '{glyph.name}' will have "
                "no instructions in font."
            )
            return

        if not asm:
            # If the assembly code is empty, don't bother adding a program
            logger.debug(f"Glyph '{glyph.name}' has no instructions.")
            return

        ttglyph.program = ttLib.tables.ttProgram.Program()
        ttglyph.program.fromAssembly(asm)

    def _set_composite_flags(self, glyph, ttglyph):
        # Set component flags

        if len(ttglyph.components) != len(glyph.components):
            # May happen if nested components have been flattened by a filter
            logger.error(
                "Number of components differ between UFO and TTF "
                f"in glyph '{glyph.name}' ({len(glyph.components)} vs. "
                f"{len(ttglyph.components)}, not setting component flags from"
                "UFO. They may still be set heuristically."
            )
            return

        # We need to decide when to set the flags.
        # Let's assume if any lib key is not there, or the component
        # doesn't have an identifier, we should leave the flags alone.

        # Keep track of which component has the USE_MY_METRICS flag
        use_my_metrics_comp = None

        for i, c in enumerate(ttglyph.components):
            ufo_component_id = glyph.components[i].identifier
            if ufo_component_id is None:
                # No information about component flags is stored in the UFO,
                # use heuristics.

                # https://github.com/googlefonts/ufo2ft/pull/425 recommends
                # to always set the ROUND_XY_TO_GRID flag
                c.flags |= ROUND_XY_TO_GRID
            elif (
                OBJECT_LIBS_KEY in glyph.lib
                and ufo_component_id in glyph.lib[OBJECT_LIBS_KEY]
                and (
                    TRUETYPE_ROUND_KEY in glyph.lib[OBJECT_LIBS_KEY][ufo_component_id]
                    or TRUETYPE_METRICS_KEY
                    in glyph.lib[OBJECT_LIBS_KEY][ufo_component_id]
                )
            ):
                component_lib = glyph.lib[OBJECT_LIBS_KEY][ufo_component_id]

                # https://github.com/googlefonts/ufo2ft/pull/425 recommends
                # to always set the ROUND_XY_TO_GRID flag, so we only
                # unset it if explicitly done so in the lib
                if component_lib.get(TRUETYPE_ROUND_KEY, True):
                    logger.info("    ROUND_XY_TO_GRID")
                    c.flags |= ROUND_XY_TO_GRID
                else:
                    c.flags &= ~ROUND_XY_TO_GRID

                if not self.autoUseMyMetrics and component_lib.get(
                    TRUETYPE_METRICS_KEY, False
                ):
                    c.flags &= ~USE_MY_METRICS
                    if use_my_metrics_comp:
                        logger.warning(
                            "Ignoring USE_MY_METRICS flag on component "
                            f"'{ufo_component_id}' because it has been set on "
                            f"component '{use_my_metrics_comp}' already "
                            f"in glyph {glyph.name}."
                        )
                    else:
                        c.flags |= USE_MY_METRICS
                        use_my_metrics_comp = ufo_component_id

            if i == 0 and TRUETYPE_OVERLAP_KEY in glyph.lib:
                # Set OVERLAP_COMPOUND on the first component only
                if glyph.lib.get(TRUETYPE_OVERLAP_KEY, False):
                    c.flags |= OVERLAP_COMPOUND
                else:
                    c.flags &= ~OVERLAP_COMPOUND

    def update_maxp(self):
        """Update the maxp table with relevant values from the UFO and compiled
        font.
        """
        maxp = self.otf["maxp"]
        ttdata = self.ufo.lib.get(TRUETYPE_INSTRUCTIONS_KEY, None)
        if ttdata:
            for name in (
                "maxStorage",
                "maxFunctionDefs",
                "maxInstructionDefs",
                "maxStackElements",
                # "maxSizeOfInstructions",  # Is recalculated below
                "maxZones",
                "maxTwilightPoints",
            ):
                value = ttdata.get(name, None)
                if value is not None:
                    setattr(maxp, name, value)

        # Recalculate maxp.maxSizeOfInstructions
        sizes = [
            len(ttglyph.program.getBytecode())
            for ttglyph in self.otf["glyf"].glyphs.values()
            if hasattr(ttglyph, "program")
        ]
        maxp.maxSizeOfInstructions = max(sizes, default=0)

    def setupTable_cvt(self):
        """Make the cvt table."""
        cvts = []
        ttdata = self.ufo.lib.get(TRUETYPE_INSTRUCTIONS_KEY, None)
        if ttdata:
            self._check_tt_data_format(ttdata, "key 'controlValue'")
            cvt_dict = ttdata.get("controlValue", None)
            if cvt_dict:
                # Convert string keys to int
                cvt_dict = {int(k): v for k, v in cvt_dict.items()}
                # Find the maximum cvt index.
                # We can't just use the dict keys because the cvt must be
                # filled consecutively.
                max_cvt = max(cvt_dict.keys())
                # Make value list, filling entries for missing keys with 0
                cvts = [cvt_dict.get(i, 0) for i in range(max_cvt + 1)]

        if cvts:
            # Only write cvt to font if it contains any values
            self.otf["cvt "] = cvt = newTable("cvt ")
            cvt.values = array.array("h", cvts)

    def setupTable_fpgm(self):
        self._compile_program("fontProgram", "fpgm")

    def setupTable_prep(self):
        self._compile_program("controlValueProgram", "prep")
