"""盛世华诞 · 2026 国庆献礼短片生成器。

画面、文字动画、烟花、国旗飘动、长城日出和配乐音效全部由代码生成，
不使用任何图片、视频或音频素材。

用法：
    pip install pillow numpy imageio-ffmpeg
    python make_video.py                 # 生成 national_day_2026.mp4
    python make_video.py --preview 3 20  # 只导出指定秒数的静帧 PNG，便于调试
"""
import math
import os
import random
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import imageio_ffmpeg

W, H, FPS = 1280, 720, 30
DUR = 60.0
SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

rng = np.random.default_rng(20261001)
YY, XX = np.mgrid[0:H, 0:W].astype(np.float32)


def sm(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def fade(t, a, b):
    """在 [a, b] 间从 0 平滑过渡到 1。"""
    return sm((t - a) / (b - a))


def vgrad(top, bottom, stops=None):
    top, bottom = np.array(top, np.float32), np.array(bottom, np.float32)
    k = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    return np.broadcast_to(top * (1 - k) + bottom * k, (H, W, 3)).copy()


# ---------------------------------------------------------------- 文字
_fonts, _sprites = {}, {}


def font(sz):
    if sz not in _fonts:
        _fonts[sz] = ImageFont.truetype(FONT, sz)
    return _fonts[sz]


def sprite(s, sz, bold, glow):
    key = (s, sz, bold, glow)
    if key not in _sprites:
        f = font(sz)
        l, t, r, b = f.getbbox(s, stroke_width=bold)
        pad = glow * 3 + 6
        im = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 0)
        ImageDraw.Draw(im).text((pad - l, pad - t), s, font=f, fill=255,
                                stroke_width=bold, stroke_fill=255)
        m = np.asarray(im, np.float32) / 255
        g = np.asarray(im.filter(ImageFilter.GaussianBlur(glow)), np.float32) / 255
        _sprites[key] = (m, np.clip(g * 1.6, 0, 1))
    return _sprites[key]


GOLD_STOPS = np.array([[1.0, 0.97, 0.78], [1.0, 0.84, 0.38], [0.86, 0.56, 0.13]], np.float32)


def text(frame, s, sz, cx, cy, alpha=1.0, color="gold", glow_col=(1.0, 0.7, 0.25),
         glow_amt=0.7, shadow=0.0, bold=2, glow=10):
    if alpha <= 0.001:
        return
    m, g = sprite(s, sz, bold, glow)
    sh, sw = m.shape
    x0, y0 = int(round(cx - sw / 2)), int(round(cy - sh / 2))
    fx0, fy0, fx1, fy1 = max(x0, 0), max(y0, 0), min(x0 + sw, W), min(y0 + sh, H)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sl = (slice(fy0 - y0, fy1 - y0), slice(fx0 - x0, fx1 - x0))
    ms = m[sl][..., None] * alpha
    gs = g[sl][..., None] * alpha
    region = frame[fy0:fy1, fx0:fx1]
    if shadow > 0:
        region *= 1 - shadow * gs
    if glow_amt > 0:
        region += np.array(glow_col, np.float32) * gs * glow_amt
    if color == "gold":
        k = np.linspace(0, 1, sh, dtype=np.float32)[sl[0]]
        col = np.where((k < 0.5)[:, None],
                       GOLD_STOPS[0] * (1 - 2 * k[:, None]) + GOLD_STOPS[1] * 2 * k[:, None],
                       GOLD_STOPS[1] * (2 - 2 * k[:, None]) + GOLD_STOPS[2] * (2 * k[:, None] - 1))
        col = col[:, None, :]
    else:
        col = np.array(color, np.float32)
    region[:] = region * (1 - ms) + col * ms


def overlay(frame, img):
    a = np.asarray(img, np.float32) / 255
    frame[:] = frame * (1 - a[..., 3:]) + a[..., :3] * a[..., 3:]


# ---------------------------------------------------------------- 星空与城市
NS = 320
star_x = rng.integers(0, W, NS)
star_y = (rng.random(NS) ** 1.4 * H * 0.62).astype(int)
star_b = rng.uniform(0.15, 0.8, NS).astype(np.float32)
star_f = rng.uniform(1.5, 5, NS)
star_p = rng.uniform(0, 6.3, NS)


def add_stars(frame, t, amt=1.0):
    b = star_b * (0.65 + 0.35 * np.sin(t * star_f + star_p)).astype(np.float32) * amt
    frame[star_y, star_x] += b[:, None] * np.array([0.85, 0.9, 1.0], np.float32)


