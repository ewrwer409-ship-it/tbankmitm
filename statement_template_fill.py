# -*- coding: utf-8 -*-
"""
Выписка: копируем страницы из Выписка.pdf (insert_pdf). Текст — redact белым + insert_text
по базовой линии шаблона (статика) / insert_textbox (сводка, колонтитул, суммы в ячейках).

Шрифты для подставляемого текста — как в чеках: полные файлы TinkoffSans-Regular.ttf и
TinkoffSans-Medium.ttf в папке tbankmitm (page.insert_font + fontfile). Если файлов нет —
Segoe / Arial / DejaVu. Subset из PDF не используем (нет глифов для нового текста).

Начертание: span flags + имя шрифта в шаблоне (Medium → жирная гарнитура).

Таблица: после заливки тела перерисовываются линии сетки (get_drawings).

Шаблон: tbankmitm/Выписка.pdf (11 стр.).
"""
from __future__ import annotations

import os
import re
import random
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import fitz


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def template_pdf_path() -> str:
    p = os.path.join(_script_dir(), "Выписка.pdf")
    if os.path.isfile(p):
        return p
    for name in os.listdir(_script_dir()):
        low = name.lower()
        if low.endswith(".pdf") and "ыписк" in name:
            return os.path.join(_script_dir(), name)
    return p


# Как в образце Выписка.pdf (если в config не заданы registration_address / contract_sign_date)
DEFAULT_REGISTRATION_ADDRESS = (
    "119021, г. Москва, район Хамовники, ул. Льва Толстого, д. 16"
)
DEFAULT_CONTRACT_SIGN_DATE = "09.09.2023"


def _symbola_font_path() -> Optional[str]:
    """Glyph ₽ как в Выписка.pdf (span Symbola). Не в репозитории — ищем в системных шрифтах."""
    wd = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    fd = os.path.join(wd, "Fonts")
    for name in ("symbola.ttf", "Symbola.ttf", "symbola.otf", "Symbola.otf"):
        p = os.path.join(fd, name)
        if os.path.isfile(p):
            return p
    return None


def _font_paths_regular_bold() -> Tuple[Optional[str], Optional[str]]:
    """
    Пары путей к TTF. Сначала TinkoffSans в каталоге модуля — тот же принцип, что в transfer.py /
    func.generate_operation_receipt (insert_font с именами my_normal / my_bold).
    """
    base = _script_dir()
    reg = os.path.join(base, "TinkoffSans-Regular.ttf")
    med = os.path.join(base, "TinkoffSans-Medium.ttf")
    if os.path.isfile(reg) and os.path.isfile(med):
        return reg, med
    if os.path.isfile(reg):
        return reg, reg

    wd = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    font_dir = os.path.join(wd, "Fonts")
    pairs = (
        ("segoeui.ttf", "segoeuib.ttf"),
        ("arial.ttf", "arialbd.ttf"),
        ("arialuni.ttf", "arialbd.ttf"),
    )
    for reg_name, bd_name in pairs:
        reg = os.path.join(font_dir, reg_name)
        bd = os.path.join(font_dir, bd_name)
        if os.path.isfile(reg):
            return reg, bd if os.path.isfile(bd) else reg
    for reg, bd in (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
    ):
        if os.path.isfile(reg):
            return reg, bd if os.path.isfile(bd) else reg
    return None, None


def _text_width_mm(text: str, fontsize: float, use_emphasis: bool) -> float:
    """Ширина строки в pt для Tinkoff TTF (get_text_length не знает my_normal)."""
    s = text or ""
    if not s:
        return 0.0
    reg_p, bd_p = _font_paths_regular_bold()
    path = bd_p if use_emphasis and bd_p else reg_p
    if path and os.path.isfile(path):
        try:
            return float(fitz.Font(fontfile=path).text_length(s, fontsize=fontsize))
        except Exception:
            pass
    try:
        return float(fitz.get_text_length(s, fontname="helv", fontsize=fontsize))
    except Exception:
        return len(s) * fontsize * 0.52


def _color_int_to_rgb(c: Any) -> Tuple[float, float, float]:
    if c is None:
        return (0, 0, 0)
    try:
        v = int(c)
    except (TypeError, ValueError):
        return (0, 0, 0)
    r = ((v >> 16) & 255) / 255.0
    g = ((v >> 8) & 255) / 255.0
    b = (v & 255) / 255.0
    return (r, g, b)


def _is_bold(flags: int) -> bool:
    # PyMuPDF / MuPDF: бит 4 — bold
    try:
        return bool(int(flags) & 16)
    except (TypeError, ValueError):
        return False


def _span_needs_emphasis_font(span: Optional[Dict[str, Any]], flags: int) -> bool:
    """В шаблоне Т-Банка заголовки часто TinkoffSans-Medium, не через bit bold."""
    if _is_bold(flags):
        return True
    fn = (span or {}).get("font") or ""
    low = fn.lower()
    return "medium" in low or "bold" in low or "semibold" in low


def _pick_draw_font(flags: int, reg: str, bd: str, span: Optional[Dict[str, Any]] = None) -> str:
    return bd if _span_needs_emphasis_font(span, flags) else reg


def _span_covering_needle(page: fitz.Page, needle: str) -> Optional[Dict[str, Any]]:
    if not needle:
        return None
    for r in page.search_for(needle, quads=False):
        clip = _expand(r, (4, 3, 8, 5))
        td = page.get_text("dict", clip=clip)
        for b in td.get("blocks", []) or []:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []) or []:
                for span in line.get("spans", []) or []:
                    if needle in (span.get("text") or ""):
                        return span
    return None


def _span_covering_needle_where(
    page: fitz.Page, needle: str, rect_ok: Optional[Callable[[fitz.Rect], bool]] = None
) -> Optional[Dict[str, Any]]:
    """Как _span_covering_needle, но только для search-hit, для которого rect_ok(r) истина."""
    if not needle:
        return None
    for r in page.search_for(needle, quads=False):
        if rect_ok is not None and not rect_ok(r):
            continue
        clip = _expand(r, (4, 3, 8, 5))
        td = page.get_text("dict", clip=clip)
        for b in td.get("blocks", []) or []:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []) or []:
                for span in line.get("spans", []) or []:
                    if needle in (span.get("text") or ""):
                        return span
    return None


_STMT_FONT_PATHS = "_stmt_font_paths"
_STMT_SYMBOLA_REG = "_stmt_symbola_registered"

# Тексты нижнего колонтитула — как в шаблоне Выписка.pdf (стр. 0/1, dict-снимок).
_STMT_TPL_FOOTER_L1 = (
    "АО «ТБанк» универсальная лицензия Банка России № 2673, к/с 30101810145250000974 в ГУ Банка России по ЦФО"
)
_STMT_TPL_FOOTER_L2 = "БИК 044525974 ИНН 7710140679 КПП 771301001"


def _register_template_fonts(page: fitz.Page) -> Tuple[str, str]:
    """
    (regular, emphasis). Имена шрифтов как в чеках: my_normal, my_bold (или helv при сбое).
    После apply_redactions() вызывать снова для этой страницы.
    """
    doc = page.parent
    paths = getattr(doc, _STMT_FONT_PATHS, None)
    if paths is None:
        reg_path, bd_path = _font_paths_regular_bold()
        setattr(doc, _STMT_FONT_PATHS, (reg_path, bd_path))
    else:
        reg_path, bd_path = paths

    if not reg_path:
        return "helv", "helv"

    try:
        page.insert_font(fontname="my_normal", fontfile=reg_path)
    except Exception:
        return "helv", "helv"
    bold_name = "my_normal"
    if bd_path and bd_path != reg_path:
        try:
            page.insert_font(fontname="my_bold", fontfile=bd_path)
            bold_name = "my_bold"
        except Exception:
            bold_name = "my_normal"

    return "my_normal", bold_name


