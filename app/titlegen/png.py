from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


GENRE_COLORS: dict[int, tuple[int, int, int, int]] = {
    -1: (0, 0, 0, 255),
    0: (0, 81, 90, 255),
    1: (156, 65, 0, 255),
    2: (55, 73, 0, 255),
    3: (90, 103, 129, 255),
    4: (189, 8, 99, 255),
    5: (115, 77, 0, 255),
    6: (77, 28, 115, 255),
    7: (156, 32, 0, 255),
}

TITLE_VARIANTS = {
    "songname_hshort.png": (720, 64, "horizontal_short"),
    "songname_hlong.png": (720, 104, "horizontal_long"),
    "songname_vshort.png": (56, 400, "vertical_short"),
    "songname_vlong.png": (96, 400, "vertical_long"),
}
TITLE_RENDER_VERSION = 8
_MIN_VERTICAL_ADVANCE_SCALE = 0.88
_TWO_COLUMN_FONT_THRESHOLD = 22
_TWO_COLUMN_MAX_SIZE = 24
_VSHORT_LOGICAL_WIDTH = 64

_ASSET_DIR = Path(__file__).resolve().parent / "util"
_FONT_PATH = _ASSET_DIR / "definitive_font.ttf"
_SMALL_KANA = set("ぁぃぅぇぉァィゥェォっッゃャゅュょョゎヮヵヶ")
_HGROUP = {"!", "?", "！", "？", "†"}
_ROTATE_SET = {
    "-", "‐", "|", "/", "\\", "ー", "～", "~", "（", "）", "(", ")",
    "「", "」", "[", "]", "Ｓ", "Ｔ", "【", "】", "…", "→", ":", "：",
}


def generate_title_pngs(
    out_dir: Path,
    title: str,
    subtitle: str | None,
    category: str | None,
) -> list[str]:
    generated: list[str] = []
    for name in TITLE_VARIANTS:
        generated.append(generate_title_png(out_dir, name, title, subtitle, category))
    return generated