def build_skyline(seed):
    r = random.Random(seed)
    img = Image.new("RGB", (W, H), (0, 0, 0))
    m = Image.new("L", (W, H), 0)
    d, dm = ImageDraw.Draw(img), ImageDraw.Draw(m)
    for col, hmin, hmax, wscale in (((26, 14, 36), 110, 250, 0.45), ((9, 5, 14), 50, 170, 0.9)):
        x = -30
        while x < W:
            bw = r.randint(38, 96)
            center = 1 - abs(x + bw / 2 - W / 2) / (W / 2)
            top = H - int(r.uniform(hmin, hmax) * (0.65 + 0.55 * center))
            box = [x, top, x + bw, H]
            d.rectangle(box, fill=col)
            dm.rectangle(box, fill=255)
            k = r.random()
            if k < 0.18:
                tri = [(x, top), (x + bw / 2, top - bw * 0.45), (x + bw, top)]
                d.polygon(tri, fill=col)
                dm.polygon(tri, fill=255)
            elif k < 0.34:
                ax = x + bw // 2
                d.rectangle([ax - 1, top - 45, ax + 1, top], fill=col)
                dm.rectangle([ax - 1, top - 45, ax + 1, top], fill=255)
                d.ellipse([ax - 2, top - 49, ax + 2, top - 45], fill=(255, 60, 50))
            for wy in range(top + 8, H - 6, 12):
                for wx in range(x + 5, x + bw - 7, 9):
                    if r.random() < 0.33:
                        s = wscale * r.uniform(0.45, 1.0)
                        d.rectangle([wx, wy, wx + 4, wy + 6],
                                    fill=(int(255 * s), int(196 * s), int(110 * s)))
            x += bw + r.randint(-10, 4)
    return np.asarray(img, np.float32) / 255, (np.asarray(m, np.float32) / 255)[..., None]


SKY_IMG, SKY_MASK = build_skyline(7)
NIGHT = vgrad((0.01, 0.01, 0.05), (0.16, 0.04, 0.10))


def add_skyline(frame):
    frame[:] = frame * (1 - SKY_MASK) + SKY_IMG * SKY_MASK


# ---------------------------------------------------------------- 烟花
PALETTE = [(1.0, 0.82, 0.3), (1.0, 0.25, 0.2), (1.0, 0.95, 0.8), (1.0, 0.45, 0.6),
           (0.5, 0.85, 1.0), (0.6, 1.0, 0.55), (1.0, 0.6, 0.15)]


class Burst:
    def __init__(self, t0, x, y, r):
        self.t0, self.x, self.y = t0, x, y
        self.kind = "willow" if r.random() < 0.25 else "peony"
        self.n = r.randint(70, 110)
        self.life = 3.0 if self.kind == "willow" else 2.2
        self.col = np.array((1.0, 0.8, 0.35) if self.kind == "willow" else r.choice(PALETTE), np.float32)
        self.col2 = np.array(r.choice(PALETTE), np.float32) if r.random() < 0.4 else self.col
        ang = np.linspace(0, 2 * np.pi, self.n, endpoint=False) + r.uniform(0, 1)
        self.dir = np.stack([np.cos(ang), np.sin(ang)], 1).astype(np.float32)
        self.v = np.array([r.uniform(0.8, 1.0) for _ in range(self.n)], np.float32) * r.uniform(230, 340)
        self.ph = np.array([r.uniform(0, 6.3) for _ in range(self.n)], np.float32)
        self.k = 1.2 if self.kind == "willow" else 1.8
        self.g = 90 if self.kind == "willow" else 55
        self.loud = r.uniform(0.6, 1.0)

    def pos(self, a):
        a = max(a, 0.0)
        s = (1 - math.exp(-self.k * a)) / self.k
        p = self.dir * (self.v[:, None] * s)
        p[:, 0] += self.x
        p[:, 1] += self.y + 0.5 * self.g * a * a
        return p


def make_bursts(times, seed, ymin=110, ymax=330):
    r = random.Random(seed)
    return [Burst(t, r.uniform(160, W - 160), r.uniform(ymin, ymax), r) for t in times]


BURSTS1 = make_bursts([0.6, 1.3, 2.0, 2.6, 3.3, 3.9, 4.6, 5.2, 5.9, 6.5, 7.1], 11)
_ft = []
_t = 47.3
_fr = random.Random(5)
while _t < 58.8:
    _ft.append(_t)
    _t += _fr.uniform(0.18, 0.42) if _t > 52 else _fr.uniform(0.3, 0.6)
