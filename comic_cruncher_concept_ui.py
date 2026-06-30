"""
Comic Cruncher - UI Concept
Modern dark-themed interface with cover art visualization.
Covers fill from grayscale to color as files are processed.

Uses the existing backend from comic_cruncher.py.
Run: python comic_cruncher_concept_ui.py
"""

import sys
import os
import re
from pathlib import Path
from PIL import Image
import io

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QScrollArea, QGridLayout, QPushButton, QStackedWidget,
    QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect
from PyQt6.QtGui import (
    QFont, QDragEnterEvent, QDropEvent, QPainter, QPixmap, QImage,
    QColor, QPen
)

# Add script directory to path for backend imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comic_cruncher import (
    ComicProcessor, BatchProcessor, ComicCombiner,
    ComicUtils, OPENCV_AVAILABLE, _POPPLER_PATH
)


# ═══════════════════════════════════════════════════════════════
# Color Palette — GitHub Dark + Google Material Accents
# ═══════════════════════════════════════════════════════════════

BG          = "#0d1117"
SURFACE     = "#161b22"
ELEVATED    = "#21262d"
HOVER       = "#30363d"
BORDER      = "#21262d"
BORDER_LT   = "#30363d"
TXT         = "#e6edf3"
TXT_DIM     = "#8b949e"
TXT_MUTED   = "#484f58"
BLUE        = "#4285f4"
GREEN       = "#34a853"
RED         = "#ea4335"
YELLOW      = "#fbbc04"

COVER_W_DEFAULT = 150
COVER_H_DEFAULT = 220
CELL_W_DEFAULT  = 170
CELL_H_DEFAULT  = 275

COVER_W     = COVER_W_DEFAULT
COVER_H     = COVER_H_DEFAULT
CELL_W      = CELL_W_DEFAULT
CELL_H      = CELL_H_DEFAULT