def _ensure_cyrillic_font(page: fitz.Page) -> str:
    r, _ = _register_template_fonts(page)
    return r


def _expand(r: fitz.Rect, pad: Tuple[float, float, float, float] = (1.5, 0.5, 2, 2.5)) -> fitz.Rect:
    return fitz.Rect(r.x0 - pad[0], r.y0 - pad[1], r.x1 + pad[2], r.y1 + pad[3])


def _union_span_bbox(spans: List[Dict[str, Any]]) -> fitz.Rect:
    x0 = min(float(s["bbox"][0]) for s in spans)
    y0 = min(float(s["bbox"][1]) for s in spans)
    x1 = max(float(s["bbox"][2]) for s in spans)
    y1 = max(float(s["bbox"][3]) for s in spans)
    return fitz.Rect(x0, y0, x1, y1)


def _line_match_prefix(
    page: fitz.Page, prefix: str
) -> Optional[Tuple[List[Dict[str, Any]], fitz.Rect, Tuple[float, float]]]:
    """Строка (join spans) начинается с prefix — для «Движение средств за период с …» при любых датах в шаблоне."""
    if not prefix:
        return None
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            if not spans:
                continue
            full = "".join((s.get("text") or "") for s in spans)
            if not full.strip().startswith(prefix):
                continue
            ub = _union_span_bbox(spans)
            ox, oy = spans[0].get("origin") or (ub.x0, ub.y1)
            return spans, ub, (float(ox), float(oy))
    return None


def _line_match_full_text(
    page: fitz.Page, needle: str
) -> Optional[Tuple[List[Dict[str, Any]], fitz.Rect, Tuple[float, float]]]:
    """
    Строка целиком (join spans) == needle. Возвращает spans, bbox объединения,
    origin (x,y) первого span — базовая линия для insert_text.
    """
    if not needle:
        return None
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            if not spans:
                continue
            full = "".join((s.get("text") or "") for s in spans)
            if full != needle:
                continue
            ub = _union_span_bbox(spans)
            ox, oy = spans[0].get("origin") or (ub.x0, ub.y1)
            return spans, ub, (float(ox), float(oy))
    return None


def _header_doc_date_layout(
    page: fitz.Page, x_min: float = 420.0, y_max: float = 140.0
) -> Optional[Tuple[List[Dict[str, Any]], fitz.Rect, Tuple[float, float]]]:
    """Одна дата dd.mm.yyyy в правом верхнем углу (не таблица)."""
    rx = re.compile(r"^\s*\d{2}\.\d{2}\.\d{4}\s*$")
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            if not spans:
                continue
            full = "".join((s.get("text") or "") for s in spans)
            if not rx.match(full):
                continue
            s0 = spans[0]
            bb = s0.get("bbox") or (0, 0, 0, 0)
            if float(bb[0]) < x_min or float(bb[1]) > y_max:
                continue
            ub = _union_span_bbox(spans)
            ox, oy = spans[0].get("origin") or (ub.x0, ub.y1)
            return spans, ub, (float(ox), float(oy))
    return None


def _draw_baseline_texts(
    page: fitz.Page,
    items: List[Tuple[float, float, str, float, int, Tuple[float, float, float], Optional[Dict[str, Any]]]],
) -> None:
    """(x, y_baseline, text, fontsize, flags, rgb, span) — без insert_textbox, без вертикального сдвига."""
    if not items:
        return
    reg, bd = _register_template_fonts(page)
    for x, y, txt, fs, flags, rgb, span in items:
        fn = _pick_draw_font(flags, reg, bd, span)
        page.insert_text((x, y), txt, fontname=fn, fontsize=fs, color=rgb)


def _draw_multispan_line_like_template(
    page: fitz.Page,
    spans: List[Dict[str, Any]],
    new_full: str,
    reg: str,
    bd: str,
) -> None:
    """
    Строка из нескольких span в шаблоне: подписи как в PDF (часто Medium), значение — Regular,
    каждый фрагмент в своём origin — совпадает с Выписка.pdf по позиции и жирности.
    """
    if not spans:
        return
    if len(spans) == 1:
        s0 = spans[0]
        ox, oy = (float(s0["origin"][0]), float(s0["origin"][1])) if s0.get("origin") else (0.0, 0.0)
        fs = max(6.0, min(16.0, float(s0.get("size") or 9)))
        rgb = _color_int_to_rgb(s0.get("color"))
        flags = int(s0.get("flags") or 0)
        fn = _pick_draw_font(flags, reg, bd, s0)
        page.insert_text((ox, oy), new_full, fontname=fn, fontsize=fs, color=rgb)
        return
    rest = new_full
    for i in range(len(spans) - 1):
        t = spans[i].get("text") or ""
        if rest.startswith(t):
            rest = rest[len(t) :]
        else:
            s0 = spans[0]
            ox, oy = (float(s0["origin"][0]), float(s0["origin"][1])) if s0.get("origin") else (0.0, 0.0)
            fs = max(6.0, min(16.0, float(s0.get("size") or 9)))
            rgb = _color_int_to_rgb(s0.get("color"))
            flags = int(s0.get("flags") or 0)
            fn = _pick_draw_font(flags, reg, bd, s0)
            page.insert_text((ox, oy), new_full, fontname=fn, fontsize=fs, color=rgb)
            return
    for i in range(len(spans) - 1):
        si = spans[i]
        o = si.get("origin")
        if not o:
            continue
        fs = max(6.0, min(16.0, float(si.get("size") or 9)))
        rgb = _color_int_to_rgb(si.get("color"))
        flags = int(si.get("flags") or 0)
        fn = _pick_draw_font(flags, reg, bd, si)
        page.insert_text((float(o[0]), float(o[1])), si.get("text") or "", fontname=fn, fontsize=fs, color=rgb)
    sl = spans[-1]
    o = sl.get("origin")
    if not o:
        return
    fs = max(6.0, min(16.0, float(sl.get("size") or 9)))
    rgb = _color_int_to_rgb(sl.get("color"))
    flags = int(sl.get("flags") or 0)
    fn = _pick_draw_font(flags, reg, bd, sl)
    page.insert_text((float(o[0]), float(o[1])), rest, fontname=fn, fontsize=fs, color=rgb)


def _sanitize_cell_text(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    # Не склеиваем \n в пробел — иначе многострочное описание (телефон на 3-й строке) не помещается в ячейку.
    parts = []
    for block in s.split("\n"):
        parts.append(re.sub(r"[ \t]+", " ", block).strip())
    return "\n".join(parts).strip()


def _fmt_total_rub(v: float) -> str:
    s = f"{abs(v):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", " ")


def _fmt_signed_cell(amount: float, typ: str) -> str:
    s = _fmt_total_rub(amount).replace(" ₽", "").strip()
    if (typ or "").strip() == "Credit":
        return f"+{s} ₽"
    return f"-{s} ₽"


def _card_last4(card_mask: str) -> str:
    d = re.sub(r"\D", "", card_mask or "")
    if len(d) >= 4:
        return d[-4:]
    return "—"


def _split_dt(ms: int) -> Tuple[str, str]:
    if ms <= 0:
        return "", ""
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%d.%m.%Y"), dt.strftime("%H:%M")


def _split_from_display(date_display: str) -> Tuple[str, str]:
    s = (date_display or "").strip()
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?)", s)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _table_clip_for_page(page: fitz.Page) -> fitz.Rect:
    foot = page.search_for("АО «ТБанк» универсальная") or page.search_for("универсальная лицензия")
    y_foot = min(r.y0 for r in foot) if foot else 800.0
    # Нижняя граница многострочной шапки таблицы (в образце подписи колонок с y≈290–315)
    bottoms: List[fitz.Rect] = []
    for needle in ("списания", "Номер карты", "карты", "Описание", "операции"):
        for r in page.search_for(needle) or []:
            if r.y0 > 200:
                bottoms.append(r)
    if bottoms:
        y_hdr = max(r.y1 for r in bottoms) + 2.5
    else:
        hdr = page.search_for("Дата и время")
        y_hdr = max(r.y1 for r in hdr) + 16.0 if hdr else 86.0
    return fitz.Rect(26, y_hdr, 570, y_foot - 8)