BURSTS5 = make_bursts(_ft, 12, 80, 300)
ALL_BURSTS = BURSTS1 + BURSTS5


def fireworks(frame, t, bursts):
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    sky_light = np.zeros(3, np.float32)
    for b in bursts:
        if t < b.t0 - 0.9 or t > b.t0 + b.life:
            continue
        if t < b.t0:
            p = (t - (b.t0 - 0.9)) / 0.9
            for j in range(6):
                q = max(p - j * 0.025, 0)
                e = 1 - (1 - q) ** 2
                y = H + 20 + (b.y - H - 20) * e
                x = b.x + math.sin(q * 9) * 4
                c = int(255 * (1 - j / 6))
                d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(c, int(c * 0.8), int(c * 0.5)))
            continue
        a = t - b.t0
        life = 1 - a / b.life
        br = life ** 1.5
        if a > b.life * 0.45:
            br *= 0.55 + 0.45 * np.sin(a * 38 + b.ph)
        else:
            br = np.full(b.n, br, np.float32)
        p1, p0 = b.pos(a), b.pos(a - (0.22 if b.kind == "willow" else 0.12))
        for i in range(b.n):
            c = (b.col if i % 2 else b.col2) * max(float(br[i]), 0) * 255
            d.line([tuple(p0[i]), tuple(p1[i])], fill=tuple(int(v) for v in c), width=2)
        if a < 0.12:
            rr = 16 * (1 - a / 0.12)
            c = tuple(int(255 * v * (1 - a / 0.12)) for v in (1, 0.95, 0.85))
            d.ellipse([b.x - rr, b.y - rr, b.x + rr, b.y + rr], fill=c)
        sky_light += b.col * 0.025 * math.exp(-a * 3.0)
    arr = np.asarray(layer, np.float32) / 255
    g1 = np.asarray(layer.filter(ImageFilter.GaussianBlur(5)), np.float32) / 255
    small = layer.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(6))
    g2 = np.asarray(small.resize((W, H), Image.BILINEAR), np.float32) / 255
    frame += arr + g1 * 1.8 + g2 * 2.5 + sky_light


