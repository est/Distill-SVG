"""Images, avatars, emoji and icons.

Two output policies are supported:

* ``embed`` (default): crop the detected photo/avatar from the original and
  embed it as a data URI.  This preserves the visual content, at the cost of
  carrying a small raster crop (and whatever pixel-level data it contained).
* ``placeholder``: replace it with a neutral glyph.  This is the privacy mode.

Icons, emoji and logo marks are traced into vector paths by default.  The
experimental ``icon_mode="embed"`` instead cuts the component out of the
original pixels (with an alpha mask) and embeds it as a PNG data URI, which is
visually closer to the source, but it can be larger and is still raster content.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, asdict

import cv2
import numpy as np

from .flatten import Prepared
from .panels import Panel


@dataclass
class ImageBlock:
    x: float
    y: float
    w: float
    h: float
    rx: float
    fill: str
    kind: str = "photo"  # photo | avatar | logo
    crop_box: tuple[float, float, float, float] | None = None
    data_uri: str = ""
    embedded: bool = False

    def render_box(self):
        box = self.crop_box if self.embedded and self.crop_box else (self.x, self.y, self.w, self.h)
        return box

    def to_dict(self):
        # Never put the base64 payload into the scene JSON: it makes the
        # editable scene unreadable and duplicates the SVG bytes.
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "rx": self.rx,
            "fill": self.fill,
            "kind": self.kind,
            "crop_box": list(self.crop_box) if self.crop_box else None,
            "embedded": self.embedded,
        }


@dataclass
class IconPath:
    d: str
    fill: str
    points: int = 0

    def to_dict(self):
        return asdict(self)


def _hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _color_mask(crop: np.ndarray, color: np.ndarray, tol: float = 12.0) -> np.ndarray:
    d = np.sqrt(((crop.astype(np.float32) - color.astype(np.float32)) ** 2).sum(axis=2))
    return d <= tol


def _panel_bbox_match(
    x: int,
    y: int,
    cw: int,
    ch: int,
    scale: int,
    panels: list[Panel],
    tol: float,
) -> Panel | None:
    """Return the panel whose full-res box nearly equals this component box."""
    x0, y0 = x * scale, y * scale
    x1, y1 = (x + cw) * scale, (y + ch) * scale
    for panel in panels:
        if (
            abs(x0 - panel.x) <= tol
            and abs(y0 - panel.y) <= tol
            and abs(x1 - (panel.x + panel.w)) <= tol
            and abs(y1 - (panel.y + panel.h)) <= tol
        ):
            return panel
    return None


def _is_panel_edge_component(
    cc: np.ndarray,
    comp_id: int,
    x: int,
    y: int,
    cw: int,
    ch: int,
    scale: int,
    panels: list[Panel],
) -> bool:
    """Detect a panel's own rounded/antialiased border caught as a component.

    The colour and local-variance passes both flag the rim of a rounded panel
    (it is neither the flat fill nor a palette colour).  After the border band
    is erased, what remains is a thin ring whose bounding box is the panel
    itself.  Left alone it becomes an embedded "icon" crop covering the whole
    panel, and text inside that crop is then discarded as duplicated photo
    content.

    A genuine avatar/photo/icon inside a panel can also share its bounding box
    (e.g. a flat blue avatar that panels.py recovered as a round panel), so a
    bbox match alone is not enough.  Only skip components whose pixels hug the
    box border: those are rims.  Solid content stays.
    """
    # A few pixels: panel and component boxes are quantised differently.
    tol = max(3.0, 2.0 * scale)
    if _panel_bbox_match(x, y, cw, ch, scale, panels, tol) is None:
        return False

    comp = cc[y:y + ch, x:x + cw] == comp_id
    ys, xs = np.where(comp)
    if len(xs) == 0:
        return False
    # Distance from every component pixel to the component box border, in
    # full-res pixels.  A rim has almost all pixels close to the border; a
    # filled disc/photo has most pixels near the centre.
    inset = np.minimum(
        np.minimum(xs, cw - 1 - xs),
        np.minimum(ys, ch - 1 - ys),
    ) * scale
    band = max(2.0, 1.5 * scale)
    return float((inset <= band).mean()) >= 0.55


def _embed_crop(
    prep: Prepared,
    box: tuple[float, float, float, float],
    fmt: str = "png",
    quality: int = 92,
    max_size: int = 2000,
    grayscale: bool = False,
    scrub: bool = False,
) -> str:
    """Crop the original screenshot and encode it as a data URI.

    The crop is re-encoded from pixels, so EXIF/JPEG metadata from the source
    file is not copied.  It is still a raster crop: if the source screenshot
    had pixel-level watermarks in that area, they are preserved.  Use
    ``--photos=placeholder`` when that matters.
    """
    x, y, w, h = box
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(prep.width, int(round(x + w)))
    y1 = min(prep.height, int(round(y + h)))
    if x1 <= x0 or y1 <= y0:
        return ""
    crop = prep.original[y0:y1, x0:x1]
    if crop.size == 0:
        return ""

    max_dim = max(crop.shape[:2])
    if max_size and max_dim > max_size:
        scale = max_size / float(max_dim)
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if grayscale:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        crop = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    if scrub:
        # Drop the low two bits per channel.  It is visually near-lossless for
        # photos but destroys the most common LSB steganography channel.
        crop = ((crop.astype(np.uint16) // 4) * 4).astype(np.uint8)

    fmt = (fmt or "png").lower()
    if fmt in ("jpg", "jpeg"):
        ok, buf = cv2.imencode(
            ".jpg",
            crop,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(max(40, min(100, quality)))],
        )
        mime = "image/jpeg"
    else:
        ok, buf = cv2.imencode(".png", crop, [int(cv2.IMWRITE_PNG_COMPRESSION), 6])
        mime = "image/png"
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _embed_icon_crop(
    prep: Prepared,
    cc: np.ndarray,
    comp_id: int,
    x: int,
    y: int,
    w: int,
    h: int,
    palette_bgr: np.ndarray,
    grayscale: bool = False,
    scrub: bool = False,
) -> tuple[str, tuple[float, float, float, float]]:
    """Cut an icon component out of the screenshot and encode it with alpha.

    The connected component is only a *support* mask: it is a filled
    local-variance blob, so using it directly would make the whole bounding
    box opaque.  Alpha comes from the colour distance to the flat UI palette
    instead, which keeps only the actual glyph/emoji pixels and leaves the
    surrounding panel (including holes inside outline icons) transparent.
    """
    s = prep.scale
    pad = 1
    x0 = max(0, int(round(x * s)) - pad)
    y0 = max(0, int(round(y * s)) - pad)
    x1 = min(prep.width, int(round((x + w) * s)) + pad)
    y1 = min(prep.height, int(round((y + h) * s)) + pad)
    if x1 <= x0 or y1 <= y0:
        return "", (0.0, 0.0, 0.0, 0.0)
    crop = prep.original[y0:y1, x0:x1].copy()

    # Support: the detection component, slightly dilated so antialiased
    # edges are allowed into the alpha computation.
    support = (cc[y:y + h, x:x + w] == comp_id).astype(np.uint8) * 255
    support = cv2.dilate(support, np.ones((3, 3), np.uint8))
    support = cv2.resize(support, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
    support = support.astype(np.float32) / 255.0

    # Alpha: distance from the nearest flat surface colour.  Pixels on the
    # panel/background become transparent; glyph pixels stay opaque; the
    # antialiased rim gets a soft value.
    d = np.sqrt(
        ((crop.astype(np.float32)[:, :, None, :] - palette_bgr[None, None, :, :]) ** 2).sum(axis=3)
    ).min(axis=2)
    alpha = np.clip((d - 4.0) / 16.0, 0.0, 1.0) * support
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0.35)
    alpha = np.clip(alpha, 0.0, 1.0)
    crop[alpha < 0.03] = 0

    if grayscale:
        crop = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    if scrub:
        crop = ((crop.astype(np.uint16) // 4) * 4).astype(np.uint8)

    rgba = np.dstack([crop, (alpha * 255.0).astype(np.uint8)])
    ok, buf = cv2.imencode(".png", rgba, [int(cv2.IMWRITE_PNG_COMPRESSION), 6])
    if not ok:
        return "", (0.0, 0.0, 0.0, 0.0)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}", (float(x0), float(y0), float(x1 - x0), float(y1 - y0))


def _trace_mask(mask: np.ndarray, unit: float = 1.0, offset: tuple[float, float] = (0.0, 0.0)) -> tuple[str, int]:
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "", 0
    parts: list[str] = []
    points = 0
    for cnt in contours:
        if cv2.contourArea(cnt) < 0.7:
            continue
        eps = max(0.55, 0.008 * cv2.arcLength(cnt, True))
        approx = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        pts = approx.astype(np.float32) * unit
        pts[:, 0] += offset[0]
        pts[:, 1] += offset[1]
        parts.append("M" + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts) + "Z")
        points += len(pts)
        if points > 450:
            break
    return " ".join(parts), points


def _vectorize_icon(
    prep: Prepared,
    x: int,
    y: int,
    w: int,
    h: int,
    surface_rgb: np.ndarray | None = None,
) -> list[IconPath]:
    """Posterize a small residual component and trace each colour layer."""
    if w < 3 or h < 3:
        return []
    pad = 1
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(prep.aw, x + w + pad), min(prep.ah, y + h + pad)
    crop = prep.flat[y0:y1, x0:x1]
    if crop.size == 0:
        return []

    # Small glyphs benefit from a little extra resolution before contouring.
    up = 4 if min(w, h) <= 20 else 2 if min(w, h) <= 40 else 1
    if up > 1:
        crop = cv2.resize(crop, (crop.shape[1] * up, crop.shape[0] * up), interpolation=cv2.INTER_CUBIC)

    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    pixels = lab.reshape(-1, 3).astype(np.float32)
    unique_count = len(np.unique(pixels, axis=0))
    k = int(np.clip(round(unique_count ** 0.5), 1, 6))
    if k < 2:
        k = 1
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    centers = np.uint8(np.clip(centers, 0, 255))
    centers_rgb = cv2.cvtColor(centers.reshape(1, -1, 3), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    labels = labels.reshape(crop.shape[:2])

    out: list[IconPath] = []
    total = labels.size
    for i in range(k):
        count = int((labels == i).sum())
        if count / total < 0.02:
            continue
        if surface_rgb is not None:
            # Do not paint the card/panel colour back as an icon layer.  This
            # is what otherwise produces big pale rectangles around emoji and
            # star ratings.
            dist = np.sqrt(((surface_rgb - centers_rgb[i].astype(np.float32)) ** 2).sum(axis=1).min())
            if dist < 14:
                continue
        mask = (labels == i).astype(np.uint8)
        # A light closing repairs JPEG/dither holes without moving edges much.
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
        d, points = _trace_mask(
            mask,
            unit=float(prep.scale) / float(up),
            offset=(x0 * prep.scale, y0 * prep.scale),
        )
        if d and points > 2:
            out.append(IconPath(d=d, fill=_hex(centers_rgb[i]), points=points))
    return out


def extract_images_and_icons(
    prep: Prepared,
    panels: list[Panel],
    text_boxes: list[tuple[float, float, float, float]],
    background: str,
    photo_mode: str = "embed",
    photo_format: str = "png",
    photo_quality: int = 92,
    photo_max_size: int = 2000,
    photo_grayscale: bool = False,
    photo_scrub: bool = False,
    trace_icons: bool = True,
    icon_mode: str = "vector",
) -> tuple[list[ImageBlock], list[IconPath]]:
    h, w = prep.labels.shape
    flat = prep.flat

    # Colours that describe flat UI surfaces.  Anything far from these is
    # content (text, icon, photo) and becomes a candidate.
    bg_rgb = (int(background[1:3], 16), int(background[3:5], 16), int(background[5:7], 16))
    palette_colors = [np.array(bg_rgb[::-1], dtype=np.float32)]
    # panels are stored in RGB hex; convert to BGR for distance tests
    for p in panels:
        rgb = (int(p.fill[1:3], 16), int(p.fill[3:5], 16), int(p.fill[5:7], 16))
        palette_colors.append(np.array(rgb[::-1], dtype=np.float32))
    pal = np.stack(palette_colors).astype(np.float32)

    d = np.sqrt(((flat.astype(np.float32)[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3))
    residual = (d.min(axis=2) > 14).astype(np.uint8)

    # A photo/avatar can share the bubble's dominant colour (e.g. a blue
    # avatar on a blue bubble).  Local variance catches it even when the
    # colour is close to a flat surface.
    gray = flat.mean(axis=2).astype(np.float32)
    mean = cv2.blur(gray, (5, 5))
    sq = cv2.blur(gray * gray, (5, 5))
    std = np.sqrt(np.maximum(0.0, sq - mean * mean))
    residual |= (std > 14).astype(np.uint8)

    # Rounded panel edges also have high local variance.  If left in, they
    # can connect an avatar to its bubble and produce a huge, wrong photo box.
    # Erase a thin band on every panel boundary.
    for panel in panels:
        px0 = max(0, int(round(panel.x / prep.scale)) - 1)
        py0 = max(0, int(round(panel.y / prep.scale)) - 1)
        px1 = min(w - 1, int(round((panel.x + panel.w) / prep.scale)) + 1)
        py1 = min(h - 1, int(round((panel.y + panel.h) / prep.scale)) + 1)
        cv2.rectangle(residual, (px0, py0), (px1, py1), 0, 2)

    # Text is handled by the text layer; don't trace its antialiasing.
    for bx, by, bw, bh in text_boxes:
        x0 = max(0, int(bx / prep.scale) - 4)
        y0 = max(0, int(by / prep.scale) - 4)
        x1 = min(w, int((bx + bw) / prep.scale) + 4)
        y1 = min(h, int((by + bh) / prep.scale) + 4)
        residual[y0:y1, x0:x1] = 0

    residual = cv2.morphologyEx(residual, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    residual = cv2.dilate(residual, np.ones((2, 2), np.uint8))

    n, cc, stats, _ = cv2.connectedComponentsWithStats(residual, 8)
    images: list[ImageBlock] = []
    icons: list[IconPath] = []
    icon_comps: list[tuple[int, int, int, int, int]] = []
    photo_boxes: list[tuple[int, int, int, int]] = []
    s = prep.scale
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i])
        if area < 8:
            continue
        # Isolated dither/JPEG specks become visible dots in wireframe mode.
        if area < 16 and cw <= 4 and ch <= 4:
            continue
        if max(cw, ch) <= 6 and area < 60:
            continue
        if max(cw, ch) <= 8 and area < 120:
            continue
        if cw < 3 or ch < 3:
            continue
        if cw > w * 0.9 or ch > h * 0.9:
            continue
        # Ignore long one-pixel borders/shadows.
        if max(cw / max(ch, 1), ch / max(cw, 1)) > 14:
            continue
        if area / float(cw * ch) < 0.15:
            continue

        # The panel's own rounded border is not an icon/photo.  Skipping it
        # here also keeps the OCR text inside the panel from being dropped by
        # api.py as "already inside an embedded image".
        if _is_panel_edge_component(cc, i, x, y, cw, ch, s, panels):
            continue

        full_w, full_h = cw * s, ch * s
        unique = len(np.unique(flat[y:y + ch, x:x + cw].reshape(-1, 3), axis=0))
        density = area / float(cw * ch)
        margin = x < w * 0.09 or x + cw > w * 0.91
        big = cw >= 34 and ch >= 34 and cw * ch >= 3000

        # An icon that occupies almost the whole of a small card (a logo
        # tile, a toolbar button) should be traced, not turned into a photo
        # placeholder.  A real photo usually sits in a much larger card.
        cx_full = (x + cw / 2.0) * s
        cy_full = (y + ch / 2.0) * s
        in_small_card = False
        if full_w * full_h < 12000:  # a glyph/button-sized component
            for panel in panels:
                if (panel.x <= cx_full <= panel.x + panel.w
                        and panel.y <= cy_full <= panel.y + panel.h):
                    in_small_card = True
                    break

        # Photos are chromatically busy; a line icon or flat logo has only a
        # handful of dominant colours.  This is a better avatar-vs-icon test
        # than brightness, which misreads a blue logo on white as a photo.
        crop_px = flat[y:y + ch, x:x + cw].reshape(-1, 3)
        _, colour_counts = np.unique(crop_px, axis=0, return_counts=True)
        top5 = float(np.sort(colour_counts)[::-1][:5].sum()) / max(1, len(crop_px))
        avatar_like = (
            margin
            and min(cw, ch) >= 14
            and unique >= 25
            and density >= 0.35
            and top5 < 0.50
            and (not in_small_card or min(cw, ch) >= 40)
        )
        is_photo = big or avatar_like

        if is_photo:
            # Keep the un-trimmed component box for the raster crop: the
            # trimmed "content" box is good for a placeholder glyph but would
            # cut off avatar edges.
            orig_x, orig_y, orig_cw, orig_ch = x, y, cw, ch
            comp_mask = (cc[y:y + ch, x:x + cw] == i)
            comp_crop = prep.flat[y:y + ch, x:x + cw]
            # Tighten the box to actual content pixels.  A sparse avatar
            # (e.g. a logo plus a party hat) otherwise gets a bounding box
            # full of background, and the placeholder looks far too large.
            dcomp_all = np.sqrt(
                ((comp_crop.astype(np.float32)[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3)
            )
            content_mask = comp_mask & (dcomp_all.min(axis=2) > 14)
            if content_mask.sum() >= 8:
                cy, cx = np.where(content_mask)
                nx0, ny0 = int(cx.min()), int(cy.min())
                nx1, ny1 = int(cx.max()) + 1, int(cy.max()) + 1
                x, y = x + nx0, y + ny0
                cw, ch = nx1 - nx0, ny1 - ny0
                comp_mask = content_mask[ny0:ny1, nx0:nx1]
                comp_crop = comp_crop[ny0:ny1, nx0:nx1]
                full_w, full_h = cw * s, ch * s
            px = comp_crop[comp_mask]
            # Ignore background/panel colours when choosing the placeholder
            # tint; otherwise an avatar on a white card becomes white.
            if len(px):
                dcomp = np.sqrt(
                    ((px.astype(np.float32)[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
                )
                content = px[dcomp.min(axis=1) > 14]
                if len(content) >= max(8, len(px) // 20):
                    px = content
            rgb = np.median(px, axis=0)[::-1] if len(px) else np.array([200, 200, 200])
            kind = "avatar" if avatar_like and cw * ch < 7000 else "photo"
            if margin and kind == "photo":
                kind = "logo"
            # A small glyph inside a button should be traced even if the
            # detector first saw it as a photo-like blob.
            if in_small_card and full_w < 70 and full_h < 70:
                icon_comps.append((x, y, cw, ch, i))
                continue

            pad = max(1, s)
            # Base the crop on the tightened content box, not the dilated
            # component box; the latter often includes several empty pixels
            # below small avatars.
            bx = max(0.0, x * s - pad)
            by = max(0.0, y * s - pad)
            bw = min(float(prep.width), (x + cw) * s + pad) - bx
            bh = min(float(prep.height), (y + ch) * s + pad) - by

            # A large product photo often contains its own headline, which the
            # OCR pass masks out.  That splits the photo into a lower fragment.
            # Grow the crop upward/downward over nearby text boxes that share
            # its horizontal span, so the restored image includes its headline.
            if kind == "photo" and full_w * full_h > 10000:
                for tx, ty, tw, th in text_boxes:
                    ox = max(0.0, min(bx + bw, tx + tw) - max(bx, tx))
                    if ox / max(1.0, tw) < 0.55:
                        continue
                    if ty + th <= by and by - (ty + th) <= 90:
                        by = max(0.0, ty - 2)
                        bh = (y + ch) * s + pad - by
                    elif ty >= by + bh and ty - (by + bh) <= 90:
                        bh = ty + th + 2 - by
                    bh = min(bh, float(prep.height) - by)
            crop_box = (bx, by, bw, bh)
            data_uri = ""
            if photo_mode == "embed":
                data_uri = _embed_crop(
                    prep,
                    crop_box,
                    fmt=photo_format,
                    quality=photo_quality,
                    max_size=photo_max_size,
                    grayscale=photo_grayscale,
                    scrub=photo_scrub,
                )
            images.append(
                ImageBlock(
                    x=x * s,
                    y=y * s,
                    w=full_w,
                    h=full_h,
                    rx=min(full_w, full_h) * (0.24 if kind == "avatar" else 0.10),
                    fill=_hex(rgb),
                    kind=kind,
                    crop_box=crop_box,
                    data_uri=data_uri,
                    embedded=bool(data_uri),
                )
            )
            photo_boxes.append((orig_x, orig_y, orig_cw, orig_ch))
        else:
            icon_comps.append((x, y, cw, ch, i))

    icon_mode = (icon_mode or "vector").lower()
    if not trace_icons and icon_mode != "embed":
        return images, []

    # Do not trace or embed text/detail *inside* a photo crop: it would
    # duplicate the embedded pixels and produce stray shapes.
    for x, y, cw, ch, comp_id in icon_comps:
        cx, cy = x + cw / 2.0, y + ch / 2.0
        inside_photo = any(
            px <= cx <= px + pw and py <= cy <= py + ph
            for px, py, pw, ph in photo_boxes
        )
        if inside_photo:
            continue
        if icon_mode == "embed":
            data_uri, crop_box = _embed_icon_crop(
                prep,
                cc,
                comp_id,
                x,
                y,
                cw,
                ch,
                palette_bgr=pal,
                grayscale=photo_grayscale,
                scrub=photo_scrub,
            )
            if not data_uri:
                continue
            images.append(
                ImageBlock(
                    x=x * s,
                    y=y * s,
                    w=cw * s,
                    h=ch * s,
                    rx=0.0,
                    fill="#808080",
                    kind="icon",
                    crop_box=crop_box,
                    data_uri=data_uri,
                    embedded=True,
                )
            )
        else:
            icons.extend(_vectorize_icon(prep, x, y, cw, ch, pal[:, ::-1]))

    # A component can produce many tiny paths; keep the output bounded.
    icons.sort(key=lambda p: p.points, reverse=True)
    return images, icons[:220]


def placeholder_glyph(kind: str, x: float, y: float, w: float, h: float, fill: str) -> str:
    """A small monochrome glyph for an image placeholder."""
    cx, cy = x + w / 2, y + h / 2
    if kind in ("avatar", "logo"):
        r = min(w, h) * 0.16
        return (
            f'<circle cx="{cx:.1f}" cy="{cy - r * 0.55:.1f}" r="{r:.1f}" fill="{fill}"/>'
            f'<path d="M{cx - r * 1.7:.1f},{cy + r * 1.5:.1f} '
            f'C{cx - r * 1.7:.1f},{cy + r * 0.1:.1f} {cx + r * 1.7:.1f},{cy + r * 0.1:.1f} '
            f'{cx + r * 1.7:.1f},{cy + r * 1.5:.1f}Z" fill="{fill}"/>'
        )
    # landscape / image glyph
    m = min(w, h) * 0.22
    return (
        f'<circle cx="{cx + m * 0.9:.1f}" cy="{cy - m * 0.9:.1f}" r="{m * 0.45:.1f}" fill="{fill}"/>'
        f'<path d="M{x + m:.1f},{y + h - m:.1f} L{cx:.1f},{cy + m * 0.2:.1f} '
        f'L{x + w - m * 1.4:.1f},{y + h - m:.1f}Z" fill="{fill}"/>'
    )