def generate_title_png(
    out_dir: Path,
    name: str,
    title: str,
    subtitle: str | None,
    category: str | None,
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height, variant = TITLE_VARIANTS[name]
    genre = genre_for_category(category)
    path = out_dir / name
    if variant.startswith("horizontal"):
        img = _horizontal_image(width, height, title, subtitle, genre, variant)
    else:
        img = _vertical_image(width, height, title, subtitle, genre, variant)
    img.save(path, "PNG")
    return f"title/{name}"


def genre_for_category(category: str | None) -> int:
    s = (category or "").casefold()
    if "anime" in s or "アニメ" in s:
        return 1
    if "vocaloid" in s or "ボーカロイド" in s:
        return 3
    if "classical" in s or "クラシック" in s:
        return 5
    if "game" in s or "ゲーム" in s:
        return 6
    if "namco" in s or "ナムコ" in s:
        return 7
    if "variety" in s or "バラエティ" in s:
        return 2
    if "children" in s or "folk" in s or "どうよう" in s:
        return 4
    return 0


def _horizontal_image(
    width: int,
    height: int,
    title: str,
    subtitle: str | None,
    genre: int,
    variant: str,
) -> Image.Image:
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    fill = (255, 255, 255, 255)
    stroke = GENRE_COLORS.get(genre, GENRE_COLORS[0])

    has_subtitle = bool(subtitle)
    title_size = 42 if height <= 64 else 48
    sub_size = 24
    font = _fit_font(title, title_size, width - 30, height - (28 if has_subtitle else 8))
    bbox = draw.textbbox((0, 0), title or "Untitled", font=font, stroke_width=5)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    if variant == "horizontal_short":
        x = width - 14 - tw
        y = (height - th) // 2 - bbox[1]
    else:
        x = (width - tw) // 2 - bbox[0]
        y = 10 - bbox[1] if has_subtitle else (height - th) // 2 - bbox[1]
    _draw_text(draw, (x, y), title or "Untitled", font, fill, stroke, 5)

    if has_subtitle and height > 80:
        sub = str(subtitle)
        sub_font = _fit_font(sub, sub_size, width - 34, 30)
        sb = draw.textbbox((0, 0), sub, font=sub_font, stroke_width=3)
        sw = sb[2] - sb[0]
        sx = (width - sw) // 2 - sb[0]
        sy = height - 34 - sb[1]
        _draw_text(draw, (sx, sy), sub, sub_font, fill, stroke, 3)

    return img


def _vertical_image(
    width: int,
    height: int,
    title: str,
    subtitle: str | None,
    genre: int,
    variant: str,
) -> Image.Image:
    out_width = width
    if variant == "vertical_short" and width < 90:
        width = _VSHORT_LOGICAL_WIDTH
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    fill = (255, 255, 255, 255)
    stroke = GENRE_COLORS.get(genre if variant == "vertical_short" else -1, GENRE_COLORS[-1])

    title_col_w = 44 if width >= 90 else width
    title_y = 2
    title_h = height - 4
    x = width - title_col_w + (title_col_w // 2)
    title_text = title or "Untitled"
    font_size = _fit_vertical_size(title_text, title_col_w, title_h, 39)
    two_col = None
    if variant == "vertical_short" and width < 90 and font_size < _TWO_COLUMN_FONT_THRESHOLD:
        two_col = _fit_vertical_two_columns(title_text, width, title_h, _TWO_COLUMN_MAX_SIZE)

    if two_col:
        left_text, right_text, col_size = two_col
        font = _font(col_size)
        stroke_w = _stroke_for_size(col_size)
        _draw_vertical(draw, width * 3 // 4, title_y, title_h, right_text,
                       font, fill, stroke, stroke_w)
        _draw_vertical(draw, width // 4, title_y, title_h, left_text,
                       font, fill, stroke, stroke_w)
    else:
        font = _font(font_size)
        _draw_vertical(draw, x, title_y, title_h, title_text,
                       font, fill, stroke, _stroke_for_size(font_size))

    if subtitle and width >= 90:
        sub_font = _font(_fit_vertical_size(subtitle, 40, height - 8, 24))
        _draw_vertical(draw, 22, 4, height - 8, subtitle, sub_font, fill, stroke, 4)

    if width != out_width:
        return img.resize((out_width, height), Image.Resampling.LANCZOS)
    return img


def _draw_vertical(
    draw: ImageDraw.ImageDraw,
    cx: int,
    y: int,
    max_h: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    items = _vertical_items(text, draw, font)
    if not items:
        return
    char_h = _vertical_char_height(draw, font, stroke_width)
    base_positions = _vertical_positions(items, 0, char_h)
    scale = _vertical_advance_scale(base_positions, char_h, max_h)
    positions = [y + p * scale for p in base_positions]
    for item, cursor in zip(items, positions):
        if cursor + char_h > y + max_h:
            break
        if item["hgroup"]:
            run = item["chars"]
            total_w = sum(_char_width(draw, ch, font) for ch in run)
            x = cx - total_w / 2.0
            for ch in run:
                bbox = draw.textbbox((0, 0), ch, font=font, stroke_width=stroke_width)
                glyph_h = bbox[3] - bbox[1]
                draw_y = cursor + (char_h - glyph_h) / 2.0 - bbox[1]
                _draw_text(draw, (round(x - bbox[0]), round(draw_y)),
                           ch, font, fill, stroke, stroke_width)
                x += _char_width(draw, ch, font)
            continue

        ch = item["chars"][0]
        if _is_vertical_dot(ch):
            _draw_vertical_dot(draw, cx, cursor + char_h / 2.0, font.size,
                               fill, stroke, stroke_width)
            continue
        bbox = draw.textbbox((0, 0), ch, font=font, stroke_width=stroke_width)
        cw = bbox[2] - bbox[0]
        glyph_h = bbox[3] - bbox[1]
        x = cx - cw / 2.0 - bbox[0]
        draw_y = cursor + (char_h - glyph_h) / 2.0 - bbox[1]
        if ch in _ROTATE_SET:
            _draw_rotated_char(draw, round(x), round(draw_y), ch, font,
                               fill, stroke, stroke_width)
        else:
            _draw_text(draw, (round(x), round(draw_y)), ch, font,
                       fill, stroke, stroke_width)


def _vertical_chars(text: str) -> list[str]:
    replacements = {
        "ー": "丨",
        "-": "︲",
        "(": "︵",
        ")": "︶",
        "[": "﹇",
        "]": "﹈",
        "/": "／",
        ":": "‥",
        "…": "⋮",
        "~": "﹬",
        "〜": "﹬",
        "～": "﹬",
        "・": "·",
    }
    chars: list[str] = []
    for ch in text:
        if ch in _SMALL_KANA:
            chars.append(ch)
        else:
            chars.append(replacements.get(ch, ch))
    return chars


def _vertical_items(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
) -> list[dict[str, Any]]:
    raw = _vertical_chars(text)
    items: list[dict[str, Any]] = []
    i = 0
    space_before = False
    while i < len(raw):
        ch = raw[i]
        if ch == "\n" or ch.isspace():
            space_before = True
            i += 1
            continue
        if ch in _HGROUP:
            j = i
            while j < len(raw) and raw[j] in _HGROUP:
                j += 1
            run = raw[i:j]
            items.append({
                "chars": run,
                "hgroup": len(run) >= 2,
                "width": sum(_char_width(draw, c, font) for c in run),
                "space_before": space_before,
            })
            space_before = False
            i = j
        else:
            items.append({"chars": [ch], "hgroup": False,
                          "width": _char_width(draw, ch, font),
                          "space_before": space_before})
            space_before = False
            i += 1
    return items


def _vertical_positions(items: list[dict[str, Any]], y: float, char_h: int) -> list[float]:
    positions = [float(y)] * len(items)
    current = float(y)
    for i in range(1, len(items)):
        advance = char_h * 0.82
        if items[i].get("space_before"):
            advance += char_h * 0.90
        current += advance
        positions[i] = current
    return positions


def _vertical_advance_scale(positions: list[float], char_h: int, max_h: int) -> float:
    if not positions:
        return 1.0
    natural_h = positions[-1] + char_h
    if natural_h <= max_h:
        return 1.0
    if positions[-1] <= 0:
        return 1.0
    return max(0.1, (max_h - char_h) / positions[-1])


def _vertical_char_height(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    stroke_width: int = 0,
) -> int:
    bbox = draw.textbbox((0, 0), "A", font=font, stroke_width=stroke_width)
    return max(1, font.size + stroke_width * 2, bbox[3] - bbox[1])


def _char_width(draw: ImageDraw.ImageDraw, ch: str, font: ImageFont.FreeTypeFont) -> int:
    if ch.isspace():
        return max(1, font.size // 2)
    if _is_vertical_dot(ch):
        return max(1, font.size // 2)
    bbox = draw.textbbox((0, 0), ch, font=font)
    return max(1, bbox[2] - bbox[0])


def _draw_rotated_char(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    ch: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    bbox = draw.textbbox((0, 0), ch, font=font, stroke_width=stroke_width)
    w = max(1, bbox[2] - bbox[0] + stroke_width * 2 + 4)
    h = max(1, bbox[3] - bbox[1] + stroke_width * 2 + 4)
    tmp = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    _draw_text(tmp_draw, (stroke_width + 2 - bbox[0], stroke_width + 2 - bbox[1]),
               ch, font, fill, stroke, stroke_width)
    rotated = tmp.rotate(-90, expand=True)
    draw.bitmap((x, y), rotated)


def _is_vertical_dot(ch: str) -> bool:
    return ch in {"・", "·", "･", "•"}


def _draw_vertical_dot(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: float,
    font_size: int,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    inner = max(2, font_size // 11)
    outer = inner + max(2, stroke_width)
    draw.ellipse(
        (cx - outer, cy - outer, cx + outer, cy + outer),
        fill=stroke,
    )
    draw.ellipse(
        (cx - inner, cy - inner, cx + inner, cy + inner),
        fill=fill,
    )


def _fit_font(text: str, start: int, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    text = text or "Untitled"
    for size in range(start, 11, -1):
        font = _font(size)
        bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox(
            (0, 0), text, font=font, stroke_width=max(2, size // 8)
        )
        if bbox[2] - bbox[0] <= max_w and bbox[3] - bbox[1] <= max_h:
            return font
    return _font(12)


def _fit_vertical_size(text: str, width: int, max_h: int, start: int) -> int:
    text = text or "Untitled"
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for size in range(start, 11, -1):
        font = _font(size)
        items = _vertical_items(text, probe, font)
        char_h = _vertical_char_height(probe, font, max(2, size // 8))
        positions = _vertical_positions(items, 0, char_h)
        advance_scale = _vertical_advance_scale(positions, char_h, max_h)
        max_w = 0
        for item in items:
            max_w = max(max_w, int(item["width"]))
        if max_w <= width and advance_scale >= _MIN_VERTICAL_ADVANCE_SCALE:
            return size
    return 12


def _fit_vertical_two_columns(
    text: str,
    width: int,
    max_h: int,
    start: int,
) -> tuple[str, str, int] | None:
    text = text or "Untitled"
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    col_w = max(16, (width - 10) // 2)
    for candidates in (_split_candidates(text, words_only=True),
                       _split_candidates(text, words_only=False)):
        if not candidates:
            continue
        for size in range(start, 11, -1):
            font = _font(size)
            stroke_width = _stroke_for_size(size)
            ok: list[tuple[float, str, str]] = []
            for left_text, right_text in candidates:
                left = _vertical_fit_metrics(left_text, probe, font, col_w, max_h, stroke_width)
                right = _vertical_fit_metrics(right_text, probe, font, col_w, max_h, stroke_width)
                if left is None or right is None:
                    continue
                score = abs(left - right)
                ok.append((score, left_text, right_text))
            if ok:
                _, left_text, right_text = min(ok, key=lambda item: item[0])
                return left_text, right_text, size
    return None


def _vertical_fit_metrics(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    width: int,
    max_h: int,
    stroke_width: int,
) -> float | None:
    items = _vertical_items(text, draw, font)
    if not items:
        return None
    char_h = _vertical_char_height(draw, font, stroke_width)
    positions = _vertical_positions(items, 0, char_h)
    advance_scale = _vertical_advance_scale(positions, char_h, max_h)
    max_w = max(int(item["width"]) for item in items)
    if max_w > width or advance_scale < _MIN_VERTICAL_ADVANCE_SCALE:
        return None
    return positions[-1] + char_h


def _split_candidates(text: str, words_only: bool) -> list[tuple[str, str]]:
    text = text.strip()
    if len(text) < 2:
        return []
    candidates: list[tuple[str, str]] = []
    mid = len(text) // 2
    for radius in range(0, max(1, len(text))):
        for idx in (mid - radius, mid + radius):
            if idx <= 0 or idx >= len(text):
                continue
            if text[idx].isspace():
                left = text[idx:].strip()
                right = text[:idx].strip()
                if left and right:
                    candidates.append((left, right))
        if candidates:
            break

    if words_only:
        return candidates

    for idx in range(max(1, mid - 4), min(len(text), mid + 5)):
        left = text[idx:].strip()
        right = text[:idx].strip()
        if left and right:
            candidates.append((left, right))

    dedup: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def _stroke_for_size(size: int) -> int:
    return max(3, min(5, size // 7))


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_PATH), size=size)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    # Multiple decreasing strokes approximate the heavier official title edge.
    for width in (stroke_width, max(1, stroke_width * 2 // 3), max(1, stroke_width // 3)):
        draw.text(pos, text, font=font, fill=stroke, stroke_width=width)
    draw.text(pos, text, font=font, fill=fill)