@dataclass
class _ColLayout:
    x_date: float
    x_date2: float
    x_amt1: float
    x_amt2: float
    x_desc: float
    x_card: float
    row_h: float
    """Основной шаг между строками (между 2-й и далее; из шаблона ≈25)."""
    row_h_first: float
    """Шаг от 1-й строки ко 2-й (в шаблоне часто больше, ≈35)."""
    time_dy: float
    """Базовая линия времени: ниже даты на time_dy pt (из шаблона ≈11)."""
    fs: float
    rows_first: int
    rows_cont: int
    flags_date: int
    flags_amount: int
    flags_desc: int
    rgb_date: Tuple[float, float, float]
    rgb_amount: Tuple[float, float, float]
    rgb_desc: Tuple[float, float, float]
    span_date: Optional[Dict[str, Any]] = None
    span_amount: Optional[Dict[str, Any]] = None
    span_desc: Optional[Dict[str, Any]] = None


def _measure_cols(tpl_path: str) -> _ColLayout:
    doc = fitz.open(tpl_path)
    try:
        # В образце данные таблицы на стр. 0; стр. 1 — только продолжение без строк.
        pg = doc[0]
        clip = _table_clip_for_page(pg)
        usable_h = clip.y1 - clip.y0
        dflt = _ColLayout(
            42,
            118,
            198,
            286,
            364,
            532,
            25.0,
            35.0,
            11.08,
            8.0,
            10,
            16,
            0,
            0,
            0,
            (0, 0, 0),
            (0, 0, 0),
            (0, 0, 0),
            None,
            None,
            None,
        )

        needle_d = "14.04.2026"
        hits = sorted(pg.search_for(needle_d), key=lambda q: (q.y0, q.x0))
        if len(hits) < 2:
            needle_d = "24.03.2026"
            hits = sorted(pg.search_for(needle_d), key=lambda q: (q.y0, q.x0))
        if len(hits) < 2:
            return dflt

        sp_d = _span_covering_needle(pg, needle_d)
        flags_date = int((sp_d or {}).get("flags") or 0)
        rgb_date = _color_int_to_rgb((sp_d or {}).get("color"))

        first_y = hits[0].y0
        same = [h for h in hits if abs(h.y0 - first_y) < 2.5]
        same.sort(key=lambda h: h.x0)
        x_date = same[0].x0
        x_date2 = same[1].x0 if len(same) > 1 else x_date + 72
        if sp_d and sp_d.get("origin"):
            x_date = float(sp_d["origin"][0])
            if sp_d.get("bbox"):
                y_row = float(sp_d["bbox"][1])
                for block in pg.get_text("dict", clip=clip).get("blocks") or []:
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines") or []:
                        row_dates: List[Dict[str, Any]] = []
                        for span in line.get("spans") or []:
                            t = (span.get("text") or "").strip()
                            if len(t) == 10 and t[2] == "." and t[5] == "." and t[0:2].isdigit():
                                bb = span.get("bbox") or (0, 0, 0, 0)
                                if abs(float(bb[1]) - y_row) < 2.5:
                                    row_dates.append(span)
                        if len(row_dates) >= 2 and row_dates[1].get("origin"):
                            x_date2 = float(row_dates[1]["origin"][0])
                            break
                    else:
                        continue
                    break

        # Шаг строк: по левой колонке дат (две даты в одной строке дают dy=0 — не используем hits[1]-hits[0])
        date_row_tops: List[float] = []
        for block in pg.get_text("dict", clip=clip).get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    t = (span.get("text") or "").strip()
                    if len(t) == 10 and t[2] == "." and t[5] == "." and t[0:2].isdigit():
                        bx = span.get("bbox") or (999, 0, 0, 0)
                        if float(bx[0]) < float(bx[2]) and float(bx[0]) < clip.x0 + 120:
                            date_row_tops.append(float(bx[1]))
        date_row_tops = sorted(set(round(y, 2) for y in date_row_tops))
        steps = [date_row_tops[i + 1] - date_row_tops[i] for i in range(len(date_row_tops) - 1)]
        if not steps:
            row_h_first = 35.0
            row_h = 25.0
        else:
            row_h_first = max(24.0, min(42.0, steps[0]))
            rest_steps = steps[1:]
            if rest_steps:
                row_h = max(22.0, min(36.0, float(statistics.median(rest_steps))))
            else:
                row_h = max(22.0, min(36.0, min(row_h_first, 28.0)))

        time_dy = 11.08
        if date_row_tops:
            y0r = date_row_tops[0]
            y1band = y0r + max(20.0, row_h_first * 0.65)
            o_date = o_time = None
            for block in pg.get_text("dict", clip=clip).get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        bb = span.get("bbox") or (0, 0, 0, 0)
                        if float(bb[0]) > clip.x0 + 130:
                            continue
                        if float(bb[3]) <= y0r - 0.5 or float(bb[1]) >= y1band:
                            continue
                        t = (span.get("text") or "").strip()
                        if len(t) == 10 and t[2] == "." and t[5] == "." and t[0:2].isdigit():
                            o_date = span.get("origin")
                        elif 4 <= len(t) <= 8 and t[0].isdigit() and ":" in t:
                            o_time = span.get("origin")
            if o_date and o_time:
                time_dy = max(9.0, min(14.0, float(o_time[1]) - float(o_date[1])))

        amt_needles = ("-2 000.00", "-50.00", "-2 000,00")
        amt: List[fitz.Rect] = []
        amt_nd = ""
        for nd in amt_needles:
            amt = [r for r in (pg.search_for(nd) or []) if r.y0 > clip.y0 + 2]
            if amt:
                amt_nd = nd
                break
        x_amt1 = 198.0
        x_amt2 = 286.0
        sp_a = None
        if amt:
            a0 = min(amt, key=lambda r: (r.y0, r.x0))
            row_amt = [r for r in amt if abs(r.y0 - a0.y0) < 3.5]
            row_amt.sort(key=lambda r: r.x0)
            sp_a = _span_covering_needle(pg, amt_nd) if amt_nd else None
            x_amt1 = row_amt[0].x0 - 2
            if sp_a and sp_a.get("origin"):
                x_amt1 = float(sp_a["origin"][0])
            if len(row_amt) >= 2:
                x_amt2 = row_amt[1].x0 - 2
            else:
                amt2_needles = ("+1 000.00", "+500.00", "+1 000,00")
                for nd2 in amt2_needles:
                    row2 = [r for r in (pg.search_for(nd2) or []) if abs(r.y0 - a0.y0) < 3.5]
                    if len(row2) >= 2:
                        row2.sort(key=lambda r: r.x0)
                        x_amt2 = row2[1].x0 - 2
                        break
        flags_amount = int((sp_a or {}).get("flags") or 0)
        rgb_amount = _color_int_to_rgb((sp_a or {}).get("color"))

        desc_needles = ("Внешний перевод", "Оплата в", "Перевод")
        desc = []
        desc_nd = ""
        for nd in desc_needles:
            desc = pg.search_for(nd)
            if desc:
                desc_nd = nd
                break
        x_desc = (desc[0].x0 - 2) if desc else 364.0
        sp_desc = _span_covering_needle(pg, desc_nd) if desc_nd else None
        if sp_desc and sp_desc.get("origin"):
            x_desc = float(sp_desc["origin"][0])
        flags_desc = int((sp_desc or {}).get("flags") or 0)
        rgb_desc = _color_int_to_rgb((sp_desc or {}).get("color"))

        card_hits = pg.search_for("9612") or []
        tblc = _table_clip_for_page(pg)
        card_in_body = [
            r for r in card_hits if tblc.y0 - 8.0 < float(r.y0) < tblc.y1 + 8.0
        ]
        if card_in_body:
            # Берём самый правый hit в теле таблицы — первый hit может быть из другой области (уезжало описание).
            x_card = max(float(r.x0) for r in card_in_body)
        elif card_hits:
            x_card = max(float(r.x0) for r in card_hits)
        else:
            x_card = 532.0

        fs = float((sp_d or {}).get("size")) if (sp_d or {}).get("size") else max(6.5, min(8.8, hits[0].y1 - hits[0].y0 - 0.5))
        fs = max(6.5, min(10.5, fs))
        row_pitch = min(row_h, row_h_first)
        rows_cont = max(5, int(usable_h / row_pitch) - 2)
        rows_first = max(4, rows_cont - 4)
        return _ColLayout(
            x_date,
            x_date2,
            x_amt1,
            x_amt2,
            x_desc,
            x_card,
            row_h,
            row_h_first,
            time_dy,
            fs,
            rows_first,
            rows_cont,
            flags_date,
            flags_amount,
            flags_desc,
            rgb_date,
            rgb_amount,
            rgb_desc,
            sp_d,
            sp_a,
            sp_desc,
        )
    finally:
        doc.close()


