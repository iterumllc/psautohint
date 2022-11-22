# Copyright 2022 Adobe. All rights reserved.

import argparse
from copy import deepcopy
import logging
import plistlib
import re
import sys

from fontTools.ttLib import TTFont
from fontTools.cffLib import FontDict, FDArrayIndex, PrivateDict, FDSelect
from fontTools.designspaceLib import DesignSpaceDocument
from afdko.fdkutils import (
    run_shell_command,
    get_temp_file_path,
    validate_path,
)

logger = logging.getLogger(__name__)


class ShellCommandError(Exception):
    pass


def replaceCFF(otfPath, tempFilePath):
    if not run_shell_command(['sfntedit', '-a',
                              f'CFF ={tempFilePath}', otfPath]):
        raise ShellCommandError


def get_options(args):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-m',
        '--metadata',
        dest='metaPath',
        type=validate_path,
        help='path to hinting metadata file',
        required=True
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-d',
        '--designspace',
        dest='dsPath',
        type=validate_path,
        help='path to design space file',
    )
    group.add_argument(
        '-f',
        '--fontpath',
        dest='fPath',
        type=validate_path,
        help='path to individual OTF font to modify',
    )
    options = parser.parse_args(args)

    return options


class dictspec():
    pass


def getDictmap(options):
    with open(options.metaPath, "rb") as dictmapfile:
        rawdictmap = plistlib.load(dictmapfile)

    dictmap = []
    for name, d in rawdictmap.items():
        dct = dictspec()
        dct.name = name
        dct.BluePairs = d.get("blue_pairs", [])
        dct.OtherPairs = d.get("other_blue_pairs", [])
        dct.SIV = d.get("stem_indexes_v", [])
        dct.SIH = d.get("stem_indexes_h", [])
        dct.re = []
        for r in d.get("regex", []):
            dct.re.append(re.compile(r))
        dictmap.append(dct)

    return dictmap


def remapDicts(fpath, dictmap):
    f = TTFont(fpath)
    if 'CFF ' not in f:
        logger.error("No CFF table in %s: will not modify" % fpath)
        return

    cff = f['CFF ']
    top = cff.cff[0]
    if hasattr(top, "FDArray"):
        logger.error("CFF table in %s already has " % fpath +
                     "%d private dicts: will not modify" % len(top.FDArray))
        return

    fdarray = top.FDArray = FDArrayIndex()
    origPriv = top.Private
    # Make sure info doesn't leak though in non-FDArray private dict
    top.Private = PrivateDict()

    for dictspec in dictmap:
        npr = deepcopy(origPriv)
        for bvals, fvals, indexes in [(getattr(npr, "BlueValues", None),
                                       getattr(npr, "FamilyBlues", None),
                                       dictspec.BluePairs),
                                      (getattr(npr, "OtherBlues", None),
                                       getattr(npr, "FamilyOtherBlues", None),
                                       dictspec.OtherPairs)]:
            nbvals = []
            nfvals = []
            for pi in sorted(indexes):
                if bvals:
                    nbvals.extend([bvals[2 * pi], bvals[2 * pi + 1]])
                if fvals:
                    nfvals.extend([fvals[2 * pi], fvals[2 * pi + 1]])
            if bvals:
                bvals[:] = nbvals
            if fvals:
                fvals[:] = nfvals
        snapvs = getattr(npr, "StemSnapV", None)
        if snapvs is not None:
            npr.StemSnapV[:] = [snapvs[i] for i in dictspec.SIV]
        snaphs = getattr(npr, "StemSnapH", None)
        if snaphs is not None:
            npr.StemSnapH[:] = [snaphs[i] for i in dictspec.SIH]

        fontDict = FontDict()
        fontDict.setCFF2(True)
        fontDict.Private = npr
        fdarray.append(fontDict)

    # add no-zone dictionary for other glyphs (leave stem sizes)
    bbox = top.FontBBox
    font_max = bbox[3]
    font_min = bbox[1]
    npr = deepcopy(origPriv)
    npr.BlueValues[:] = [font_min - 100, font_min - 85,
                         font_max + 85, font_max + 100]
    if hasattr(npr, "FamilyBlues"):
        npr.FamilyBlues[:] = [font_min - 100, font_min - 85,
                              font_max + 85, font_max + 100]
    if hasattr(npr, "OtherBlues"):
        npr.OtherBlues[:] = []
    if hasattr(npr, "FamilyOtherBlues"):
        npr.FamilyOtherBlues[:] = []
    fontDict = FontDict()
    fontDict.setCFF2(True)
    fontDict.Private = npr
    fdarray.append(fontDict)

    fdselect = top.FDSelect = FDSelect()
    for gn in cff.getGlyphOrder():
        done = False
        for i, dictspec in enumerate(dictmap):
            for r in dictspec.re:
                if r.search(gn):
                    fdselect.append(i)
                    done = True
                    break
            if done:
                break
        if not done:
            fdselect.append(i + 1)

    logger.warning("Updating %s with %d dictionaries" % (fpath, i + 2))
    cfftpath = get_temp_file_path()
    cfftfile = open(cfftpath, "w+b")
    cff.cff.compile(cfftfile, f, False)
    cfftfile.close()
    f.close()

    replaceCFF(fpath, cfftpath)


def main(args=None):
    options = get_options(args)

    dictmap = getDictmap(options)

    if options.dsPath is not None:
        ds = DesignSpaceDocument.fromfile(options.dsPath)
        otf_paths = [s.path.replace('.ufo', '.otf') for s in ds.sources]
    else:
        otf_paths = [options.fPath]

    for p in otf_paths:
        remapDicts(p, dictmap)

    return 0


if __name__ == "__main__":
    sys.exit(main())
