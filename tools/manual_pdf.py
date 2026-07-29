#!/usr/bin/env python3
"""manual_pdf.py -- render docs/manual*.md as period game manuals.

A deliberately small markdown subset (the manuals only use it):
  # ## ###   headings        - bullet   | tables |
  > quote    --- rule        **bold** `code` inline
Everything is laid out in the two-colour, boxed-table look of a mid-80s
Amstrad manual.  DejaVu carries the Greek.

Usage:  python3 tools/manual_pdf.py docs/manual.md docs/manual.pdf
"""
import re
import sys

from fpdf import FPDF

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
INK = (24, 24, 28)
ACCENT = (176, 28, 32)          # the red of a Gremlin/Ocean header rule
GREY = (110, 112, 120)
PANEL = (238, 236, 230)


class Manual(FPDF):
    title_line = ""

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Dj", "", 7.5)
        self.set_text_color(*GREY)
        self.cell(0, 5, self.title_line, align="L")
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.4)
        self.line(self.l_margin, 14, self.w - self.r_margin, 14)
        self.ln(8)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-13)
        self.set_font("Dj", "", 7.5)
        self.set_text_color(*GREY)
        self.cell(0, 5, str(self.page_no()), align="C")


def inline(pdf, text, size, style="", colour=INK, height=5.2):
    """Write a paragraph honouring **bold** and `code` runs."""
    parts = re.split(r"(\*\*.+?\*\*|`.+?`)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            pdf.set_font("Dj", "B", size)
            pdf.set_text_color(*INK)
            pdf.write(height, part[2:-2])
        elif part.startswith("`") and part.endswith("`"):
            pdf.set_font("DjMono", "", size - 0.5)
            pdf.set_text_color(*ACCENT)
            pdf.write(height, part[1:-1])
        else:
            pdf.set_font("Dj", style, size)
            pdf.set_text_color(*colour)
            pdf.write(height, part)
    pdf.ln(height)


def plain(text):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "")


def table(pdf, rows):
    """rows[0] is the header; columns are sized by their content."""
    avail = pdf.w - pdf.l_margin - pdf.r_margin
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    # A column may never be narrower than its longest unbreakable word,
    # or headings hyphen-stack into "Xei/rist/erio".  Measure with the
    # real font, hand the floor to every column, then share what is left
    # in proportion to how much text each column actually carries.
    pdf.set_font("Dj", "B", 8.5)
    floors, weights = [], []
    for c in range(ncol):
        widest = 0
        for r in rows:
            for word in plain(r[c]).split():
                widest = max(widest, pdf.get_string_width(word))
        floors.append(min(widest + 3.4, avail * 0.45))
        weights.append(max(sum(len(plain(r[c])) for r in rows), 1))
    spare = avail - sum(floors)
    if spare < 0:                        # floors alone overflow: scale down
        k = avail / sum(floors)
        widths = [f * k for f in floors]
    else:
        total = sum(weights)
        widths = [f + spare * w / total for f, w in zip(floors, weights)]

    for ri, row in enumerate(rows):
        pdf.set_font("Dj", "B" if ri == 0 else "", 8.5)
        heights = []
        for c, cell in enumerate(row):
            heights.append(len(pdf.multi_cell(
                widths[c], 4.6, plain(cell), dry_run=True, output="LINES")))
        h = max(heights) * 4.6 + 1.6
        if pdf.get_y() + h > pdf.h - 20:
            pdf.add_page()
            pdf.set_font("Dj", "B" if ri == 0 else "", 8.5)
        y0, x = pdf.get_y(), pdf.l_margin
        for c, cell in enumerate(row):
            pdf.set_xy(x, y0)
            if ri == 0:
                pdf.set_fill_color(*ACCENT)
                pdf.set_text_color(255, 255, 255)
            else:
                pdf.set_fill_color(*(PANEL if ri % 2 else (255, 255, 255)))
                pdf.set_text_color(*INK)
            pdf.cell(widths[c], h, "", border=0, fill=True)
            pdf.set_xy(x + 1.4, y0 + 0.8)
            pdf.multi_cell(widths[c] - 2.8, 4.6, plain(cell), align="L")
            x += widths[c]
        pdf.set_xy(pdf.l_margin, y0 + h)
    pdf.set_draw_color(*GREY)
    pdf.ln(3)


def cover(pdf, lines):
    """lines: [title, subtitle, publisher]"""
    pdf.add_page()
    pdf.set_fill_color(18, 18, 24)
    pdf.rect(0, 0, pdf.w, pdf.h, "F")
    H = pdf.h
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, H * 0.34, pdf.w, 2.6, "F")
    pdf.rect(0, H * 0.52, pdf.w, 2.6, "F")
    size = 40
    pdf.set_font("Dj", "B", size)
    while pdf.get_string_width(lines[0]) > pdf.w - 20 and size > 14:
        size -= 1
        pdf.set_font("Dj", "B", size)
    pdf.set_xy(0, H * 0.38)
    pdf.set_font("Dj", "B", size)
    pdf.set_text_color(250, 208, 42)
    pdf.cell(pdf.w, H * 0.12, lines[0], align="C")
    pdf.set_xy(0, H * 0.555)
    pdf.set_font("Dj", "", 11)
    pdf.set_text_color(120, 200, 226)
    pdf.cell(pdf.w, 8, lines[1], align="C")
    pdf.set_xy(0, H * 0.84)
    pdf.set_font("Dj", "B", 12)
    pdf.set_text_color(240, 240, 235)
    pdf.cell(pdf.w, 7, lines[2], align="C")
    pdf.set_xy(0, H * 0.885)
    pdf.set_font("Dj", "", 9)
    pdf.set_text_color(150, 152, 160)
    pdf.cell(pdf.w, 6, lines[3], align="C")