def _wipe_table(page: fitz.Page, clip: Optional[fitz.Rect] = None) -> fitz.Rect:
    if clip is None:
        clip = _table_clip_for_page(page)
    page.add_redact_annot(clip, fill=(1, 1, 1))
    try:
        page.apply_redactions()
    except Exception:
        page.apply_redactions(images=0)
    return clip


def _collect_wide_horizontal_lines_above_y(
    page: fitz.Page, y_max: float, min_width: float = 320.0
) -> List[Dict[str, Any]]:
    """
    Длинные горизонтали в блоке ФИО/реквизитов (y < верха таблицы).
    Белые redact по тексту пересекают эти линии — без повторной отрисовки они пропадают.
    """
    segs: List[Dict[str, Any]] = []
    for d in page.get_drawings():
        if d.get("type") != "s":
            continue
        col = d.get("color") or (0, 0, 0)
        w = float(d.get("width") or 0.5)
        for it in d.get("items") or []:
            if not it or it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            if abs(float(p1.y) - float(p2.y)) > 0.05:
                continue
            y = float(p1.y)
            if y >= y_max:
                continue
            if abs(float(p2.x) - float(p1.x)) < min_width:
                continue
            segs.append({"p1": p1, "p2": p2, "color": col, "width": w})
    return segs


def _collect_line_segments_in_clip(page: fitz.Page, clip: fitz.Rect) -> List[Dict[str, Any]]:
    """Горизонтальные/вертикальные штрихи сетки таблицы до заливки — потом перерисуем."""
    segs: List[Dict[str, Any]] = []
    for d in page.get_drawings():
        if d.get("type") != "s":
            continue
        col = d.get("color") or (0, 0, 0)
        w = float(d.get("width") or 0.5)
        for it in d.get("items") or []:
            if not it or it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            bb = fitz.Rect(
                min(p1.x, p2.x) - 0.02,
                min(p1.y, p2.y) - 0.02,
                max(p1.x, p2.x) + 0.02,
                max(p1.y, p2.y) + 0.02,
            )
            if bb.intersects(clip):
                segs.append({"p1": p1, "p2": p2, "color": col, "width": w})
    return segs


def _redraw_line_segments(page: fitz.Page, segs: List[Dict[str, Any]]) -> None:
    for s in segs:
        try:
            page.draw_line(s["p1"], s["p2"], color=s["color"], width=float(s.get("width") or 0.5))
        except Exception:
            pass


def _table_body_hline_x_range(clip: fitz.Rect) -> Tuple[float, float]:
    """Горизонтали тела таблицы как в Выписка.pdf (~56–539 pt), внутри clip."""
    x0 = max(float(clip.x0), 56.0)
    x1 = min(float(clip.x1), 539.0)
    if x1 <= x0:
        return float(clip.x0), float(clip.x1)
    return x0, x1


def _draw_statement_row_separators(page: fitz.Page, clip: fitz.Rect, y_levels: List[float]) -> None:
    """
    Горизонтали: линия под шапкой таблицы + под нижним краем каждой строки данных.
    В Выписка.pdf линия между строками чуть выше начала следующей строки (≈ y + row_step − 2 pt).
    """
    if not y_levels:
        return
    x0, x1 = _table_body_hline_x_range(clip)
    rgb = (0, 0, 0)
    # Чуть жирнее штрих (печать/экран).
    w = 0.78
    prev: Optional[float] = None
    for y in sorted(y_levels):
        yy = float(y)
        if yy < clip.y0 - 1 or yy > clip.y1 + 1:
            continue
        if prev is not None and abs(yy - prev) < 0.35:
            continue
        prev = yy
        try:
            page.draw_line(fitz.Point(x0, yy), fitz.Point(x1, yy), color=rgb, width=w)
        except Exception:
            pass


def _clamp_rect_to_page(page: fitz.Page, r: fitz.Rect, inset: float = 0.5) -> fitz.Rect:
    """Красные зоны не вылезают за mediabox — иначе частично съедаются линии/бордюры справа."""
    b = page.rect + (-inset, -inset, inset, inset)
    return r & b


def _apply_whiteouts(page: fitz.Page, rects: List[fitz.Rect]) -> None:
    for r in rects:
        rc = _clamp_rect_to_page(page, r)
        if rc.is_empty or rc.get_area() <= 0:
            continue
        page.add_redact_annot(rc, fill=(1, 1, 1))
    try:
        page.apply_redactions()
    except Exception:
        page.apply_redactions(images=0)


def _format_statement_account_display(raw: str, stmt: Dict[str, Any]) -> str:
    """Номер лицевого счёта в выписке: к значению из конфига добавляем 2 цифры в конце (как в образце)."""
    s = re.sub(r"\s+", "", (raw or "").strip())
    if not s:
        s = "40817810200031613966"
    suf = str((stmt or {}).get("account_suffix", "01") or "01")[:2]
    if len(suf) < 2:
        suf = (suf + "0")[:2]
    if s.isdigit() and len(s) < 22:
        return s + suf
    return (raw or "").strip() or s


def _draw_text_jobs(
    page: fitz.Page,
    jobs: List[Tuple[fitz.Rect, str, float, int, Tuple[float, float, float], int, Optional[Dict[str, Any]]]],
) -> None:
    """
    Рисует текст после белых redact. PyMuPDF при redact(text=..., fontname=my_normal) не умеет
    измерять шрифт — текст пропадает; поэтому только заливка, затем insert_textbox.
    jobs: (rect, text, fontsize, span_flags, rgb, align, span|None) align = fitz.TEXT_ALIGN_*.
    """
    if not jobs:
        return
    reg, bd = _register_template_fonts(page)
    for rect, txt, fs, flags, rgb, align, span in jobs:
        fn = _pick_draw_font(flags, reg, bd, span)
        page.insert_textbox(rect, txt, fontname=fn, fontsize=fs, color=rgb, align=align)