# ---------------------------------------------------------------- 五星红旗
def build_flag(fw):
    s = 2
    Wf, Hf = fw * s, fw * 2 // 3 * s
    u = Hf / 20
    img = Image.new("RGB", (Wf, Hf), (222, 30, 36))
    d = ImageDraw.Draw(img)

    def star(cx, cy, R, rot):
        pts = []
        for k in range(10):
            rad = R if k % 2 == 0 else R * 0.381966
            a = rot + k * math.pi / 5
            pts.append((cx * u + rad * u * math.cos(a), cy * u + rad * u * math.sin(a)))
        d.polygon(pts, fill=(255, 222, 0))

    star(5, 5, 3, -math.pi / 2)
    for cx, cy in ((10, 2), (12, 4), (12, 7), (10, 9)):
        star(cx, cy, 1, math.atan2(5 - cy, 5 - cx))
    img = img.resize((fw, fw * 2 // 3), Image.LANCZOS)
    return np.asarray(img, np.float32) / 255


FLAG = build_flag(720)


def waving_flag(frame, t, fx, fy):
    Hf, Wf = FLAG.shape[:2]
    A = 20
    xs = np.arange(Wf)
    u = xs / Wf
    phase = xs * 2 * np.pi / 380 - t * 2 * np.pi * 0.85
    amp = A * (0.12 + 0.88 * u)
    disp = amp * np.sin(phase) + 6 * np.sin(xs * 2 * np.pi / 170 - t * 5.1) * u
    shade = (1 + 0.3 * np.cos(phase) * (0.15 + 0.85 * u)).astype(np.float32)
    yd = np.arange(Hf + 2 * A)[:, None]
    sy = yd - A - disp[None, :] - u[None, :] * 14
    y0 = np.floor(sy).astype(int)
    fy_ = (sy - y0)[..., None].astype(np.float32)
    y0c, y1c = np.clip(y0, 0, Hf - 1), np.clip(y0 + 1, 0, Hf - 1)
    samp = FLAG[y0c, xs] * (1 - fy_) + FLAG[y1c, xs] * fy_
    alpha = np.clip(np.minimum(sy + 0.5, Hf - 0.5 - sy), 0, 1)[..., None].astype(np.float32)
    col = samp * shade[None, :, None]
    y_top = fy - A
    region = frame[y_top:y_top + Hf + 2 * A, fx:fx + Wf]
    region[:] = region * (1 - alpha) + col * alpha


# ---------------------------------------------------------------- 各场景
def scene_open(t):
    """1949—2026：夜空烟花与城市天际线。"""
    f = NIGHT.copy()
    add_stars(f, t)
    fireworks(f, t, BURSTS1)
    add_skyline(f)
    text(f, "1949—2026", 118, W / 2, 285 - 25 * (1 - fade(t, 1.2, 2.4)), fade(t, 1.2, 2.4),
         bold=3, glow=14, glow_amt=0.9)
    text(f, "热烈庆祝中华人民共和国成立77周年", 50, W / 2, 410, fade(t, 2.8, 3.8),
         color=(1.0, 0.95, 0.88), glow_col=(1.0, 0.3, 0.2), glow_amt=0.8)
    return f


DAWN = vgrad((0.10, 0.20, 0.45), (1.0, 0.70, 0.40))
RAY_THETA = np.arctan2(YY - 60, XX - 1150)
RAY_DIST = np.hypot(YY - 60, XX - 1150)


def scene_flag(t):
    """五星红旗迎着朝阳飘扬。"""
    f = DAWN.copy()
    rays = (0.5 + 0.5 * np.cos(20 * RAY_THETA + t * 0.4)) ** 4 * np.exp(-RAY_DIST / 900) * 0.16
    f += rays[..., None] * np.array([1.0, 0.85, 0.5], np.float32)
    f += np.exp(-RAY_DIST ** 2 / 90000)[..., None] * np.array([1.0, 0.9, 0.6], np.float32) * 0.6
    # 旗杆
    px = 262
    f[80:, px - 5:px + 5] = np.linspace(0.55, 0.9, 10, dtype=np.float32)[None, :, None] * np.array(
        [0.95, 0.9, 0.85], np.float32)
    f[70:82, px - 8:px + 8] = np.array([1.0, 0.8, 0.3], np.float32)
    waving_flag(f, t, px + 5, 115)
    text(f, "山河锦绣  国泰民安", 50, W / 2, 668, fade(t, 9.0, 10.2),
         shadow=0.6, glow_col=(1, 0.5, 0.2), glow_amt=0.3)
    return f


REDBG = None
_d = np.hypot((XX - W / 2) / W, (YY - H * 0.35) / H)
REDBG = (np.array([0.78, 0.07, 0.07], np.float32) * (1 - np.clip(_d * 1.4, 0, 1))[..., None]
         + np.array([0.30, 0.00, 0.03], np.float32) * np.clip(_d * 1.4, 0, 1)[..., None])
R3_THETA = np.arctan2(YY + 150, XX - W / 2)
R3_DIST = np.hypot(YY + 150, XX - W / 2)
DUST = [(rng.uniform(0, W), rng.uniform(0, H + 40), rng.uniform(15, 45), rng.uniform(0.2, 0.7))
        for _ in range(110)]
_stamp = np.exp(-((np.arange(7) - 3)[:, None] ** 2 + (np.arange(7) - 3)[None, :] ** 2) / 3.0)
DUST_STAMP = _stamp.astype(np.float32)[..., None] * np.array([1.0, 0.8, 0.4], np.float32)


def add_dust(f, t):
    for x, y0, sp, b in DUST:
        y = (y0 - sp * t) % (H + 40) - 20
        xi, yi = int(x + 12 * math.sin(t * 0.7 + y0)) - 3, int(y) - 3
        if 0 <= xi < W - 7 and 0 <= yi < H - 7:
            f[yi:yi + 7, xi:xi + 7] += DUST_STAMP * b


MILESTONES = [
    ("1921", "中国共产党成立  开天辟地"),
    ("1949", "中华人民共和国成立"),
    ("1964", "第一颗原子弹爆炸成功"),
    ("1978", "改革开放  伟大转折"),
    ("1997", "香港回归祖国"),
    ("2008", "北京奥运会成功举办"),
    ("2021", "全面建成小康社会"),
    ("2026", "“十五五”开局  奋进新征程"),
]
T3, STEP = 15.0, 2.5
TL_X0, TL_X1, TL_Y = 150, 1130, 604


def scene_timeline(t):
    """百年征程时间轴。"""
    f = REDBG.copy()
    rays = (0.5 + 0.5 * np.cos(28 * R3_THETA - t * 0.5)) ** 5 * np.exp(-R3_DIST / 1100) * 0.16
    f += rays[..., None] * np.array([1.0, 0.75, 0.35], np.float32)
    add_dust(f, t)
    lt = t - T3
    i = int(min(max(lt // STEP, 0), len(MILESTONES) - 1))
    p = lt - i * STEP
    ein = fade(p, 0, 0.55)
    eout = 1 - fade(p, 2.15, 2.5) if i < len(MILESTONES) - 1 else 1.0
    a = ein * eout
    year, cap = MILESTONES[i]
    text(f, "百年征程  波澜壮阔", 32, W / 2, 78, fade(t, 15.2, 16.0),
         color=(1.0, 0.9, 0.7), glow_amt=0.25, shadow=0.4, bold=1)
    text(f, year, 190, W / 2, 262 - 36 * (1 - ein), a, bold=4, glow=16, glow_amt=0.8,
         shadow=0.5)
    text(f, cap, 54, W / 2, 452 + 20 * (1 - ein), a, color=(1.0, 0.96, 0.9),
         glow_col=(1.0, 0.6, 0.3), glow_amt=0.35, shadow=0.55)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    lw = 230 * ein
    d.rectangle([W / 2 - lw, 368, W / 2 + lw, 370], fill=(255, 210, 110, int(230 * a)))
    n = len(MILESTONES)
    xs = [TL_X0 + (TL_X1 - TL_X0) * k / (n - 1) for k in range(n)]
    prog = xs[i] if i == 0 else xs[i - 1] + (xs[i] - xs[i - 1]) * fade(p, 0, 0.6)
    tla = int(255 * fade(t, 15.3, 16.2))
    d.line([(TL_X0, TL_Y), (TL_X1, TL_Y)], fill=(255, 200, 150, tla // 3), width=2)
    d.line([(TL_X0, TL_Y), (prog, TL_Y)], fill=(255, 215, 110, tla), width=4)
    for k, x in enumerate(xs):
        yr = MILESTONES[k][0]
        if k < i or (k == i and p > 0.5):
            r = 7 + (4 + 2 * math.sin(t * 6) if k == i else 0)
            d.ellipse([x - r, TL_Y - r, x + r, TL_Y + r], fill=(255, 215, 110, tla))
        else:
            d.ellipse([x - 6, TL_Y - 6, x + 6, TL_Y + 6], outline=(255, 200, 150, tla // 2), width=2)
        tf = font(22)
        c = (255, 225, 150, tla) if k <= i else (255, 200, 170, tla // 2)
        d.text((x, TL_Y + 28), yr, font=tf, fill=c, anchor="mm")
    overlay(f, ov)
    return f


# 长城日出
LW = 1760
_lx = np.arange(LW, dtype=np.float32)


def ridge(base, amps, seed):
    r = np.random.default_rng(seed)
    y = np.full(LW, base, np.float32)
    for a in amps:
        y += a * np.sin(_lx * r.uniform(0.6, 1.4) * 2 * np.pi / (a * 9) + r.uniform(0, 6.3))
    return y


def layer_mask(rdg):
    return np.clip(np.arange(H, dtype=np.float32)[:, None] - rdg[None, :] + 0.5, 0, 1)


FAR = layer_mask(ridge(430, [40, 18, 7], 1))
MID = layer_mask(ridge(505, [45, 20, 6], 2))
_near = ridge(585, [55, 22, 5], 3)


def build_wall():
    im = Image.new("L", (LW, H), 0)
    d = ImageDraw.Draw(im)
    top = [(x, float(_near[x]) - 26) for x in range(LW)]
    d.polygon(top + [(LW, H), (0, H)], fill=255)
    for x in range(0, LW - 8, 15):
        y = float(max(_near[x:x + 9])) - 26
        d.rectangle([x, y - 8, x + 8, y + 2], fill=255)
    for x in range(120, LW - 60, 360):
        y = float(min(_near[x:x + 56]))
        d.rectangle([x, y - 78, x + 56, y], fill=255)
        for cx in range(x, x + 56, 12):
            d.rectangle([cx, y - 88, cx + 7, y - 78], fill=255)
        for wx in (x + 10, x + 34):
            d.rectangle([wx, y - 58, wx + 11, y - 40], fill=0)
            d.pieslice([wx, y - 64, wx + 11, y - 52], 180, 360, fill=0)
    return np.maximum(np.asarray(im, np.float32) / 255, layer_mask(_near))


NEAR = build_wall()
_k = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
SUNSKY = np.broadcast_to(np.where(
    _k < 0.6,
    np.array([0.16, 0.10, 0.30], np.float32) * (1 - _k / 0.6) + np.array([1.0, 0.60, 0.30], np.float32) * (_k / 0.6),
    np.array([1.0, 0.60, 0.30], np.float32) * (1 - (_k - 0.6) / 0.4) + np.array([1.0, 0.78, 0.45], np.float32) * ((_k - 0.6) / 0.4)),
    (H, W, 3)).copy()
T4 = 35.0


def scene_wall(t):
    """长城日出 + 初心使命。"""
    lt = t - T4
    f = SUNSKY.copy()
    sx, sy = 820, 520 - 150 * sm(lt / 11)
    d2 = (XX - sx) ** 2 + (YY - sy) ** 2
    f += np.exp(-d2 / 60000)[..., None] * np.array([1.0, 0.7, 0.35], np.float32) * 0.55
    f += np.exp(-d2 / 6000)[..., None] * np.array([1.0, 0.85, 0.5], np.float32) * 0.6
    disc = np.clip(62 - np.sqrt(d2), 0, 1)[..., None]
    f[:] = f * (1 - disc) + np.array([1.0, 0.93, 0.7], np.float32) * disc
    off = 25 * lt
    for m, col, par in ((FAR, (0.62, 0.34, 0.36), 0.3), (MID, (0.36, 0.15, 0.20), 0.6),
                        (NEAR, (0.10, 0.035, 0.06), 1.0)):
        o = int(min(max(off * par, 0), LW - W))
        mm = m[:, o:o + W][..., None]
        f[:] = f * (1 - mm) + np.array(col, np.float32) * mm
        f += np.exp(-((YY - 470 - par * 70) / 45) ** 2)[..., None] * 0.035 * par
    text(f, "没有共产党  就没有新中国", 62, W / 2, 112 + 20 * (1 - fade(t, 36.0, 37.0)),
         fade(t, 36.0, 37.0), bold=3, glow=12, glow_col=(1.0, 0.35, 0.15), glow_amt=0.6,
         shadow=0.35)
    text(f, "不忘初心  牢记使命", 50, W / 2, 196, fade(t, 38.5, 39.4),
         color=(1.0, 0.97, 0.9), glow_col=(1.0, 0.45, 0.2), glow_amt=0.45, shadow=0.35)
    text(f, "为中国人民谋幸福  为中华民族谋复兴", 42, W / 2, 262, fade(t, 41.0, 41.9),
         color=(1.0, 0.97, 0.9), glow_col=(1.0, 0.45, 0.2), glow_amt=0.45, shadow=0.35)
    return f


FINALE_SKY = vgrad((0.02, 0.01, 0.06), (0.26, 0.04, 0.08))


def scene_finale(t):
    """万家灯火，烟花满天，国庆快乐。"""
    f = FINALE_SKY.copy()
    add_stars(f, t, 0.7)
    fireworks(f, t, BURSTS5)
    add_skyline(f)
    out = 1 - fade(t, 55.6, 56.3)
    text(f, "祝福伟大祖国  繁荣昌盛", 58, W / 2, 150, fade(t, 48.3, 49.3) * out,
         color=(1.0, 0.96, 0.88), glow_col=(1.0, 0.3, 0.2), glow_amt=0.8, bold=3)
    s = fade(t, 50.8, 51.8)
    text(f, "国庆快乐", 150, W / 2, 335 + 30 * (1 - s), s, bold=4, glow=20, glow_amt=1.0,
         glow_col=(1.0, 0.3, 0.1))
    text(f, "2026 · 10 · 1", 44, W / 2, 468, fade(t, 56.0, 56.9),
         color=(1.0, 0.9, 0.7), glow_amt=0.5)
    return f


SCENES = [(0.0, 7.0, scene_open), (7.0, 15.0, scene_flag), (15.0, 35.0, scene_timeline),
          (35.0, 47.0, scene_wall), (47.0, DUR, scene_finale)]
XF = 0.6


def render(t):
    for i, (a, b, fn) in enumerate(SCENES):
        if a <= t < b or i == len(SCENES) - 1:
            break
    if i + 1 < len(SCENES) and t > b - XF:
        w = sm((t - (b - XF)) / (2 * XF))
        f = fn(t) * (1 - w) + SCENES[i + 1][2](t) * w
    elif i > 0 and t < a + XF:
        w = sm((t - (a - XF)) / (2 * XF))
        f = SCENES[i - 1][2](t) * (1 - w) + fn(t) * w
    else:
        f = fn(t)
    f *= fade(t, 0, 1.0) * (1 - fade(t, DUR - 1.3, DUR))
    return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------- 配乐与音效（原创旋律）
def synth_audio():
    n = int(SR * DUR)
    L = np.zeros(n, np.float32)
    ts_cache = {}

    def tt(sec):
        m = int(sec * SR)
        if m not in ts_cache:
            ts_cache[m] = np.arange(m, dtype=np.float32) / SR
        return ts_cache[m]

    def add(buf, start, sig, gain=1.0):
        i = int(start * SR)
        if i < 0:
            sig, i = sig[-i:], 0
        if i >= n:
            return
        j = min(n, i + len(sig))
        buf[i:j] += sig[: j - i] * gain

    def mhz(m):
        return 440.0 * 2 ** ((m - 69) / 12)

    PENT = [0, 2, 4, 7, 9]

    def deg(dg):
        return 62 + 12 * (dg // 5) + PENT[dg % 5]

    def pluck(freq, dur):
        x = tt(dur + 1.2)
        s = sum(np.sin(2 * np.pi * freq * h * x) / h ** 1.2 * np.exp(-x * (2.2 + 0.9 * h))
                for h in range(1, 7))
        return s * np.minimum(x / 0.004, 1)

    def lead(freq, dur):
        x = tt(dur + 0.15)
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.6 * x) * np.minimum(x / 0.3, 1)
        ph = 2 * np.pi * freq * np.cumsum(vib) / SR
        s = np.sin(ph) + 0.35 * np.sin(2 * ph) + 0.18 * np.sin(3 * ph) + 0.08 * np.sin(4 * ph)
        env = np.minimum(x / 0.035, 1) * np.clip((dur + 0.15 - x) / 0.15, 0, 1)
        return s * env

    def pad(midis, dur):
        x = tt(dur + 0.6)
        s = np.zeros_like(x)
        for m in midis:
            f0 = mhz(m)
            for det in (0.998, 1.002):
                s += np.sin(2 * np.pi * f0 * det * x) + 0.25 * np.sin(4 * np.pi * f0 * det * x)
        env = np.minimum(x / 0.45, 1) * np.clip((dur + 0.6 - x) / 0.6, 0, 1)
        return s * env

    def drum(dur=0.9):
        x = tt(dur)
        f = 45 + 80 * np.exp(-x * 18)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 5.5)

    def noise(dur, seed):
        return np.random.default_rng(seed).standard_normal(int(dur * SR)).astype(np.float32)

    def lowpass(sig, k):
        return np.convolve(sig, np.ones(k, np.float32) / k, mode="same")

    BEAT = 0.6
    BAR = 4 * BEAT
    A = [(5, 1), (4, .5), (3, .5), (2, 1), (3, 1), (5, 1.5), (6, .5), (5, 2),
         (3, 1), (4, .5), (5, .5), (6, 1), (5, .5), (4, .5), (3, 3), (None, 1)]
    B = [(2, 1), (3, .5), (5, .5), (4, 1), (3, 1), (2, 1), (1, .5), (2, .5), (3, 2),
         (5, 1), (6, 1), (7, 1), (6, .5), (5, .5), (5, 3), (None, 1)]
    C = [(8, 1.5), (7, .5), (6, 1), (5, 1), (6, 1), (7, .5), (8, .5), (7, 2),
         (5, 1), (6, .5), (5, .5), (3, 1), (4, 1), (5, 4)]
    CH = {"D": (50, 0), "Bm": (47, 1), "G": (43, 0), "A": (45, 0)}
    chords = {"A": ["D", "Bm", "G", "A"], "B": ["D", "A", "G", "D"], "C": ["D", "A", "G", "D"]}
    form = ["A", "B", "A", "C", "C"]
    mel = {"A": A, "B": B, "C": C}

    bar_chords = ["D", "D"] + [c for s in form for c in chords[s]] + ["D", "D", "D"]
    t = 2 * BAR
    for si, sec in enumerate(form):
        up = 12 if si == 4 else 0
        for dg, bl in mel[sec]:
            if dg is not None:
                m = deg(dg) + up * 0
                add(L, t, lead(mhz(m), bl * BEAT), 0.13)
                add(L, t, pluck(mhz(m), bl * BEAT), 0.10)
                if si >= 3:
                    add(L, t, lead(mhz(m - 12 + 7 if dg % 5 else m - 12), bl * BEAT), 0.05)
            t += bl * BEAT
    # 结尾分解和弦
    for k, m in enumerate((62, 66, 69, 74, 78, 81, 86)):
        add(L, t + k * 0.12, pluck(mhz(m), 3.0), 0.09)
    for b, name in enumerate(bar_chords):
        root, minor = CH[name]
        tri = [root + 12, root + 12 + (3 if minor else 4), root + 19]
        last = b == len(bar_chords) - 3
        dur = BAR * 3 if last else BAR
        if b > len(bar_chords) - 3:
            continue
        add(L, b * BAR, pad(tri, dur), 0.022)
        add(L, b * BAR, pluck(mhz(root - 12), BEAT), 0.16)
        add(L, b * BAR + 2 * BEAT, pluck(mhz(root - 12 + 7), BEAT), 0.10)
        add(L, b * BAR, drum(), 0.42)
        if not last:
            add(L, b * BAR + 2 * BEAT, drum(), 0.26)
            for e in range(8):
                x = tt(0.08)
                hit = noise(0.08, b * 8 + e) * np.exp(-x * 60) * 0.5 + np.sin(2 * np.pi * 220 * x) * np.exp(-x * 40)
                add(L, b * BAR + e * BEAT / 2, hit, 0.05 if e % 2 else 0.035)
        if (b - 1) % 4 == 0 and b >= 5 and not last:
            for e in range(4):
                add(L, b * BAR + 3 * BEAT + e * BEAT / 4, drum(0.3), 0.2 + 0.05 * e)
    for ct in (7.0, 15.0, 35.0, 47.0, 50.8):
        nz = noise(2.5, int(ct * 10))
        x = tt(2.5)
        crash = (nz - lowpass(nz, 6)) * np.exp(-x * 1.6)
        add(L, ct - 0.02, crash, 0.12)
        add(L, ct, drum(1.4), 0.4)
    # 烟花：哨音 + 爆炸 + 噼啪声
    for bi, b in enumerate(ALL_BURSTS):
        x = tt(0.85)
        fw = 1500 + 1300 * x / 0.85
        whistle = np.sin(2 * np.pi * np.cumsum(fw) / SR) * np.minimum(x / 0.2, 1) * (1 - x / 0.85)
        add(L, b.t0 - 0.88, whistle, 0.012)
        nz = noise(1.4, 1000 + bi)
        x = tt(1.4)
        boom = lowpass(nz, 40) * np.exp(-x * 4.0) * 3 + nz * np.exp(-x * 25) * 0.3
        add(L, b.t0, boom, 0.14 * b.loud)
        r = random.Random(bi)
        for _ in range(30 if b.kind == "willow" else 18):
            ct = b.t0 + r.uniform(0.5, b.life * 0.9)
            x = tt(0.02)
            add(L, ct, noise(0.02, r.randint(0, 10 ** 6)) * np.exp(-x * 300), 0.05 * b.loud)
    # 简易混响与立体声
    R = L.copy()
    for dl, g in ((0.071, 0.28), (0.113, 0.22), (0.187, 0.16), (0.293, 0.1)):
        k = int(dl * SR)
        L[k:] += L[:-k] * g * 0.9
        k2 = int(dl * 1.13 * SR)
        R[k2:] += R[:-k2] * g
    st = np.stack([L, R], 1)
    st /= np.abs(st).max() + 1e-6
    st = np.tanh(st * 1.3) * 0.88
    x = np.arange(n) / SR
    env = np.clip(x / 0.5, 0, 1) * np.clip((DUR - x) / 2.0, 0, 1)
    st *= env[:, None]
    path = os.path.join(HERE, "_audio.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())
    return path


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        for s in sys.argv[2:]:
            Image.fromarray(render(float(s))).save(os.path.join(HERE, f"_preview_{s}.png"))
        return
    audio = synth_audio()
    out = os.path.join(HERE, "national_day_2026.mp4")
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", audio, "-c:v", "libx264", "-preset", "slow", "-crf", "21",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    total = int(DUR * FPS)
    for k in range(total):
        proc.stdin.write(render(k / FPS).tobytes())
        if k % 150 == 0:
            print(f"frame {k}/{total}", flush=True)
    proc.stdin.close()
    proc.wait()
    os.remove(audio)
    print("done:", out)


if __name__ == "__main__":
    main()
