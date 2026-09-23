#!/usr/bin/env python3
"""
build_reference.py — generate applications/ats-reference.docx, the pandoc style
template used for every ATS .docx. Goal: still 100% ATS-safe (single column, real
text, standard headings, standard fonts, no text boxes / layout tables) BUT
professional-looking when a recruiter opens the file after the ATS parses it.

pandoc maps our ats.md like this:
  # Name        -> Heading 1   (styled: large, bold, navy, centered)
  ## Section    -> Heading 2   (styled: bold, navy, uppercase, bottom rule)
  ### Job/title -> Heading 3   (styled: bold, dark gray)
  - bullet      -> List Bullet
  paragraph     -> Normal / First Paragraph / Body Text

Run:  python build_reference.py   (writes ats-reference.docx next to this script)
Then build.py picks it up automatically via --reference-doc.
"""
import os
import subprocess
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from jobpilot.core.paths import APPLICATIONS as APP_DIR  # noqa: E402
REF = os.path.join(APP_DIR, "ats-reference.docx")

BODY_FONT = "Calibri"          # ubiquitous, clean, parses everywhere
NAVY = RGBColor(0x1F, 0x3B, 0x57)
DARK = RGBColor(0x33, 0x33, 0x33)
GRAY = RGBColor(0x44, 0x44, 0x44)


def get_style(styles, name):
    """Look up a style by DISPLAY NAME (subscript matches on id, which differs)."""
    for s in styles:
        if s.name == name:
            return s
    return None


def set_font(style, name=BODY_FONT, size=None, bold=None, color=None,
             caps=False, upper=False):
    f = style.font
    f.name = name
    # ensure east-asian/complex-script also use the same font
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), name)
    if size is not None:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if color is not None:
        f.color.rgb = color
    if caps:
        e = OxmlElement("w:smallCaps"); rpr.append(e)
    if upper:
        e = OxmlElement("w:caps"); rpr.append(e)


def para_spacing(style, before=0, after=4, line=1.05):
    pf = style.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line


def bottom_border(style, color="1F3B57", size=6):
    """Add a bottom rule under a heading style."""
    pPr = style.element.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    pPr.append(pbdr)


def main():
    # 1. start from pandoc's own default reference so every style pandoc needs exists
    default = os.path.join(APP_DIR, ".pandoc-default-reference.docx")
    with open(default, "wb") as f:
        subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"],
                       stdout=f, check=True)

    doc = Document(default)
    styles = doc.styles

    # 2. page margins ~0.7"
    for section in doc.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    # 3. body text
    for name in ("Normal", "Body Text", "First Paragraph", "Compact"):
        st = get_style(styles, name)
        if st is not None:
            set_font(st, size=10.5, color=DARK)
            para_spacing(st, before=0, after=4, line=1.05)

    # 4. Name (Heading 1) — large, bold, navy, centered
    h1 = get_style(styles, "Heading 1")
    set_font(h1, size=22, bold=True, color=NAVY)
    para_spacing(h1, before=0, after=2, line=1.0)
    h1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 5. Section headings (Heading 2) — bold navy, UPPERCASE, bottom rule
    h2 = get_style(styles, "Heading 2")
    set_font(h2, size=12, bold=True, color=NAVY, upper=True)
    para_spacing(h2, before=10, after=3, line=1.0)
    bottom_border(h2)

    # 6. Job/subsection (Heading 3) — bold dark gray
    h3 = get_style(styles, "Heading 3")
    set_font(h3, size=11, bold=True, color=GRAY)
    para_spacing(h3, before=6, after=1, line=1.0)

    # 7. bullets — tight
    for name in ("List Bullet",):
        st = get_style(styles, name)
        if st is not None:
            para_spacing(st, before=0, after=2, line=1.03)
            set_font(st, size=10.5, color=DARK)

    doc.save(REF)
    os.remove(default)
    print(f"wrote {REF}")


if __name__ == "__main__":
    main()