def _statement_footer_layout_from_template(
    page: fitz.Page,
) -> Optional[Tuple[fitz.Rect, Dict[str, Any], fitz.Rect, fitz.Rect, fitz.Rect]]:
    """
    Нижний колонтитул в Выписка.pdf: две строки (АО…; БИК…) и отдельный span с номером страницы справа.
    Возвращает (union_whiteout, bik_span, ao_rect, bik_rect, page_num_rect) или None.
    """
    med = page.rect
    ao_r: Optional[fitz.Rect] = None
    bik_r: Optional[fitz.Rect] = None
    bik_sp: Optional[Dict[str, Any]] = None
    pg_r: Optional[fitz.Rect] = None
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            for sp in line.get("spans") or []:
                bb = sp.get("bbox")
                if not bb:
                    continue
                if float(bb[1]) < med.height * 0.82:
                    continue
                t = (sp.get("text") or "").strip()
                r = fitz.Rect(bb)
                if "ТБанк" in t or "тбанк" in t.lower():
                    ao_r = r if ao_r is None else (ao_r | r)
                if "БИК 044525974" in t:
                    bik_r = r
                    bik_sp = sp
                if t.isdigit() and 1 <= len(t) <= 3 and float(bb[0]) > med.width * 0.65:
                    pg_r = r
    if bik_r is None or bik_sp is None or ao_r is None:
        return None
    if pg_r is None:
        # Зона номера в образце: правый край цифры ≈ med.x1 − 56 (A4 595 pt).
        pg_r = fitz.Rect(med.x1 - 72.0, bik_r.y0 - 4.0, med.x1 - 56.0, bik_r.y1 + 12.0)
    union = bik_r | ao_r | pg_r
    union = _clamp_rect_to_page(page, _expand(union, (4, 4, 6, 8)))
    return union, bik_sp, ao_r, bik_r, pg_r


def _paint_statement_footer_like_template(page: fitz.Page, page_no: int) -> bool:
    """Перерисовать колонтитул как в Выписка.pdf (центр двух строк + номер справа)."""
    lay = _statement_footer_layout_from_template(page)
    if lay is None:
        return False
    union, bik_sp, ao_r, bik_r, pg_r = lay
    page.add_redact_annot(union, fill=(1, 1, 1))
    try:
        page.apply_redactions()
    except Exception:
        page.apply_redactions(images=0)
    fs_ft = float(bik_sp.get("size") or 8.0)
    fs_ft = max(5.5, min(9.5, fs_ft))
    rgb_ft = _color_int_to_rgb(bik_sp.get("color"))
    if rgb_ft == (0.0, 0.0, 0.0):
        rgb_ft = (0.25, 0.25, 0.25)
    reg2, bd2 = _register_template_fonts(page)
    fn2 = _pick_draw_font(int(bik_sp.get("flags", 0)), reg2, bd2, bik_sp)
    med = page.rect
    l1 = fitz.Rect(med.x0 + 32.0, ao_r.y0 - 2.5, med.x1 - 32.0, ao_r.y1 + 5.0)
    l2 = fitz.Rect(med.x0 + 32.0, bik_r.y0 - 2.0, med.x1 - 62.0, bik_r.y1 + 6.0)
    page.insert_textbox(
        l1,
        _STMT_TPL_FOOTER_L1,
        fontname=fn2,
        fontsize=fs_ft,
        color=rgb_ft,
        align=fitz.TEXT_ALIGN_CENTER,
    )
    page.insert_textbox(
        l2,
        _STMT_TPL_FOOTER_L2,
        fontname=fn2,
        fontsize=fs_ft,
        color=rgb_ft,
        align=fitz.TEXT_ALIGN_CENTER,
    )
    # Номер страницы: правый край текста как в шаблоне (у «1» x1 ≈ med−56), не у края листа.
    page_num_right = float(pg_r.x1)
    pr = fitz.Rect(page_num_right - 52.0, bik_r.y0 + 1.4, page_num_right + 0.5, bik_r.y1 + 7.4)
    pr = _clamp_rect_to_page(page, pr)
    page.insert_textbox(
        pr,
        str(page_no),
        fontname=fn2,
        fontsize=fs_ft,
        color=rgb_ft,
        align=fitz.TEXT_ALIGN_RIGHT,
    )
    return True