def render(src, dst):
    md = open(src, encoding="utf-8").read().split("\n")
    pdf = Manual(format="A5")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(15, 16, 15)
    for style, name in (("", "DejaVuSans.ttf"), ("B", "DejaVuSans-Bold.ttf"),
                        ("I", "DejaVuSans-Oblique.ttf")):
        pdf.add_font("Dj", style, f"{FONT_DIR}/{name}")
    pdf.add_font("DjMono", "", f"{FONT_DIR}/DejaVuSansMono.ttf")

    heads = [l for l in md if l.startswith("# ")]
    subs = [l for l in md if l.startswith("### ")]
    pdf.title_line = plain(heads[0][2:]) if heads else ""
    cover(pdf, [plain(heads[0][2:]),
                plain(subs[0][4:]) if subs else "",
                "REVIVE8BIT SOFTWARE", "2026"])
    pdf.add_page()

    i, first_h1_seen = 0, False
    while i < len(md):
        line = md[i].rstrip()

        if line.startswith("|"):                      # ---- table
            rows = []
            while i < len(md) and md[i].startswith("|"):
                cells = [c.strip() for c in md[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") and c for c in cells):
                    rows.append(cells)
                i += 1
            table(pdf, rows)
            continue

        if line.startswith(">"):                      # ---- boxed notice
            block = []
            while i < len(md) and md[i].startswith(">"):
                block.append(md[i][1:].strip())
                i += 1
            while block and not block[-1]:
                block.pop()
            # fold each run of lines into one paragraph: a **bold** span
            # may straddle two source lines, and only whole paragraphs
            # wrap to the box width instead of the author's line breaks
            folded, buf = [], ""
            for b in block + [""]:
                if b.startswith("## ") or not b:
                    if buf:
                        folded.append(buf)
                        buf = ""
                    folded.append(b)
                else:
                    buf = f"{buf} {b}".strip()
            block = folded
            y0 = pdf.get_y() + 2
            if y0 > pdf.h - 70:
                pdf.add_page()
                y0 = pdf.get_y() + 2
            pdf.set_xy(pdf.l_margin + 5, y0 + 5)
            saved_l, saved_r = pdf.l_margin, pdf.r_margin
            pdf.set_margins(saved_l + 5, pdf.t_margin, saved_r + 5)
            for b in block:
                if not b:
                    pdf.ln(2.4)
                elif b.startswith("## "):
                    pdf.set_x(pdf.l_margin)
                    inline(pdf, b[3:], 12, "B", ACCENT, 6)
                    pdf.ln(1.2)
                else:
                    pdf.set_x(pdf.l_margin)
                    inline(pdf, b, 9.4, "", INK, 5.0)
            pdf.set_margins(saved_l, pdf.t_margin, saved_r)
            y1 = pdf.get_y() + 4
            pdf.set_draw_color(*ACCENT)
            pdf.set_line_width(0.7)
            pdf.rect(saved_l, y0, pdf.w - saved_l - saved_r, y1 - y0)
            pdf.set_xy(saved_l, y1 + 4)
            continue

        if line.startswith("### "):
            inline(pdf, line[4:], 10, "B", GREY, 5.5)
        elif line.startswith("## "):
            if pdf.get_y() > pdf.h - 46:
                pdf.add_page()
            pdf.ln(2)
            inline(pdf, line[3:], 15, "B", ACCENT, 8)
            pdf.set_draw_color(*ACCENT)
            pdf.set_line_width(0.5)
            y = pdf.get_y() - 1
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)
        elif line.startswith("# "):
            if first_h1_seen:
                pdf.add_page()
            first_h1_seen = True
            inline(pdf, line[2:], 22, "B", INK, 11)
        elif line.startswith("- "):
            body = line[2:]
            while i + 1 < len(md) and md[i + 1].startswith("  ") \
                    and md[i + 1].strip():
                i += 1
                body += " " + md[i].strip()
            x = pdf.get_x()
            pdf.set_font("Dj", "B", 10)
            pdf.set_text_color(*ACCENT)
            pdf.cell(4, 5.2, "▪")
            saved = pdf.l_margin
            pdf.set_margins(saved + 4, pdf.t_margin, pdf.r_margin)
            pdf.set_x(saved + 4)
            inline(pdf, body, 9.6)
            pdf.set_margins(saved, pdf.t_margin, pdf.r_margin)
            pdf.set_x(x)
        elif line.startswith("---"):
            pdf.ln(1.5)
            pdf.set_draw_color(*GREY)
            pdf.set_line_width(0.2)
            y = pdf.get_y()
            pdf.line(pdf.l_margin + 26, y, pdf.w - pdf.r_margin - 26, y)
            pdf.ln(4)
        elif not line:
            pdf.ln(2.6)
        else:                                          # ---- paragraph
            body = line
            while i + 1 < len(md) and md[i + 1].strip() \
                    and not re.match(r"^(#|-\s|\||>|---)", md[i + 1]):
                i += 1
                body += " " + md[i].strip()
            inline(pdf, body, 9.6)
        i += 1

    pdf.output(dst)
    print(f"{dst}  ({pdf.page_no()} pages)")


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2])