ZOOM_STEPS  = [0.6, 0.8, 1.0, 1.25, 1.5]
ZOOM_IDX_DEFAULT = 2  # 1.0x


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def extract_cover_image(file_path):
    """Extract the first image from a comic file as a PIL Image."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext in ('.cbz', '.cbr'):
        try:
            archive, _ = ComicUtils._open_archive(path)
            with archive:
                names = sorted(
                    n for n in archive.namelist()
                    if ComicUtils.is_image_file(n)
                )
                if names:
                    data = archive.read(names[0])
                    return Image.open(io.BytesIO(data)).convert('RGB')
        except Exception:
            return None

    elif ext == '.pdf':
        try:
            import pdf2image
            images = pdf2image.convert_from_path(
                str(path), dpi=150,
                first_page=1, last_page=1,
                poppler_path=_POPPLER_PATH
            )
            return images[0].convert('RGB') if images else None
        except Exception:
            return None

    return None


def pil_to_qimage(pil_img):
    """Convert a PIL Image to a QImage (thread-safe)."""
    if pil_img.mode != 'RGB':
        pil_img = pil_img.convert('RGB')
    w, h = pil_img.size
    data = pil_img.tobytes('raw', 'RGB')
    return QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def grayscale_qimage(qimg):
    """Return a grayscale copy of a QImage."""
    gray = qimg.convertToFormat(QImage.Format.Format_Grayscale8)
    return gray.convertToFormat(QImage.Format.Format_RGB888)


def clean_filename(filepath):
    """Clean scanner tags, format tags, and bracket junk from a comic filename.
    Keeps: year (YYYY), issue/volume info like (Issues 1-9).
    Returns the new full path (same directory, cleaned stem, same extension).
    """
    p = Path(filepath)
    stem = p.stem
    ext = p.suffix

    # Remove square bracket content entirely: [DC-Piranha Press 1992]
    stem = re.sub(r'\[.*?\]', '', stem)

    # Remove parenthetical tags EXCEPT years and "Issues X-Y"
    def _keep(m):
        inner = m.group(1).strip()
        if re.match(r'^(19|20)\d{2}$', inner):
            return m.group(0)
        if re.match(r'^Issues?\s+\d', inner, re.IGNORECASE):
            return m.group(0)
        return ''

    stem = re.sub(r'\(([^)]*)\)', _keep, stem)

    # Remove standalone "c2c" (scan type)
    stem = re.sub(r'\bc2c\b', '', stem, flags=re.IGNORECASE)

    # Collapse whitespace, strip trailing dashes/dots/spaces
    stem = re.sub(r'\s{2,}', ' ', stem).strip()
    stem = re.sub(r'[\s\-\.]+$', '', stem)
    stem = stem.strip()

    return str(p.with_name(stem + ext))


# ═══════════════════════════════════════════════════════════════
# Cover Extractor Thread
# ═══════════════════════════════════════════════════════════════

class CoverExtractor(QThread):
    cover_ready = pyqtSignal(int, QImage, QImage)   # index, color, gray
    all_done = pyqtSignal()

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths

    def run(self):
        for i, fp in enumerate(self.file_paths):
            pil_img = extract_cover_image(fp)
            if pil_img:
                pil_img.thumbnail((300, 450), Image.Resampling.LANCZOS)
                color = pil_to_qimage(pil_img)
                gray = grayscale_qimage(color)
                self.cover_ready.emit(i, color, gray)
            else:
                ph = QImage(COVER_W, COVER_H, QImage.Format.Format_RGB888)
                ph.fill(QColor(ELEVATED))
                self.cover_ready.emit(i, ph, ph)
        self.all_done.emit()


# ═══════════════════════════════════════════════════════════════
# Top Progress Bar  (thin 3 px line spanning full width)
# ═══════════════════════════════════════════════════════════════

class TopProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._pct = 0
        self._visible = False
        self._color = QColor(BLUE)

    def set_progress(self, v):
        self._pct = max(0, min(100, v))
        self._visible = True
        self.update()

    def set_complete(self):
        self._pct = 100
        self._color = QColor(GREEN)
        self.update()
        QTimer.singleShot(2500, self._hide)

    def _hide(self):
        self._visible = False
        self._pct = 0
        self._color = QColor(BLUE)
        self.update()

    def paintEvent(self, _event):
        if not self._visible:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        if self._pct > 0:
            w = int(self.width() * self._pct / 100)
            p.fillRect(0, 0, w, self.height(), self._color)


# ═══════════════════════════════════════════════════════════════
# Cover Widget — grayscale ➜ color fill from bottom
# ═══════════════════════════════════════════════════════════════

class CoverWidget(QWidget):
    clicked = pyqtSignal(int)  # index
    move_left = pyqtSignal(int)   # index
    move_right = pyqtSignal(int)  # index

    def __init__(self, filename, index, parent=None):
        super().__init__(parent)
        self.setFixedSize(CELL_W, CELL_H)
        self.filename = filename
        self.index = index
        self._clickable = False
        self._reorderable = False
        self._position = 0  # 1-based display position
        self._arrow_left_rect = QRect()
        self._arrow_right_rect = QRect()

        self.color_px = None
        self.gray_px = None
        self.progress = 0.0
        self._target = 0.0
        self.status = 'pending'
        self._flash = False

        self._anim = QTimer()
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)

        self._flash_timer = QTimer()
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

    # ── public API ──────────────────────────────────────────

    def set_images(self, color_qi, gray_qi):
        self.color_px = QPixmap.fromImage(color_qi).scaled(
            COVER_W, COVER_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.gray_px = QPixmap.fromImage(gray_qi).scaled(
            COVER_W, COVER_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.update()

    def set_progress(self, v):
        self._target = max(0.0, min(100.0, v))
        if self.status == 'pending':
            self.status = 'processing'
        if not self._anim.isActive():
            self._anim.start()

    def set_active(self):
        self.status = 'active'
        self.update()

    def set_completed(self):
        self._target = 100.0
        self.progress = 100.0
        self.status = 'completed'
        self._anim.stop()
        self._flash = True
        self._flash_timer.start(300)
        self.update()

    def _clear_flash(self):
        self._flash = False
        self.update()

    def set_skipped(self):
        self._target = 100.0
        self.progress = 100.0
        self.status = 'skipped'
        self._anim.stop()
        self.update()

    def set_error(self):
        self.status = 'error'
        self._anim.stop()
        self.update()

    def set_deleted(self):
        self._target = 0.0
        self.progress = 0.0
        self.status = 'deleted'
        self._clickable = False
        self._anim.stop()
        self.update()

    def mousePressEvent(self, event):
        pos = event.pos()
        if self._reorderable:
            if self._arrow_left_rect.contains(pos):
                self.move_left.emit(self.index)
                return
            if self._arrow_right_rect.contains(pos):
                self.move_right.emit(self.index)
                return
        if self._clickable and self.status != 'deleted':
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    # ── animation ───────────────────────────────────────────

    def _tick(self):
        diff = self._target - self.progress
        if abs(diff) < 0.4:
            self.progress = self._target
            self._anim.stop()
        else:
            self.progress += diff * 0.18
        self.update()

    # ── painting ────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        cx = (self.width() - COVER_W) // 2
        cover_rect = QRect(cx, 0, COVER_W, COVER_H)

        if not self.color_px:
            p.fillRect(cover_rect, QColor(ELEVATED))
            p.setPen(QColor(TXT_MUTED))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter, "...")
        else:
            aw, ah = self.gray_px.width(), self.gray_px.height()
            px = cx + (COVER_W - aw) // 2
            py = (COVER_H - ah) // 2
            pix = QRect(px, py, aw, ah)

            # grayscale base
            p.drawPixmap(pix, self.gray_px)

            # color reveal from bottom — clean hard edge
            reveal = int(ah * self.progress / 100)
            if reveal > 0:
                sy = ah - reveal
                src = QRect(0, sy, aw, reveal)
                dst = QRect(px, py + sy, aw, reveal)
                p.drawPixmap(dst, self.color_px, src)

                # thin 1px line at boundary
                if 0 < self.progress < 100:
                    ly = py + sy
                    p.setPen(QPen(QColor(BLUE), 1))
                    p.drawLine(px, ly, px + aw - 1, ly)

        # --- white flash on completion ---
        if self._flash:
            flash_c = QColor(255, 255, 255, 100)
            p.fillRect(cover_rect, flash_c)

        # --- dark overlay for deleted covers ---
        if self.status == 'deleted':
            p.fillRect(cover_rect, QColor(0, 0, 0, 160))
            p.setPen(QColor(RED))
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter, "DELETED")

        # --- hover cursor for clickable covers ---
        if self._clickable and self.status != 'deleted':
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        # --- border: left accent stripe for active/completed states ---
        # outer border always subtle
        p.setPen(QPen(QColor(BORDER_LT), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cover_rect.adjusted(0, 0, -1, -1))

        # 3px left accent stripe
        stripe_colors = {
            'completed': GREEN, 'processing': BLUE,
            'active': BLUE, 'error': RED, 'skipped': YELLOW,
            'deleted': RED,
        }
        sc = stripe_colors.get(self.status)
        if sc:
            p.fillRect(QRect(cx, 0, 3, COVER_H), QColor(sc))

        # --- filename + status text ---
        name = Path(self.filename).stem
        p.setFont(QFont("Segoe UI", 8))
        fm = p.fontMetrics()
        elided = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, self.width() - 6)

        # filename always light gray
        p.setPen(QColor(TXT_DIM))
        text_r = QRect(0, COVER_H + 8, self.width(), 16)
        p.drawText(text_r, Qt.AlignmentFlag.AlignHCenter, elided)

        # status line below filename
        status_r = QRect(0, COVER_H + 24, self.width(), 16)
        p.setFont(QFont("Segoe UI", 8))
        if self._reorderable and self._position > 0:
            # ◄  #  ► reorder controls
            mid_x = self.width() // 2
            arrow_w, arrow_h = 20, 16
            num_text = str(self._position)

            # left arrow
            self._arrow_left_rect = QRect(mid_x - 30, COVER_H + 24, arrow_w, arrow_h)
            p.setPen(QColor(TXT_MUTED))
            p.fillRect(self._arrow_left_rect, QColor(ELEVATED))
            p.drawText(self._arrow_left_rect, Qt.AlignmentFlag.AlignCenter, "\u25C4")

            # position number
            p.setPen(QColor(BLUE))
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            p.drawText(status_r, Qt.AlignmentFlag.AlignCenter, num_text)

            # right arrow
            self._arrow_right_rect = QRect(mid_x + 10, COVER_H + 24, arrow_w, arrow_h)
            p.setPen(QColor(TXT_MUTED))
            p.setFont(QFont("Segoe UI", 8))
            p.fillRect(self._arrow_right_rect, QColor(ELEVATED))
            p.drawText(self._arrow_right_rect, Qt.AlignmentFlag.AlignCenter, "\u25BA")

            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.status in ('active', 'processing') and 0 < self.progress < 100:
            p.setPen(QColor(BLUE))
            p.drawText(status_r, Qt.AlignmentFlag.AlignHCenter, f"{int(self.progress)}%")
        elif self.status == 'completed':
            p.setPen(QColor(GREEN))
            p.drawText(status_r, Qt.AlignmentFlag.AlignHCenter, "Done")
        elif self.status == 'skipped':
            p.setPen(QColor(YELLOW))
            p.drawText(status_r, Qt.AlignmentFlag.AlignHCenter, "Skipped")
        elif self.status == 'error':
            p.setPen(QColor(RED))
            p.drawText(status_r, Qt.AlignmentFlag.AlignHCenter, "Error")
        elif self.status == 'deleted':
            p.setPen(QColor(RED))
            p.drawText(status_r, Qt.AlignmentFlag.AlignHCenter, "Deleted")


# ═══════════════════════════════════════════════════════════════
# Drag & Drop Zone
# ═══════════════════════════════════════════════════════════════

class DragDropZone(QWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._hover = False

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hover = True
            self.update()

    def dragLeaveEvent(self, _event):
        self._hover = False
        self.update()

    def dropEvent(self, event: QDropEvent):
        self._hover = False
        self.update()
        paths = []
        for url in event.mimeData().urls():
            fp = url.toLocalFile()
            if os.path.isdir(fp):
                for root, _, files in os.walk(fp):
                    for f in files:
                        full = os.path.join(root, f)
                        if full.lower().endswith(('.pdf', '.cbz', '.cbr')):
                            paths.append(full)
            elif fp.lower().endswith(('.pdf', '.cbz', '.cbr')):
                paths.append(fp)
        if paths:
            paths.sort()
            self.files_dropped.emit(paths)

    def paintEvent(self, _event):
        p = QPainter(self)
        m = 60
        rect = self.rect().adjusted(m, m, -m, -m)

        # single-pixel border, solid
        bc = QColor(BLUE) if self._hover else QColor(BORDER_LT)
        p.setPen(QPen(bc, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

        if self._hover:
            p.fillRect(rect.adjusted(1, 1, -1, -1), QColor(66, 133, 244, 8))

        cy = rect.center().y()

        # simple down-arrow made of two lines
        ax = rect.center().x()
        ay = cy - 32
        arrow_c = QColor(BLUE) if self._hover else QColor(TXT_MUTED)
        p.setPen(QPen(arrow_c, 2))
        p.drawLine(ax, ay, ax, ay + 28)
        p.drawLine(ax - 10, ay + 18, ax, ay + 28)
        p.drawLine(ax + 10, ay + 18, ax, ay + 28)

        # main text
        p.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        p.setPen(QColor(TXT) if self._hover else QColor(TXT_DIM))
        p.drawText(QRect(rect.x(), cy + 12, rect.width(), 28),
                   Qt.AlignmentFlag.AlignHCenter, "Drop Comics Here")

        # subtitle
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(TXT_MUTED))
        p.drawText(QRect(rect.x(), cy + 40, rect.width(), 20),
                   Qt.AlignmentFlag.AlignHCenter, "CBZ  \u00b7  CBR  \u00b7  PDF")


# ═══════════════════════════════════════════════════════════════
# Result Item
# ═══════════════════════════════════════════════════════════════

class ResultItem(QFrame):
    def __init__(self, filename, status, detail, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(0)

        # 3px left accent bar
        accent_colors = {'completed': GREEN, 'skipped': YELLOW, 'error': RED}
        bar = QFrame()
        bar.setFixedSize(3, 20)
        bar.setStyleSheet(
            f"background-color: {accent_colors.get(status, TXT_MUTED)}; border: none;"
        )
        lay.addWidget(bar)
        lay.addSpacing(14)

        # filename
        lbl = QLabel(Path(filename).name)
        lbl.setFont(QFont("Segoe UI", 9))
        lbl.setStyleSheet(f"color: {TXT}; border: none;")
        lay.addWidget(lbl, stretch=1)

        # detail (right-aligned, muted)
        det = QLabel(detail)
        det.setFont(QFont("Segoe UI", 9))
        det.setStyleSheet(f"color: {TXT_DIM}; border: none;")
        det.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        det.setMinimumWidth(120)
        lay.addWidget(det)


# ═══════════════════════════════════════════════════════════════
# Results Panel
# ═══════════════════════════════════════════════════════════════

SCROLL_STYLE = f"""
    QScrollArea {{ background-color: {SURFACE}; border: none; }}
    QScrollBar:vertical {{
        background-color: {BG}; width: 8px; border: none;
    }}
    QScrollBar::handle:vertical {{
        background-color: {ELEVATED}; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: {HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(50)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # header
        hdr = QFrame()
        hdr.setFixedHeight(34)
        hdr.setStyleSheet(f"background-color: {BG}; border-bottom: 1px solid {BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 0, 20, 0)

        t = QLabel("Results")
        t.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        t.setStyleSheet(f"color: {TXT_DIM}; border: none;")
        hl.addWidget(t)

        self.count_lbl = QLabel("")
        self.count_lbl.setFont(QFont("Segoe UI", 9))
        self.count_lbl.setStyleSheet(f"color: {TXT_MUTED}; border: none;")
        hl.addWidget(self.count_lbl, alignment=Qt.AlignmentFlag.AlignRight)
        outer.addWidget(hdr)

        # scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_STYLE)

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background-color: {SURFACE};")
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addStretch()

        scroll.setWidget(self._inner)
        outer.addWidget(scroll)
        self._scroll = scroll
        self._n = 0

    def add_result(self, filename, status, detail):
        row = ResultItem(filename, status, detail)
        # alternating row tint
        if self._n % 2 == 1:
            row.setStyleSheet(
                f"background-color: {BG}; border-bottom: 1px solid {BORDER};"
            )
        else:
            row.setStyleSheet(
                f"background-color: {SURFACE}; border-bottom: 1px solid {BORDER};"
            )
        self._lay.insertWidget(self._n, row)
        self._n += 1
        self.count_lbl.setText(f"{self._n} file{'s' if self._n != 1 else ''}")
        QTimer.singleShot(50, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self):
        while self._n > 0:
            item = self._lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
            self._n -= 1
        self.count_lbl.setText("")


# ═══════════════════════════════════════════════════════════════
# Main Window
# ═══════════════════════════════════════════════════════════════

STAGE_WEIGHTS = {
    "EXTRACTING": (0, 15),
    "RESIZING":   (15, 95),
    "PACKAGING":  (95, 100),
}


class ConceptUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comic Cruncher")
        self.setMinimumSize(700, 500)
        self.resize(1100, 800)
        self.setStyleSheet(f"background-color: {BG};")

        self.processor = None
        self.cover_extractor = None
        self.current_mode = "cruncher"
        self.file_paths = []
        self.file_sizes = {}
        self.covers = []
        self.active_idx = -1
        self.processing = False
        self._zoom_idx = ZOOM_IDX_DEFAULT
        self.btn_combine_go = None

        cw = QWidget()
        self.setCentralWidget(cw)
        ml = QVBoxLayout(cw)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self.top_bar = TopProgressBar()
        ml.addWidget(self.top_bar)

        ml.addWidget(self._build_header())

        ml.addWidget(self._divider())

        # content area
        self.stack = QStackedWidget()
        self.drop_zone = DragDropZone()
        self.drop_zone.files_dropped.connect(self._on_files_dropped)

        self.cover_scroll = QScrollArea()
        self.cover_scroll.setWidgetResizable(True)
        self.cover_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cover_scroll.setStyleSheet(SCROLL_STYLE.replace(SURFACE, BG))

        self.cover_container = QWidget()
        self.cover_container.setStyleSheet(f"background-color: {BG};")
        self.cover_grid = QGridLayout(self.cover_container)
        self.cover_grid.setContentsMargins(20, 20, 20, 20)
        self.cover_grid.setSpacing(10)
        self.cover_scroll.setWidget(self.cover_container)

        self.stack.addWidget(self.drop_zone)
        self.stack.addWidget(self.cover_scroll)
        ml.addWidget(self.stack, stretch=1)

        ml.addWidget(self._divider())

        self.results = ResultsPanel()
        self.results.setFixedHeight(220)
        ml.addWidget(self.results)

        # status bar
        sb = QFrame()
        sb.setFixedHeight(28)
        sb.setStyleSheet(f"background-color: {SURFACE}; border-top: 1px solid {BORDER};")
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(20, 0, 20, 0)
        sbl.setSpacing(8)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setFont(QFont("Segoe UI", 8))
        self.status_lbl.setStyleSheet(f"color: {TXT_MUTED}; border: none;")
        sbl.addWidget(self.status_lbl)

        sbl.addStretch()

        eng = "OpenCV SIMD" if OPENCV_AVAILABLE else "PIL"
        el = QLabel(f"Engine: {eng}  \u00b7  Threads: {os.cpu_count() * 2}")
        el.setFont(QFont("Segoe UI", 8))
        el.setStyleSheet(f"color: {TXT_MUTED}; border: none;")
        sbl.addWidget(el)

        # cancel button
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedHeight(20)
        self.btn_cancel.setFont(QFont("Segoe UI", 8))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {RED};
                border: 1px solid {RED}; padding: 0 10px;
            }}
            QPushButton:hover {{
                background-color: {RED}; color: white;
            }}
        """)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        sbl.addWidget(self.btn_cancel)

        # separator
        sep = QFrame()
        sep.setFixedSize(1, 16)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        sbl.addWidget(sep)

        # zoom controls
        zoom_btn_style = f"""
            QPushButton {{
                background-color: transparent; color: {TXT_MUTED};
                border: 1px solid {BORDER}; padding: 0;
                min-width: 20px; max-width: 20px;
                min-height: 20px; max-height: 20px;
            }}
            QPushButton:hover {{
                background-color: {ELEVATED}; color: {TXT};
            }}
        """
        self.btn_zoom_out = QPushButton("\u2212")
        self.btn_zoom_out.setFont(QFont("Segoe UI", 10))
        self.btn_zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_out.setStyleSheet(zoom_btn_style)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        sbl.addWidget(self.btn_zoom_out)

        self.zoom_lbl = QLabel(f"{int(ZOOM_STEPS[self._zoom_idx] * 100)}%")
        self.zoom_lbl.setFont(QFont("Segoe UI", 8))
        self.zoom_lbl.setStyleSheet(f"color: {TXT_MUTED}; border: none;")
        self.zoom_lbl.setFixedWidth(32)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sbl.addWidget(self.zoom_lbl)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFont(QFont("Segoe UI", 10))
        self.btn_zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_in.setStyleSheet(zoom_btn_style)
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        sbl.addWidget(self.btn_zoom_in)

        ml.addWidget(sb)

    # ── header ──────────────────────────────────────────────

    def _build_header(self):
        hdr = QFrame()
        hdr.setFixedHeight(46)
        hdr.setStyleSheet(f"background-color: {SURFACE};")
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(12)

        title = QLabel("COMIC CRUNCHER")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TXT}; border: none; letter-spacing: 1px;")
        lay.addWidget(title)
        lay.addStretch()

        # New button (hidden until processing finishes)
        self.btn_new = QPushButton("New Batch")
        self.btn_new.setFixedHeight(28)
        self.btn_new.setFont(QFont("Segoe UI", 9))
        self.btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {BLUE};
                border: 1px solid {BLUE};
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: {BLUE};
                color: white;
            }}
        """)
        self.btn_new.clicked.connect(self._reset)
        self.btn_new.setVisible(False)
        lay.addWidget(self.btn_new)

        # mode toggles
        self.btn_crunch = QPushButton("Cruncher")
        self.btn_combine = QPushButton("Combiner")
        self.btn_clean = QPushButton("Cleaner")
        self.btn_cull = QPushButton("Cull")
        for b in (self.btn_crunch, self.btn_combine, self.btn_clean, self.btn_cull):
            b.setFixedHeight(28)
            b.setMinimumWidth(90)
            b.setFont(QFont("Segoe UI", 9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_crunch.clicked.connect(lambda: self._set_mode("cruncher"))
        self.btn_combine.clicked.connect(lambda: self._set_mode("combiner"))
        self.btn_clean.clicked.connect(lambda: self._set_mode("cleaner"))
        self.btn_cull.clicked.connect(lambda: self._set_mode("cull"))
        self._style_mode_buttons()
        lay.addWidget(self.btn_crunch)
        lay.addWidget(self.btn_combine)
        lay.addWidget(self.btn_clean)
        lay.addWidget(self.btn_cull)
        return hdr

    def _style_mode_buttons(self):
        on = f"""QPushButton {{
            background-color: {BLUE}; color: white;
            border: none; padding: 0 14px;
        }}"""
        off = f"""QPushButton {{
            background-color: transparent; color: {TXT_MUTED};
            border: 1px solid {BORDER}; padding: 0 14px;
        }} QPushButton:hover {{
            background-color: {ELEVATED}; color: {TXT};
        }}"""
        for btn, mode in [(self.btn_crunch, "cruncher"),
                          (self.btn_combine, "combiner"),
                          (self.btn_clean, "cleaner"),
                          (self.btn_cull, "cull")]:
            btn.setStyleSheet(on if self.current_mode == mode else off)

    def _set_mode(self, mode):
        if self.processing:
            return
        self.current_mode = mode
        self._style_mode_buttons()
        self._reset()

    # ── helpers ─────────────────────────────────────────────

    @staticmethod
    def _divider():
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background-color: {BORDER};")
        return d

    def _reset(self):
        # Stop any running processor
        if self.processor and hasattr(self.processor, 'should_stop'):
            self.processor.should_stop = True
        if self.cover_extractor and self.cover_extractor.isRunning():
            self.cover_extractor.terminate()
        # Clean up combine button if present
        if hasattr(self, 'btn_combine_go') and self.btn_combine_go:
            self.btn_combine_go.deleteLater()
            self.btn_combine_go = None
        for w in self.covers:
            w.deleteLater()
        self.covers.clear()
        self.file_paths.clear()
        self.file_sizes.clear()
        self.active_idx = -1
        self.processing = False
        self.results.clear()
        self.top_bar._hide()
        self.btn_new.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.status_lbl.setText("Ready")
        self.stack.setCurrentWidget(self.drop_zone)

    def _on_cancel(self):
        """Cancel current operation and reset."""
        if self.processor and hasattr(self.processor, 'should_stop'):
            self.processor.should_stop = True
        self.status_lbl.setText("Cancelled")
        self._reset()

    # ── zoom ───────────────────────────────────────────────

    def _apply_zoom(self):
        global COVER_W, COVER_H, CELL_W, CELL_H
        scale = ZOOM_STEPS[self._zoom_idx]
        COVER_W = int(COVER_W_DEFAULT * scale)
        COVER_H = int(COVER_H_DEFAULT * scale)
        CELL_W = int(CELL_W_DEFAULT * scale)
        CELL_H = int(CELL_H_DEFAULT * scale)
        self.zoom_lbl.setText(f"{int(scale * 100)}%")

        # Rebuild grid if covers are visible
        if self.covers:
            # Rescale existing pixmaps and resize widgets
            for cw in self.covers:
                cw.setFixedSize(CELL_W, CELL_H)
                if cw.color_px:
                    cw.color_px = QPixmap.fromImage(cw.color_px.toImage()).scaled(
                        COVER_W, COVER_H,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                if cw.gray_px:
                    cw.gray_px = QPixmap.fromImage(cw.gray_px.toImage()).scaled(
                        COVER_W, COVER_H,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)

            # Reflow the grid
            cols = max(1, (self.cover_scroll.width() - 60) // CELL_W)
            cols = min(cols, len(self.covers))
            for k, cw in enumerate(self.covers):
                self.cover_grid.addWidget(
                    cw, k // cols, k % cols,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                )
                cw.update()

    def _zoom_in(self):
        if self._zoom_idx < len(ZOOM_STEPS) - 1:
            self._zoom_idx += 1
            self._apply_zoom()

    def _zoom_out(self):
        if self._zoom_idx > 0:
            self._zoom_idx -= 1
            self._apply_zoom()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reflow the cover grid when window is resized
        if self.covers and self.stack.currentWidget() == self.cover_scroll:
            cols = max(1, (self.cover_scroll.width() - 60) // CELL_W)
            cols = min(cols, len(self.covers))
            for k, cw in enumerate(self.covers):
                self.cover_grid.addWidget(
                    cw, k // cols, k % cols,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                )

    # ── file drop handling ──────────────────────────────────

    def _on_files_dropped(self, paths):
        if self.processing:
            return
        self.btn_new.setVisible(False)

        # Cleaner mode: no covers, just rename files
        if self.current_mode == "cleaner":
            self.file_paths = paths
            self._run_cleaner(paths)
            return

        # Cull mode: show covers, make clickable for deletion
        if self.current_mode == "cull":
            self.file_paths = paths
            self._build_cover_grid()
            self.stack.setCurrentWidget(self.cover_scroll)
            self.btn_cancel.setVisible(True)
            for cw in self.covers:
                cw._clickable = True
                cw.clicked.connect(self._on_cover_clicked)
            self.cover_extractor = CoverExtractor(paths)
            self.cover_extractor.cover_ready.connect(self._cover_ready)
            self.cover_extractor.all_done.connect(self._on_cull_covers_done)
            self.cover_extractor.start()
            self.status_lbl.setText(f"Click a cover to delete it ({len(paths)} files)")
            return

        # In cruncher mode with multiple files, filter out already-crunched
        if self.current_mode == "cruncher" and len(paths) > 1:
            to_process = []
            for fp in paths:
                if ComicUtils.is_already_crunched(fp):
                    self.results.add_result(fp, 'skipped', 'Already optimized')
                else:
                    to_process.append(fp)
            if not to_process:
                self.results.setVisible(True)
                self.status_lbl.setText(f"All {len(paths)} files already crunched")
                self.btn_new.setVisible(True)
                return
            if len(to_process) < len(paths):
                self.status_lbl.setText(
                    f"{len(paths) - len(to_process)} already crunched, processing {len(to_process)}")
            paths = to_process

        self.file_paths = paths
        self.processing = True

        for fp in paths:
            try:
                self.file_sizes[fp] = os.path.getsize(fp)
            except Exception:
                self.file_sizes[fp] = 0

        self._build_cover_grid()
        self.stack.setCurrentWidget(self.cover_scroll)

        self.cover_extractor = CoverExtractor(paths)
        self.cover_extractor.cover_ready.connect(self._cover_ready)
        self.cover_extractor.all_done.connect(self._covers_done)
        self.cover_extractor.start()
        self.status_lbl.setText(f"Extracting covers... ({len(paths)} files)")

    def _build_cover_grid(self):
        for w in self.covers:
            w.deleteLater()
        self.covers.clear()

        cols = max(1, (self.cover_scroll.width() - 60) // CELL_W)
        cols = min(cols, len(self.file_paths))

        for i, fp in enumerate(self.file_paths):
            w = CoverWidget(fp, i)
            self.cover_grid.addWidget(
                w, i // cols, i % cols,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            )
            self.covers.append(w)

    def _cover_ready(self, idx, color_qi, gray_qi):
        if idx < len(self.covers):
            self.covers[idx].set_images(color_qi, gray_qi)

    def _covers_done(self):
        if self.current_mode == "combiner":
            self._enter_reorder_mode()
            return
        self.status_lbl.setText("Starting processing...")
        self._start_processing()

    # ── combiner reorder ─────────────────────────────────────

    def _enter_reorder_mode(self):
        """Show covers in full color with reorder controls."""
        self.btn_cancel.setVisible(True)
        for i, cw in enumerate(self.covers):
            cw.progress = 100.0
            cw._target = 100.0
            cw._reorderable = True
            cw._position = i + 1
            cw.move_left.connect(self._move_cover_left)
            cw.move_right.connect(self._move_cover_right)
            cw.update()

        # Show combine button
        self.btn_combine_go = QPushButton("Combine")
        self.btn_combine_go.setFixedHeight(32)
        self.btn_combine_go.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_combine_go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_combine_go.setStyleSheet(f"""
            QPushButton {{
                background-color: {GREEN}; color: white;
                border: none; padding: 0 24px;
            }}
            QPushButton:hover {{ background-color: #2ea043; }}
        """)
        self.btn_combine_go.clicked.connect(self._on_combine_go)
        # Insert above the results panel
        main_lay = self.centralWidget().layout()
        main_lay.insertWidget(main_lay.count() - 1, self.btn_combine_go)

        self.status_lbl.setText("Reorder with \u25C4 \u25BA arrows, then click Combine")

    def _move_cover_left(self, idx):
        if idx <= 0:
            return
        self._swap_covers(idx, idx - 1)

    def _move_cover_right(self, idx):
        if idx >= len(self.covers) - 1:
            return
        self._swap_covers(idx, idx + 1)

    def _swap_covers(self, i, j):
        # Swap file paths
        self.file_paths[i], self.file_paths[j] = self.file_paths[j], self.file_paths[i]

        # Swap cover widgets in list
        self.covers[i], self.covers[j] = self.covers[j], self.covers[i]

        # Update indices and positions
        for k, cw in enumerate(self.covers):
            cw.index = k
            cw._position = k + 1

        # Rebuild grid positions
        cols = max(1, (self.cover_scroll.width() - 60) // CELL_W)
        cols = min(cols, len(self.file_paths))
        for k, cw in enumerate(self.covers):
            self.cover_grid.addWidget(
                cw, k // cols, k % cols,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            )
            cw.update()

    def _on_combine_go(self):
        """User confirmed order — start combining."""
        # Remove combine button
        self.btn_combine_go.deleteLater()
        self.btn_combine_go = None

        # Disable reorder on covers
        for cw in self.covers:
            cw._reorderable = False
            cw.progress = 0.0
            cw._target = 0.0
            cw.status = 'active'
            cw.update()

        self._start_combiner()

    # ── processing ──────────────────────────────────────────

    def _start_processing(self):
        if self.current_mode == "combiner":
            self._start_combiner()
        elif len(self.file_paths) == 1:
            self._start_single()
        else:
            self._start_batch()

    def _start_single(self):
        self.active_idx = 0
        self.btn_cancel.setVisible(True)
        if self.covers:
            self.covers[0].set_active()
        self.processor = ComicProcessor(self.file_paths[0])
        self.processor.progress_update.connect(self._on_progress)
        self.processor.finished.connect(self._on_finished)
        self.processor.start()
        self.status_lbl.setText(f"Processing: {Path(self.file_paths[0]).name}")

    def _start_batch(self):
        self._batch_completed = 0
        self.btn_cancel.setVisible(True)
        for c in self.covers:
            c.set_active()
        self.processor = BatchProcessor(self.file_paths)
        self.processor.file_info_update.connect(self._on_file_info)
        self.processor.file_progress.connect(self._on_file_progress)
        self.processor.batch_progress.connect(self._on_batch_progress)
        self.processor.finished.connect(self._on_finished)
        self.processor.start()
        self.status_lbl.setText(f"Processing batch: {len(self.file_paths)} files")

    def _start_combiner(self):
        comics = [f for f in self.file_paths if f.lower().endswith(('.cbz', '.cbr'))]
        if len(comics) < 2:
            self.status_lbl.setText("Need at least 2 comic files to combine")
            self.processing = False
            return
        self.btn_cancel.setVisible(True)
        for c in self.covers:
            c.set_active()
        self.processor = ComicCombiner(comics)
        self.processor.progress_update.connect(self._on_progress_combiner)
        self.processor.finished.connect(self._on_finished)
        self.processor.start()
        self.status_lbl.setText("Combining comics into TPB...")

    # ── signal handlers ─────────────────────────────────────

    def _on_progress(self, stage, pct):
        """Single-file progress only."""
        s, e = STAGE_WEIGHTS.get(stage, (0, 100))
        overall = s + (pct / 100) * (e - s)
        if self.covers:
            self.covers[0].set_progress(overall)
        self.top_bar.set_progress(overall)

    def _on_progress_combiner(self, stage, pct):
        s, e = STAGE_WEIGHTS.get(stage, (0, 100))
        overall = s + (pct / 100) * (e - s)
        for c in self.covers:
            c.set_progress(overall)
        self.top_bar.set_progress(overall)

    def _on_file_info(self, info):
        if not isinstance(info, str):
            return

        if info.startswith("Processing:"):
            name = info.replace("Processing:", "").strip()
            for i, fp in enumerate(self.file_paths):
                if Path(fp).name == name or name in fp:
                    self.active_idx = i
                    self.covers[i].set_active()
                    self.status_lbl.setText(f"Processing: {Path(fp).name}")
                    break

        elif info.startswith("Completed:"):
            text = info.replace("Completed:", "").strip()
            detail = ""
            name = text
            # Format: "filename (12.4MB → 6.6MB, 47% saved)" or "filename"
            if " (" in text:
                name, raw = text.split(" (", 1)
                name = name.strip()
                detail = raw.rstrip(")")
            elif " - " in text:
                name, detail = text.split(" - ", 1)
                name = name.strip()
                detail = detail.strip()

            for i, fp in enumerate(self.file_paths):
                if Path(fp).name == name or name in fp:
                    self.covers[i].set_completed()
                    if not detail:
                        detail = self._calc_savings(fp)
                    self.results.add_result(fp, 'completed', detail)
                    break

        elif info.startswith("Skipped:"):
            text = info.replace("Skipped:", "").strip()
            reason = "Already optimized"
            name = text
            if " (" in text:
                name, reason = text.split(" (", 1)
                name = name.strip()
                reason = reason.rstrip(")")

            for i, fp in enumerate(self.file_paths):
                if Path(fp).name == name or name in fp:
                    self.covers[i].set_skipped()
                    self.results.add_result(fp, 'skipped', reason)
                    break

        elif info.startswith("Error:"):
            text = info.replace("Error:", "").strip()
            for i, fp in enumerate(self.file_paths):
                if Path(fp).name in text or text in fp:
                    self.covers[i].set_error()
                    self.results.add_result(fp, 'error', text[:60])
                    break

    def _on_file_progress(self, filename, pct):
        """Real per-file progress from BatchProcessor."""
        for i, fp in enumerate(self.file_paths):
            if Path(fp).name == filename:
                self.covers[i].set_progress(pct)
                break

    def _on_batch_progress(self, current, total):
        pct = int(current / total * 100)
        self.top_bar.set_progress(pct)

    def _on_finished(self, success, message):
        self.processing = False
        self.btn_new.setVisible(True)
        self.btn_cancel.setVisible(False)

        # single-file: record the result
        if len(self.file_paths) == 1 and self.covers:
            fp = self.file_paths[0]
            if success:
                if "already crunched" in message.lower():
                    self.covers[0].set_skipped()
                    self.results.add_result(fp, 'skipped', 'Already optimized')
                else:
                    self.covers[0].set_completed()
                    detail = self._calc_savings(fp)
                    self.results.add_result(fp, 'completed', detail)
            else:
                self.covers[0].set_error()
                self.results.add_result(fp, 'error', message[:60])

        # batch: any covers still in 'active'/'processing' that never got
        # a Completed/Skipped/Error signal — mark done now
        for cw in self.covers:
            if cw.status in ('active', 'processing'):
                cw.set_completed()

        self.top_bar.set_complete()
        self.status_lbl.setText("Complete" if success else "Done with errors")

    def _calc_savings(self, fp):
        old = self.file_sizes.get(fp, 0)
        if old <= 0:
            return "Processed"

        # Output might be at same path (.cbz) or converted (.cbr → .cbz)
        p = Path(fp)
        candidates = [
            p,
            p.with_suffix('.cbz'),
        ]

        for path in candidates:
            try:
                if not path.exists():
                    continue
                new = path.stat().st_size
                if 0 < new < old:
                    pct = int((1 - new / old) * 100)
                    return f"{self._fmt(old)} \u2192 {self._fmt(new)}, {pct}% saved"
                elif new > 0:
                    return f"{self._fmt(old)} \u2192 {self._fmt(new)}"
            except OSError:
                continue
        return "Processed"

    @staticmethod
    def _fmt(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    # ── cull mode ─────────────────────────────────────────

    def _on_cull_covers_done(self):
        """After covers are extracted in cull mode, show them in full color."""
        for cw in self.covers:
            cw.progress = 100.0
            cw._target = 100.0
            cw.status = 'pending'  # keep pending so stripe stays off
            cw.update()
        self.status_lbl.setText(f"Click a cover to delete it ({len(self.file_paths)} files)")

    def _on_cover_clicked(self, index):
        if index >= len(self.file_paths):
            return
        fp = self.file_paths[index]
        name = Path(fp).name

        reply = QMessageBox.question(
            self, "Delete File",
            f"Permanently delete:\n{name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            os.remove(fp)
            self.covers[index].set_deleted()
            self.results.add_result(fp, 'error', 'Deleted')
            deleted = sum(1 for c in self.covers if c.status == 'deleted')
            self.status_lbl.setText(
                f"Deleted {deleted} of {len(self.file_paths)} | Click a cover to delete")
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Could not delete:\n{e}")

    # ── cleaner mode ────────────────────────────────────────

    def _run_cleaner(self, paths):
        renamed = 0
        skipped = 0
        total = len(paths)
        for idx, fp in enumerate(paths):
            old_path = Path(fp)
            new_path = Path(clean_filename(fp))

            # Animate cover: fill progress then mark done
            if idx < len(self.covers):
                self.covers[idx].set_progress(50)
                QApplication.processEvents()

            if old_path.name == new_path.name:
                if idx < len(self.covers):
                    self.covers[idx].set_skipped()
                self.results.add_result(fp, 'skipped', 'Already clean')
                skipped += 1
            else:
                try:
                    os.rename(old_path, new_path)
                    if idx < len(self.covers):
                        self.covers[idx].set_completed()
                    self.results.add_result(
                        fp, 'completed',
                        new_path.name
                    )
                    renamed += 1
                except OSError as e:
                    if idx < len(self.covers):
                        self.covers[idx].set_error()
                    self.results.add_result(fp, 'error', str(e)[:60])

            self.top_bar.set_progress(int((idx + 1) / total * 100))
            QApplication.processEvents()

        self.btn_new.setVisible(True)
        done = renamed + skipped
        self.status_lbl.setText(
            f"Cleaned {renamed} of {done} files"
            + (f" ({skipped} already clean)" if skipped else "")
        )


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = ConceptUI()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