def _summary_row_spans_for_label(
    page: fitz.Page, label_variants: Tuple[str, ...]
) -> Optional[Tuple[List[Dict[str, Any]], fitz.Rect]]:
    """
    В Выписка.pdf подпись и сумма — в разных dict-line, но с одним y (одна визуальная строка).
    Собираем все span на той же базовой линии, что и найденная подпись, сортируем по x.
    """
    for lab in label_variants:
        hits = page.search_for(lab, quads=False)
        if not hits:
            continue
        r0 = hits[0]
        yc = (float(r0.y0) + float(r0.y1)) * 0.5
        slack = 3.3
        spans_out: List[Dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                for sp in line.get("spans") or []:
                    bb = sp.get("bbox")
                    if not bb:
                        continue
                    ym = (float(bb[1]) + float(bb[3])) * 0.5
                    if abs(ym - yc) <= slack:
                        spans_out.append(sp)
        if len(spans_out) < 2:
            continue
        spans_out.sort(key=lambda s: float(s["bbox"][0]))
        ub = _union_span_bbox(spans_out)
        return spans_out, ub
    return None


def _font_for_ruble_glyph(page: fitz.Page, rub_span: Dict[str, Any], reg: str, bd: str) -> str:
    """В образце ₽ — Symbola; иначе тот же шрифт, что для суммы (TinkoffSans)."""
    low = ((rub_span or {}).get("font") or "").lower()
    if "symbola" not in low:
        return _pick_draw_font(int(rub_span.get("flags") or 0), reg, bd, rub_span)
    doc = page.parent
    if getattr(doc, _STMT_SYMBOLA_REG, False):
        return "my_symbola"
    sp = _symbola_font_path()
    if not sp:
        return reg
    try:
        page.insert_font(fontname="my_symbola", fontfile=sp)
        setattr(doc, _STMT_SYMBOLA_REG, True)
        return "my_symbola"
    except Exception:
        return reg


def _draw_summary_total_line(
    page: fitz.Page,
    spans: List[Dict[str, Any]],
    label_base: str,
    tot: float,
    rub: str,
    reg: str,
    bd: str,
) -> None:
    lb_plain = (label_base or "").strip().rstrip(":").strip()
    label_spans: List[Dict[str, Any]] = []
    amt_sp: Optional[Dict[str, Any]] = None
    rub_sp: Optional[Dict[str, Any]] = None
    for s in spans:
        t = s.get("text") or ""
        ts = t.strip()
        if lb_plain and (ts.startswith(lb_plain) and ":" in t):
            label_spans.append(s)
            continue
        stripped = t.replace(rub, "")
        if rub in t and not re.search(r"\d", stripped):
            rub_sp = s
            continue
        if re.search(r"\d", t):
            amt_sp = s
            continue

    for s in label_spans:
        t = s.get("text") or ""
        o = s.get("origin")
        if not o:
            continue
        fs = max(7.0, min(16.0, float(s.get("size") or 10)))
        rgb = _color_int_to_rgb(s.get("color"))
        flags = int(s.get("flags") or 0)
        fn = _pick_draw_font(flags, reg, bd, s)
        page.insert_text((float(o[0]), float(o[1])), t, fontname=fn, fontsize=fs, color=rgb)

    num = _fmt_total_rub(tot)
    if amt_sp is None:
        return
    oa = amt_sp.get("origin")
    if not oa:
        return
    fs_a = max(7.0, min(16.0, float(amt_sp.get("size") or 10)))
    rgb_a = _color_int_to_rgb(amt_sp.get("color"))
    fl_a = int(amt_sp.get("flags") or 0)
    em_a = _span_needs_emphasis_font(amt_sp, fl_a)
    fn_a = _pick_draw_font(fl_a, reg, bd, amt_sp)
    ox_a, oy_a = float(oa[0]), float(oa[1])
    # Без хвостового пробела — в шаблоне bbox суммы и «₽» стыкуются (x1 суммы = x0 ₽).
    wnum = _text_width_mm(num, fs_a, em_a)
    page.insert_text((ox_a, oy_a), num, fontname=fn_a, fontsize=fs_a, color=rgb_a)
    if rub_sp is None:
        page.insert_text((ox_a + wnum, oy_a), rub, fontname=fn_a, fontsize=fs_a, color=rgb_a)
        return
    oru = rub_sp.get("origin")
    if not oru:
        return
    fs_r = max(7.0, min(16.0, float(rub_sp.get("size") or 10)))
    rgb_r = _color_int_to_rgb(rub_sp.get("color"))
    fn_r = _font_for_ruble_glyph(page, rub_sp, reg, bd)
    page.insert_text((ox_a + wnum, float(oru[1])), rub, fontname=fn_r, fontsize=fs_r, color=rgb_r)


def _count_wrapped_description_lines(
    desc: str,
    x0: float,
    x1: float,
    fontsize: float,
    line_step: float,
) -> int:
    """Сколько горизонтальных строк займёт описание (как в _draw_wrapped_description), для высоты строки таблицы."""
    s = _sanitize_cell_text(desc)
    if not s:
        return 1
    max_w = max(18.0, float(x1) - float(x0))
    lh = max(8.5, min(13.5, float(line_step)))
    lines = 0

    def flush_words_count(words: List[str]) -> None:
        nonlocal lines
        line: List[str] = []
        for w in words:
            cand = " ".join(line + [w]) if line else w
            if _text_width_mm(cand, fontsize, False) <= max_w:
                line.append(w)
                continue
            if line:
                lines += 1
                line = [w]
                if _text_width_mm(w, fontsize, False) <= max_w:
                    continue
                chunk = w
                while chunk:
                    lines += 1
                    n = 1
                    while n <= len(chunk) and _text_width_mm(chunk[:n], fontsize, False) <= max_w:
                        n += 1
                    n = max(1, n - 1)
                    chunk = chunk[n:].lstrip()
                line = []
                continue
            chunk = w
            while chunk:
                lines += 1
                n = 1
                while n <= len(chunk) and _text_width_mm(chunk[:n], fontsize, False) <= max_w:
                    n += 1
                n = max(1, n - 1)
                chunk = chunk[n:].lstrip()
        if line:
            lines += 1

    for block in s.split("\n"):
        para = block.strip()
        if not para:
            continue
        flush_words_count(para.split())
    return max(1, lines)


def _compute_row_step_for_description(lay: _ColLayout, desc: str, is_first_row: bool) -> float:
    """Шаг строки: не меньше шаблона, плюс запас под многострочное описание (дата+время слева — две базовые линии)."""
    base = float(lay.row_h_first) if is_first_row else float(lay.row_h)
    fs = float(lay.fs)
    td = float(lay.time_dy)
    x_desc_r = float(lay.x_card) - 6.0
    if x_desc_r <= lay.x_desc + 20.0:
        x_desc_r = lay.x_desc + 110.0
    n_lines = _count_wrapped_description_lines(desc, lay.x_desc, x_desc_r, fs, td)
    lh = max(8.5, min(13.5, td))
    # Нижняя граница контента: последняя базовая линия описания (y+fs + (n_lines-1)*lh) или время (y+fs+td).
    content_h = max(fs + td, fs + max(0, n_lines - 1) * lh)
    return max(base, content_h + 5.0)


def _draw_wrapped_description(
    page: fitz.Page,
    x0: float,
    x1: float,
    y_first_baseline: float,
    y_max_baseline: float,
    text: str,
    fontname: str,
    fontsize: float,
    color: Tuple[float, float, float],
    line_step: float,
) -> None:
    """
    После apply_redactions insert_textbox с подставным шрифтом (my_normal) часто даёт rc<0
    в низком прямоугольнике — текст не рисуется. insert_text + перенос по словам — стабильно.

    В Выписка.pdf строки описания с тем же вертикальным шагом, что и «время» под датой (time_dy),
    а не fs*1.12 — иначе многострочное описание «плывёт» относительно сумм и линии строки.
    """
    s = _sanitize_cell_text(text)
    if not s:
        return
    max_w = max(18.0, float(x1) - float(x0))
    lh = max(8.5, min(13.5, float(line_step)))
    y = float(y_first_baseline)

    def flush_words(words: List[str]) -> bool:
        nonlocal y
        line: List[str] = []
        for w in words:
            cand = " ".join(line + [w]) if line else w
            if _text_width_mm(cand, fontsize, False) <= max_w:
                line.append(w)
                continue
            if line:
                page.insert_text((x0, y), " ".join(line), fontname=fontname, fontsize=fontsize, color=color)
                y += lh
                if y > y_max_baseline:
                    return False
                line = [w]
                if _text_width_mm(w, fontsize, False) <= max_w:
                    continue
                chunk = w
                while chunk and y <= y_max_baseline:
                    n = 1
                    while n <= len(chunk) and _text_width_mm(chunk[:n], fontsize, False) <= max_w:
                        n += 1
                    n = max(1, n - 1)
                    page.insert_text((x0, y), chunk[:n], fontname=fontname, fontsize=fontsize, color=color)
                    y += lh
                    chunk = chunk[n:].lstrip()
                line = []
                continue
            chunk = w
            while chunk and y <= y_max_baseline:
                n = 1
                while n <= len(chunk) and _text_width_mm(chunk[:n], fontsize, False) <= max_w:
                    n += 1
                n = max(1, n - 1)
                page.insert_text((x0, y), chunk[:n], fontname=fontname, fontsize=fontsize, color=color)
                y += lh
                chunk = chunk[n:].lstrip()
        if line and y <= y_max_baseline:
            page.insert_text((x0, y), " ".join(line), fontname=fontname, fontsize=fontsize, color=color)
            y += lh
        return y <= y_max_baseline

    blocks = s.split("\n")
    for bi, block in enumerate(blocks):
        para = block.strip()
        if not para:
            if bi < len(blocks) - 1:
                y += lh * 0.25
            if y > y_max_baseline:
                return
            continue
        if not flush_words(para.split()):
            return


def _first_body_row_y0(
    page: fitz.Page, clip: fitz.Rect, first_date: str, page_index: int, lay: _ColLayout
) -> float:
    """
    Верх первой строки данных (y первой ячейки даты в колонке), до заливки таблицы.
    Важно: брать самый верхний hit среди всех шаблонных дат — иначе первая игла (дата текущей
    операции) может совпасть только со строкой ниже по странице, и сверху остаётся белый разрыв
    (часто на 3–4-й странице продолжения).
    """
    needles: List[str] = []
    if first_date:
        needles.append(first_date)
    needles.extend(("04.04.2026", "14.04.2026", "24.03.2026", "12.04.2026"))
    seen: set[str] = set()
    hits_all: List[fitz.Rect] = []
    for nd in needles:
        if not nd or nd in seen:
            continue
        seen.add(nd)
        for r in page.search_for(nd, quads=False):
            if r.y0 >= clip.y0 - 2.0 and r.x0 < clip.x0 + 140 and r.y0 < clip.y1 - 70.0:
                hits_all.append(r)
    if hits_all:
        return float(min(h.y0 for h in hits_all))
    pad = max(8.0, float(lay.row_h_first) * 0.75)
    return float(clip.y0) + pad


def _draw_row(
    page: fitz.Page,
    lay: _ColLayout,
    font_reg: str,
    font_bd: str,
    y: float,
    d1: str,
    t1: str,
    d2: str,
    t2: str,
    a1: str,
    a2: str,
    desc: str,
    card4: str,
    clip_y1: float,
    is_first_row: bool,
    row_step: float,
) -> None:
    fs = lay.fs
    td = float(lay.time_dy)
    f_dt = _pick_draw_font(lay.flags_date, font_reg, font_bd, lay.span_date)
    f_am = _pick_draw_font(lay.flags_amount, font_reg, font_bd, lay.span_amount)
    f_dc = _pick_draw_font(lay.flags_desc, font_reg, font_bd, lay.span_desc)
    page.insert_text((lay.x_date, y + fs), d1, fontname=f_dt, fontsize=fs, color=lay.rgb_date)
    page.insert_text((lay.x_date, y + fs + td), t1, fontname=f_dt, fontsize=fs, color=lay.rgb_date)
    page.insert_text((lay.x_date2, y + fs), d2, fontname=f_dt, fontsize=fs, color=lay.rgb_date)
    page.insert_text((lay.x_date2, y + fs + td), t2, fontname=f_dt, fontsize=fs, color=lay.rgb_date)
    y_amt = y + fs
    # В Выписка.pdf суммы с левого края колонок (origin x), а не right-align.
    page.insert_text((lay.x_amt1, y_amt), a1, fontname=f_am, fontsize=fs, color=lay.rgb_amount)
    page.insert_text((lay.x_amt2, y_amt), a2, fontname=f_am, fontsize=fs, color=lay.rgb_amount)
    fs_desc = float(fs)
    x_desc_r = float(lay.x_card) - 6.0
    if x_desc_r <= lay.x_desc + 20.0:
        x_desc_r = lay.x_desc + 110.0
    row_step = float(row_step)
    # Нижняя граница: почти до низа ячейки (дата/время слева занимают две базовые линии; описание — вторая+третья строки).
    # Раньше было row_bottom_line - 1.5 (≈ y+row_step-3.5) — не хватало высоты на 2-й абзац с телефоном при lh≈time_dy.
    y_desc_max_bl = min(float(clip_y1) - 2.0, y + float(row_step) - 0.5)
    _draw_wrapped_description(
        page,
        lay.x_desc,
        x_desc_r,
        y + fs,
        y_desc_max_bl,
        desc,
        f_dc,
        fs_desc,
        lay.rgb_desc,
        td,
    )
    page.insert_text((lay.x_card, y + fs), card4, fontname=f_dt, fontsize=fs, color=lay.rgb_date)


def _fill_static_page0(page: fitz.Page, cfg: Dict[str, Any], period_line: str, doc_date: str) -> None:
    table_y0 = _table_clip_for_page(page).y0
    header_rule_segs = _collect_wide_horizontal_lines_above_y(page, table_y0 - 0.5)

    nm = cfg.get("name") or {}
    rk = cfg.get("reki") or {}
    stmt = cfg.get("statement") or {}
    full_name = (nm.get("full_name") or " ".join(
        filter(None, [(nm.get("last_name") or "").strip(), (nm.get("first_name") or "").strip(), (nm.get("middle_name") or "").strip()])
    )).strip() or "Клиент"
    address = (nm.get("registration_address") or nm.get("address") or stmt.get("default_address") or "").strip()
    if not address:
        address = DEFAULT_REGISTRATION_ADDRESS
    contract = (rk.get("contract") or rk.get("dogovor") or "").strip()
    account = _format_statement_account_display(str(rk.get("account") or ""), stmt)
    contract_sign = (nm.get("contract_sign_date") or rk.get("contract_sign_date") or stmt.get("default_contract_sign_date") or "").strip()
    if not contract_sign:
        contract_sign = DEFAULT_CONTRACT_SIGN_DATE
    out_num = f"{random.randint(10_000_000, 99_999_999):08x}"[:8]

    # Строки — как в Выписка.pdf (двойной пробел после «:» в реквизитах; адрес — целиком).
    pairs: List[Tuple[str, str, float, Tuple[float, float, float, float]]] = [
        (
            "Адрес места жительства: 422526, Респ Татарстан, П Октябрьский, Ул Подгорная , д. 61А",
            f"Адрес места жительства: {address or '—'}",
            8,
            # Низ 28 pt заходил на «О продукте» (~13 pt под строкой адреса) — заголовок пропадал.
            (4, 2, 300, 5),
        ),
        (
            "Дата заключения договора:  09.09.2023",
            f"Дата заключения договора:  {contract_sign}",
            8,
            (4, 2, 220, 6),
        ),
        ("Номер договора:  5353811622", f"Номер договора:  {contract or '—'}", 8, (4, 2, 220, 6)),
        (
            "Номер лицевого счета:  40817810200031613966",
            f"Номер лицевого счета:  {account or '—'}",
            8,
            (4, 2, 280, 6),
        ),
        ("Исх. № 1b118203", f"Исх. № {out_num}", 9, (2, 2, 96, 4)),
    ]

    white: List[fitz.Rect] = []
    jobs: List[Tuple[fitz.Rect, str, float, int, Tuple[float, float, float], int, Optional[Dict[str, Any]]]] = []
    baseline: List[Tuple[float, float, str, float, int, Tuple[float, float, float], Optional[Dict[str, Any]]]] = []
    segmented: List[Tuple[List[Dict[str, Any]], str]] = []

    name_needles: List[str] = []
    for cand in (
        full_name,
        "Драпеза Светлана Петровна",
        "Кузнецова Ирина Владимировна",
    ):
        c = (cand or "").strip()
        if c and c not in name_needles:
            name_needles.append(c)
    name_lm: Optional[Tuple[List[Dict[str, Any]], fitz.Rect, Tuple[float, float]]] = None
    for cand in name_needles:
        name_lm = _line_match_full_text(page, cand)
        if name_lm:
            break
    if name_lm:
        sp_n, ub_n, _ = name_lm
        white.append(_expand(ub_n, (4, 2, 220, 6)))
        segmented.append((sp_n, full_name))

    lm_mov = _line_match_prefix(page, "Движение средств за период с")
    if lm_mov:
        sp_m, ub_m, _ = lm_mov
        white.append(_expand(ub_m, (4, 2, 340, 8)))
        segmented.append((sp_m, period_line))

    for old, new, fs_fb, pad in pairs:
        lm = _line_match_full_text(page, old)
        if lm:
            spans, ub, _ = lm
            er = _expand(ub, pad)
            white.append(er)
            segmented.append((spans, new))
            continue
        for r in page.search_for(old, quads=False):
            sp = _span_covering_needle(page, old) if old else None
            fs_use = float(sp["size"]) if sp and sp.get("size") else fs_fb
            fs_use = max(6.0, min(16.0, fs_use))
            flags = int(sp.get("flags", 0)) if sp else 0
            rgb = _color_int_to_rgb(sp.get("color") if sp else None)
            er = _expand(r, pad)
            white.append(er)
            jobs.append((er, new, fs_use, flags, rgb, fitz.TEXT_ALIGN_LEFT, sp))

    dd = _header_doc_date_layout(page)
    if dd:
        spans_d, ub_d, (ox_d, oy_d) = dd
        st_d = spans_d[0]
        fs_doc = float(st_d.get("size") or 10.0)
        fs_doc = max(6.0, min(13.0, fs_doc))
        flags_d = int(st_d.get("flags", 0))
        rgb_doc = _color_int_to_rgb(st_d.get("color"))
        white.append(_expand(ub_d, (4, 2, 64, 4)))
        baseline.append((ox_d, oy_d, doc_date, fs_doc, flags_d, rgb_doc, st_d))

    _apply_whiteouts(page, white)
    reg, bd = _register_template_fonts(page)
    for spans, new_full in segmented:
        _draw_multispan_line_like_template(page, spans, new_full, reg, bd)
    _draw_baseline_texts(page, baseline)
    _draw_text_jobs(page, jobs)
    _redraw_line_segments(page, header_rule_segs)


def _fill_summary_page(page: fitz.Page, tot_cred: float, tot_deb: float) -> None:
    """Сводка как в Выписка.pdf: подпись слева, сумма и ₽ — отдельные span’ы справа на той же базовой линии."""
    rub = "\u20bd"
    planned: List[Tuple[List[Dict[str, Any]], fitz.Rect, str, float]] = []
    rows_cfg: List[Tuple[Tuple[str, ...], str, float]] = [
        (("Пополнения:", "Пополнения"), "Пополнения:", tot_cred),
        (("Расходы:", "Расходы"), "Расходы:", tot_deb),
    ]
    for variants, label_base, tot in rows_cfg:
        hit = _summary_row_spans_for_label(page, variants)
        if hit:
            planned.append((hit[0], hit[1], label_base, tot))
    if not planned:
        return
    white = [_expand(ub, (4, 2, 18, 8)) for _, ub, __, ___ in planned]
    _apply_whiteouts(page, white)
    reg, bd = _register_template_fonts(page)
    for spans, _ub, label_base, tot in planned:
        _draw_summary_total_line(page, spans, label_base, tot, rub, reg, bd)


def generate_statement_pdf_from_template(
    movements: List[dict],
    from_ms: int,
    to_ms: int,
    output_path: str,
    config: Dict[str, Any],
) -> str:
    tpl_path = template_pdf_path()
    if not os.path.isfile(tpl_path):
        raise FileNotFoundError(f"Шаблон не найден: {tpl_path}")

    cfg = config or {}
    bal = cfg.get("balance") or {}
    card_mask = (bal.get("new_card_number") or bal.get("new_card_number2") or "").strip()
    card4 = _card_last4(card_mask)

    d1 = datetime.fromtimestamp(from_ms / 1000)
    d2 = datetime.fromtimestamp(to_ms / 1000)
    period_line = f"Движение средств за период с {d1.strftime('%d.%m.%Y')} по {d2.strftime('%d.%m.%Y')}"
    # Дата в шапке (справа) — как «По дату» в панели, а не текущий день.
    doc_date = d2.strftime("%d.%m.%Y")

    tot_cred = sum(float(m.get("amount") or 0) for m in movements if (m.get("type") or "").strip() == "Credit")
    tot_deb = sum(float(m.get("amount") or 0) for m in movements if (m.get("type") or "").strip() != "Credit")

    lay = _measure_cols(tpl_path)
    MAX_STMT_PAGES = 600

    tpl = fitz.open(tpl_path)
    try:
        if tpl.page_count < 11:
            raise ValueError(f"Выписка.pdf: нужно ≥11 страниц (как в образце), сейчас {tpl.page_count}")

        out = fitz.open()
        out.insert_pdf(tpl, from_page=0, to_page=0)
        pending = list(movements)
        pi = 0
        while True:
            if pi >= MAX_STMT_PAGES:
                raise ValueError(
                    f"Выписка: слишком много страниц операций (>{MAX_STMT_PAGES}). "
                    "Сократите период или проверьте шаблон."
                )
            if pi >= out.page_count:
                out.insert_pdf(tpl, from_page=1, to_page=1)
            pg = out[pi]
            clip = _table_clip_for_page(pg)
            if pi == 0:
                _fill_static_page0(pg, cfg, period_line, doc_date)
            if not pending:
                if pi == 0 and not movements:
                    first_date = ""
                    y = _first_body_row_y0(pg, clip, first_date, 0, lay)
                    _wipe_table(pg, clip)
                    font_reg, font_bd = _register_template_fonts(pg)
                    _draw_statement_row_separators(pg, clip, [float(clip.y0) + 0.34])
                    pg.insert_text(
                        (lay.x_date, y + 2),
                        "Нет операций за выбранный период.",
                        fontname=_pick_draw_font(lay.flags_date, font_reg, font_bd, lay.span_date),
                        fontsize=lay.fs,
                        color=lay.rgb_date,
                    )
                break
            m0 = pending[0]
            ms0 = int(m0.get("ms") or 0)
            if ms0 > 0:
                first_date, _ = _split_dt(ms0)
            else:
                first_date, _ = _split_from_display(m0.get("date_display") or "")
            y = _first_body_row_y0(pg, clip, first_date, pi, lay)
            _wipe_table(pg, clip)
            font_reg, font_bd = _register_template_fonts(pg)
            row_bottom = float(lay.fs) + float(lay.time_dy) + 2.0
            ri = 0
            drew_any = False
            # Линия под шапкой таблицы уже есть в шаблоне; на стр. 2+ повтор даёт «двойную» линию.
            row_sep_ys: List[float] = (
                [float(clip.y0) + 0.34] if pi == 0 else []
            )
            while pending:
                mpeek = pending[0]
                desc_try = (mpeek.get("description") or "Операция").strip()
                row_step = _compute_row_step_for_description(lay, desc_try, ri == 0)
                if y + row_step + row_bottom > clip.y1 - 2:
                    break
                m = pending.pop(0)
                drew_any = True
                ms = int(m.get("ms") or 0)
                if ms > 0:
                    d_op, t_op = _split_dt(ms)
                else:
                    d_op, t_op = _split_from_display(m.get("date_display") or "")
                typ = (m.get("type") or "Debit").strip()
                amt = float(m.get("amount") or 0)
                c1 = c2 = _fmt_signed_cell(amt, typ)
                desc = (m.get("description") or "Операция").strip()
                _draw_row(
                    pg,
                    lay,
                    font_reg,
                    font_bd,
                    y,
                    d_op,
                    t_op,
                    d_op,
                    t_op,
                    c1,
                    c2,
                    desc,
                    card4,
                    clip.y1,
                    ri == 0,
                    row_step,
                )
                row_sep_ys.append(y + row_step - 2.0)
                y += row_step
                ri += 1
            _draw_statement_row_separators(pg, clip, row_sep_ys)
            if not drew_any and pending:
                raise ValueError(
                    "Выписка: на странице не помещается ни одна строка таблицы (проверьте шаблон Выписка.pdf)."
                )
            if not pending:
                break
            pi += 1

        out.insert_pdf(tpl, from_page=10, to_page=10)
        sum_pg = out[-1]
        _fill_summary_page(sum_pg, tot_cred, tot_deb)

        # Колонтитул и номера страниц — как в Выписка.pdf (две строки по центру + номер отдельно справа).
        for i in range(out.page_count):
            pg = out[i]
            if _paint_statement_footer_like_template(pg, i + 1):
                continue
            hits = pg.search_for("БИК 044525974", quads=False)
            if not hits:
                continue
            r0 = hits[0]
            line_rect = fitz.Rect(36, r0.y0 - 1, 560, r0.y1 + 11)
            sp_ft = _span_covering_needle(pg, "БИК 044525974")
            fs_ft = float(sp_ft["size"]) if sp_ft and sp_ft.get("size") else 6.5
            fs_ft = max(5.5, min(9.0, fs_ft))
            rgb_ft = _color_int_to_rgb(sp_ft.get("color") if sp_ft else None)
            if rgb_ft == (0.0, 0.0, 0.0) and not sp_ft:
                rgb_ft = (0.25, 0.25, 0.25)
            line_txt = f"БИК 044525974 ИНН 7710140679 КПП 771301001 {i + 1}"
            pg.add_redact_annot(line_rect, fill=(1, 1, 1))
            try:
                pg.apply_redactions()
            except Exception:
                pg.apply_redactions(images=0)
            reg2, bd2 = _register_template_fonts(pg)
            fn2 = _pick_draw_font(int(sp_ft.get("flags", 0)) if sp_ft else 0, reg2, bd2, sp_ft)
            pg.insert_textbox(
                line_rect,
                line_txt,
                fontname=fn2,
                fontsize=fs_ft,
                color=rgb_ft,
                align=fitz.TEXT_ALIGN_LEFT,
            )

        out.save(output_path, garbage=4, deflate=True, incremental=False)
        out.close()
    finally:
        tpl.close()

    return output_path
