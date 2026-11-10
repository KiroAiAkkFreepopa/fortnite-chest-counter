import base64
import ctypes
import datetime
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

# ---------------- Константы ----------------
RESET_CONFIRM_SEC = 3.0   # время на подтверждение сброса
PANEL_W = 290             # базовая ширина панели при масштабе 1.0
MAX_COUNT = 99999
SCALE_MIN, SCALE_MAX = 0.7, 2.2
PINNED_ALPHA_CAP = 0.85   # в режиме закрепления чуть прозрачнее

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312
MODS = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT

# Настраиваемые действия: имя -> id горячей клавиши
HK_IDS = {"open": 1}
# Фиксированные действия: id -> (имя, клавиша). Модификаторы — Ctrl+Alt.
FIXED_HOTKEYS = {
    3: ("undo", ord("Z")),    # Ctrl+Alt+Z — отмена последнего
    4: ("reset", ord("R")),   # Ctrl+Alt+R — сброс (дважды)
    5: ("quit", ord("Q")),    # Ctrl+Alt+Q — выход
    6: ("pin", ord("P")),     # Ctrl+Alt+P — закрепить / открепить
    7: ("auto", ord("A")),    # Ctrl+Alt+A — автоподсчёт вкл / выкл
    8: ("map", ord("M")),     # Ctrl+Alt+M — мини-карта вкл / выкл
}
FIXED_LOOKUP = {(MOD_CONTROL | MOD_ALT, vk): name
                for _hid, (name, vk) in FIXED_HOTKEYS.items()}
ACTION_RU = {"open": "открытые", "undo": "отмена", "menu": "меню",
             "reset": "сброс", "quit": "выход", "pin": "закрепить",
             "auto": "автоподсчёт", "map": "карта"}

# ---------------- Названия клавиш <-> виртуальные коды ----------------
VK_NAMES = {}
for _i in range(10):
    VK_NAMES[0x30 + _i] = str(_i)           # 0-9
    VK_NAMES[0x60 + _i] = f"Num{_i}"        # цифровой блок
for _i in range(26):
    VK_NAMES[0x41 + _i] = chr(65 + _i)      # A-Z
for _i in range(1, 25):
    VK_NAMES[0x6F + _i] = f"F{_i}"          # F1-F24
VK_NAMES.update({
    0x6A: "NumMul", 0x6B: "NumAdd", 0x6D: "NumSub", 0x6E: "NumDec", 0x6F: "NumDiv",
    0x20: "Space", 0x09: "Tab", 0x2D: "Insert", 0x2E: "Delete",
    0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})
NAME_TO_VK = {v: k for k, v in VK_NAMES.items()}
MOD_FLAGS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT}
MOD_ORDER = ("Ctrl", "Alt", "Shift")
MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    "Super_L", "Super_R", "Win_L", "Win_R", "Meta_L", "Meta_R",
    "Caps_Lock", "Num_Lock", "ISO_Level3_Shift",
}


def is_free_key(name):
    """Клавиши, которые разрешено занимать без Ctrl/Alt/Shift."""
    return (name[:1] == "F" and name[1:].isdigit()) or name.startswith("Num")


def parse_combo(text):
    """'Ctrl+Alt+1' -> (флаги модификаторов, vk) или None."""
    if not isinstance(text, str) or not text:
        return None
    parts = text.split("+")
    key, mods = parts[-1], parts[:-1]
    if key not in NAME_TO_VK or len(set(mods)) != len(mods):
        return None
    flags = 0
    for m in mods:
        if m not in MOD_FLAGS:
            return None
        flags |= MOD_FLAGS[m]
    return flags, NAME_TO_VK[key]


def valid_hotkey(text):
    p = parse_combo(text)
    if p is None or p in FIXED_LOOKUP:
        return False
    return bool(p[0]) or is_free_key(VK_NAMES[p[1]])


APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "FortniteChestCounter"
)
STATE_FILE = os.path.join(APP_DIR, "state.json")

FONT = "Segoe UI"
AMBER = "#ffc24a"
ALERT = "#ff4f5e"

THEMES = {
    "Тёмная": dict(bg="#0d0f14", card="#171a21", card_hi="#1f232c",
                   border="#2a2f3a", fg="#f4f6fa", dim="#7d8594"),
    "Чёрная": dict(bg="#000000", card="#0e0e10", card_hi="#1a1a1e",
                   border="#2a2a30", fg="#ffffff", dim="#80808a"),
    "Графит": dict(bg="#1c1e22", card="#25282e", card_hi="#2f333b",
                   border="#3a3f49", fg="#f1f3f6", dim="#9aa1ad"),
    "Синяя": dict(bg="#0a1020", card="#111a30", card_hi="#18233f",
                  border="#25345a", fg="#eef3ff", dim="#7f90b5"),
    "Неон": dict(bg="#0b0a14", card="#141225", card_hi="#1e1b35",
                 border="#322d52", fg="#f3f0ff", dim="#8e88b0"),
    "Океан": dict(bg="#06121a", card="#0c1d29", card_hi="#122b3c",
                  border="#1f4058", fg="#eaf6ff", dim="#7ea3bd"),
    "Рассвет": dict(bg="#150a10", card="#21111b", card_hi="#2d1826",
                    border="#4a2840", fg="#fff1f5", dim="#bb8c9e"),
}
ACCENT2 = "#a78bfa"          # второй цвет градиентов в меню
PALETTE = ["#34e07a", "#22d3ee", "#60a5fa", "#a78bfa",
           "#f472b6", "#ff4f5e", "#fb923c", "#facc15"]

DEFAULTS = {
    "open": 0, "x": 20, "y": 20, "pinned": False,
    "scale": 1.0, "alpha": 0.92, "radius": 18, "cooldown": 1.5,
    "show_bar": False, "theme": "Тёмная", "auto": False,
    "auto_mode": "game",         # "game" — по плашке в игре, "replay" — по реплею после игры
    "days": {},                  # {"2026-11-10": 7} — сколько открыто за день
    "games": [],                 # [["2026-11-10 21:05:00", 12], ...] — сундуки за игру (только реплей)
    "replays_done": [],          # уже посчитанные реплеи
    "panel": True,
    "menu_x": -1, "menu_y": -1,
    "notify": False, "notify_x": -1, "notify_y": -1,
    "notify_sec": 2.0, "notify_scale": 1.0,
    "col_open": "#34e07a",
    "hk_open": "Ctrl+Alt+1",
    "capture_visible": True,     # видно на скриншотах и в трансляции экрана
    "obs_window": False,         # отдельное окно для захвата в OBS
    "obs_geo": "",               # его размер и положение, «ШxВ+X+Y»
    "minimap": False,            # мини-карта сундуков поверх игры
    "map_x": -1, "map_y": -1,    # её положение (-1 — по умолчанию, справа)
    "map_w": 420,                # ширина карты в пикселях (высота по пропорциям)
    "map_alpha": 0.92,
    "map_lock": False,           # закреплена: клики проходят сквозь карту
    "map_mode": "radar",         # radar — карта едет за игроком; full — вся карта
    "map_r": 300,                # сторона радара в пикселях
    "map_zoom": 1.0,             # 1.0 — приближение как у миникарты игры
    "map_track": True,           # следить за игроком по внутриигровой миникарте
}
STYLE_KEYS = ("theme", "col_open", "alpha", "radius", "cooldown", "show_bar")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
GEO_RE = re.compile(r"^\d{2,4}x\d{2,4}[+-]-?\d{1,5}[+-]-?\d{1,5}$")
# python fortnite_counter.py --obs — сразу открыть окно для OBS
OBS_FLAG = "--obs" in sys.argv[1:]

# Текущая тема (обновляется при смене настроек)
T = dict(THEMES["Тёмная"])
T["open"] = DEFAULTS["col_open"]


def apply_theme(state):
    T.update(THEMES.get(state["theme"], THEMES["Тёмная"]))
    T["open"] = state["col_open"]


def on_color(hexc):
    """Тёмный или белый текст — что читается на заданном цвете."""
    try:
        r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    except ValueError:
        return "#0a0a0a"
    return "#0a0a0a" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


def kcolor():
    return T["open"]


# ---------------- Windows API ----------------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

if os.name == "nt":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.UINT]
    user32.RedrawWindow.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def combo_from_event(e):
    """Событие клавиши Tk -> ('Ctrl+Alt+1', None) или (None, 'ошибка')."""
    name = VK_NAMES.get(e.keycode)
    if name is None:
        return None, "Эта клавиша не поддерживается"
    mods = [m for m, vk in (("Ctrl", 0x11), ("Alt", 0x12), ("Shift", 0x10))
            if key_down(vk)]
    if not mods and not is_free_key(name):
        return None, "Добавьте Ctrl/Alt/Shift (без них — только F1–F24 и Num)"
    return "+".join(mods + [name]), None


# ---------------- Автоподсчёт по картинке экрана ----------------
try:
    import cv2
    import numpy as np
    AUTO_ERR = None
except Exception:  # noqa: BLE001
    cv2 = np = None
    AUTO_ERR = "Автоподсчёт: pip install opencv-python numpy mss"

try:
    import mss as _mss
except Exception:  # noqa: BLE001
    _mss = None

AUTO_OK = AUTO_ERR is None
AUTO_SAMPLE_SEC = 0.25
AUTO_WIDTH = 1280
AUTO_LOG = os.path.join(APP_DIR, "auto_log.txt")
AUTO_LOG_MAX = 200_000
PROMPT_MIN_PROGRESS = 0.5    # насколько заполнено кольцо E, чтобы считать «открыл»


def _log(text):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(AUTO_LOG) and os.path.getsize(AUTO_LOG) > AUTO_LOG_MAX:
            os.remove(AUTO_LOG)
        with open(AUTO_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + text + "\n")
    except Exception:  # noqa: BLE001
        pass


# ---- Детектор надписи «+N ОП / СУНДУК» -------------------------------------
# Ищем не цвет, а ФОРМУ белого слова «СУНДУК» под числом опыта (шаблон
# вырезан из реальных скриншотов). Любая другая подпись («ВЫЖИВШИЙ»,
# «ЭЛИМИНАЦИЯ» и т.п.) выглядит иначе и не засчитывается.
POPUP_THR = 0.80                       # на скриншотах: слово 0.87-0.97, фон до 0.73
POPUP_REGION = (.45, .95, .15, .55)    # где ищем (доли кадра): x0, x1, y0, y1
POPUP_SCALES = (0.85, 0.92, 1.0, 1.08, 1.17)   # запас на другой масштаб HUD
USE_PROMPT_SIGNAL = False   # True — считать ещё и по 'E ОБЫСКАТЬ' (может давать лишнее)
_CHEST_B64 = "iVBORw0KGgoAAAANSUhEUgAAADUAAAARCAAAAABYsLuMAAABB0lEQVQoFZ3BQXUEQAhEwY+mRhNoAk20p8zmmpfLVgXfCL4R/JKw+SXMhwwU2HwI8yEDwcdIuH27aNqz5twwBU7VmnOrmlIDwaNRq7aP9tE+N+dGww5Rs63brgnODQTPKa3bnkrGrXFr3NS0x63bngqOnEoDwXNK89SslK7ZrnIzapVbg6uX08rNE4CGNo8OeqnxltKcu2abKZzUAGmeAHROpIUTaU52ka5q12wzRZoatnp5AtA5OaWZ6oUjOQVTvadtarahJhmSJwCNVuqFI6HKzbmnwMhJTS9MBUfyBE+VoUGyQWIZMzZITsYLSMtU8AT/qUmDxs0fwX9UDWjc/BF8I/hG8I0ft6msEsEBLIYAAAAASUVORK5CYII="
_CHEST_TPL = None


def _white_f(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 80) & (hsv[..., 2] > 185)).astype(np.float32)


def popup_score(bgr):
    """Насколько кадр похож на слово «СУНДУК» из всплывашки: 0..1."""
    global _CHEST_TPL
    import base64
    if _CHEST_TPL is None:
        t = cv2.imdecode(np.frombuffer(base64.b64decode(_CHEST_B64), np.uint8),
                         cv2.IMREAD_GRAYSCALE)
        _CHEST_TPL = t.astype(np.float32) / 255.0
    h, w = bgr.shape[:2]
    x0, x1 = int(POPUP_REGION[0] * w), int(POPUP_REGION[1] * w)
    y0, y1 = int(POPUP_REGION[2] * h), int(POPUP_REGION[3] * h)
    g = _white_f(bgr[y0:y1, x0:x1])
    gb = cv2.GaussianBlur(g, (3, 3), 0.8)
    base = h / 714.0
    best = 0.0
    for k in POPUP_SCALES:
        s = k * base
        tw = max(8, int(round(_CHEST_TPL.shape[1] * s)))
        th = max(4, int(round(_CHEST_TPL.shape[0] * s)))
        if g.shape[0] < th or g.shape[1] < tw:
            continue
        tpl = cv2.resize(_CHEST_TPL, (tw, th), interpolation=cv2.INTER_AREA)
        r = cv2.matchTemplate(gb, cv2.GaussianBlur(tpl, (3, 3), 0.8),
                              cv2.TM_CCOEFF_NORMED)
        # пустые/забитые белым окна дают ложные 1.0 — пропускаем их
        cnt = cv2.boxFilter(g, -1, (tw, th), normalize=False, anchor=(0, 0),
                            borderType=cv2.BORDER_CONSTANT)[:r.shape[0], :r.shape[1]]
        ts = float(tpl.sum())
        r = np.nan_to_num(r, nan=-1.0)
        r = np.where((cnt > 0.55 * ts) & (cnt < 1.8 * ts), r, -1.0)
        best = max(best, float(r.max()))
    return best


def detect_popup(bgr):
    """Всплыла надпись «+N ОП / СУНДУК»."""
    return popup_score(bgr) >= POPUP_THR


_PROMPT_B64 = "iVBORw0KGgoAAAANSUhEUgAAAFYAAAANCAAAAAAWdMYfAAAAu0lEQVQ4EbXBAXEEMQADMS1/0G6Sm0J4KT+Rn8hnjjBXTJ4Jc8RicoyYJ5N/eUYjJs/kmZhMTCZME3PE5F+eiYnJM3kmIxPT5Jgmx4TJv1wjJibPHGEy+UyTY5ocE+bIk2vExFwxV0wmnzlyLJZjwly5co2YmJhMTCaTzzRhYsKEyciRZ2JiYjIxmYyMTCPmCRMmJkee0YjJyGRkYjIxmZiYMGEycuQzR5gr5ooJc8TCZGLChLly5SfyE387zHQOq4aXjwAAAABJRU5ErkJggg=="
_PROMPT_TPL = None


def _white(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 60) & (hsv[..., 2] > 215)).astype(np.uint8) * 255


def detect_prompt(bgr):
    """Подсказка 'E ОБЫСКАТЬ' рядом с сундуком. Возвращает (есть, заполнение кольца 0..1).
    Работает на кадре шириной AUTO_WIDTH (1280)."""
    global _PROMPT_TPL
    import base64
    if _PROMPT_TPL is None:
        _PROMPT_TPL = cv2.imdecode(
            np.frombuffer(base64.b64decode(_PROMPT_B64), np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = bgr.shape[:2]
    x0, x1, y0, y1 = int(.45 * w), int(.80 * w), int(.25 * h), int(.75 * h)
    g = _white(bgr)
    res = cv2.matchTemplate(g[y0:y1, x0:x1], _PROMPT_TPL, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx < 0.6:
        return False, 0.0
    tx, ty = loc[0] + x0, loc[1] + y0
    best = 0.0
    for dx in range(-4, 5, 2):
        for dy in range(-4, 5, 2):
            cx, cy = tx - 30 + dx, ty + 6 + dy
            for R in (16, 17, 18, 19):
                hit = 0
                for a in range(0, 360, 5):
                    t = math.radians(a - 90)
                    x = int(round(cx + R * math.cos(t)))
                    y = int(round(cy + R * math.sin(t)))
                    if 0 <= y < g.shape[0] and 0 <= x < g.shape[1] and g[y, x]:
                        hit += 1
                best = max(best, hit / 72.0)
    return True, best


def analyze(bgr, with_prompt=USE_PROMPT_SIGNAL):
    pop = detect_popup(bgr)
    prm, prog = detect_prompt(bgr) if with_prompt else (False, 0.0)
    return pop, prm, prog


def _decode(path):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _shrink(bgr, width=None):
    width = width or AUTO_WIDTH
    h, w = bgr.shape[:2]
    if w > width:
        s = width / float(w)
        bgr = cv2.resize(bgr, (width, int(h * s)), interpolation=cv2.INTER_AREA)
    return bgr


def make_grabber(width=None):
    """Возвращает функцию, которая отдаёт кадр основного монитора (BGR)."""
    if _mss is not None:
        sct = _mss.mss()
        mon = sct.monitors[1]

        def grab():
            shot = sct.grab(mon)
            bgra = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
            tw = width or AUTO_WIDTH
            if shot.width > tw:           # сжимаем сразу в 4 каналах — без лишних копий
                bgra = cv2.resize(bgra, (tw, int(shot.height * tw / float(shot.width))),
                                  interpolation=cv2.INTER_AREA)
            return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        return grab
    from PIL import ImageGrab

    def grab():
        rgb = np.array(ImageGrab.grab().convert("RGB"))
        return _shrink(np.ascontiguousarray(rgb[:, :, ::-1]), width)
    return grab


def auto_worker(q, stop_evt, enabled_evt):
    """Поток: смотрит на экран и шлёт ('auto', 'open') в очередь.
    Сигналы: (1) 'E ОБЫСКАТЬ' удерживали (кольцо заполнилось) и подсказка пропала;
    (2) всплыло '+N ОП / СУНДУК' (сигнал 1 по умолчанию выключен). Один сундук = одно +1 (защита DEDUPE_SEC)."""
    try:
        grab = make_grabber()
    except Exception:  # noqa: BLE001
        q.put(("auto_error", "Не удалось захватить экран"))
        return
    DEDUPE_SEC = 4.0
    last_fire = -1e9
    prm_gap = 0
    max_prog = 0.0
    pop_run = pop_gap = 0
    pop_armed = True
    last_err = -1e9

    def fire(why):
        nonlocal last_fire
        now = time.monotonic()
        if now - last_fire < DEDUPE_SEC:
            return
        last_fire = now
        _log("открытый: " + why + " -> +1")
        q.put(("auto", "open"))

    while not stop_evt.is_set():
        if not enabled_evt.is_set():
            pop_run = pop_gap = prm_gap = 0
            max_prog = 0.0
            pop_armed = True
            time.sleep(0.3)
            continue
        t0 = time.monotonic()
        try:
            pop, prm, prog = analyze(grab())
        except Exception as e:  # noqa: BLE001
            if time.monotonic() - last_err > 30:
                last_err = time.monotonic()
                _log(f"ОШИБКА анализа кадра: {e!r}")
            time.sleep(1.0)
            continue
        # сигнал 1: подсказка была с заполненным кольцом, потом исчезла
        if prm:
            prm_gap = 0
            max_prog = max(max_prog, prog)
        else:
            prm_gap += 1
            if prm_gap == 2 and max_prog >= PROMPT_MIN_PROGRESS:
                fire("ОБЫСКАТЬ (кольцо %.2f)" % max_prog)
            if prm_gap >= 2:
                max_prog = 0.0
        # сигнал 2: надпись +ОП / СУНДУК два снимка подряд
        if pop:
            pop_run += 1
            pop_gap = 0
            if pop_armed and pop_run >= 2:
                pop_armed = False
                fire("надпись +ОП/СУНДУК")
        else:
            pop_run = 0
            pop_gap += 1
            if pop_gap >= 5:
                pop_armed = True
        time.sleep(max(0.0, AUTO_SAMPLE_SEC - (time.monotonic() - t0)))


def selftest(folder):
    """python fortnite_counter.py --selftest папка — что видит автоподсчёт."""
    if not AUTO_OK:
        raise SystemExit(AUTO_ERR)
    names = sorted(n for n in os.listdir(folder)
                   if n.lower().endswith((".png", ".jpg", ".jpeg")))
    for n in names:
        im = _decode(os.path.join(folder, n))
        if im is None:
            continue
        im = _shrink(im)
        sc = popup_score(im)
        _pop, prm, prog = analyze(im, with_prompt=True)
        print(f"{n}: СУНДУК={'ДА' if sc >= POPUP_THR else '-'} (схожесть {sc:.2f}, порог {POPUP_THR})"
              f"  ОБЫСКАТЬ={'ДА' if prm else '-'}  кольцо={prog:.2f}")


# ---------------- Состояние ----------------
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def to_int(text, default=0):
    try:
        return _clamp(int(str(text).strip()), 0, MAX_COUNT)
    except (ValueError, TypeError):
        return default


DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def day_bump(state, d, key=None):
    """Прибавить d к счётчику дня (по умолчанию сегодня). Хранится 120 последних дней."""
    k = key or time.strftime("%Y-%m-%d")
    n = max(0, state["days"].get(k, 0) + d)
    if n:
        state["days"][k] = n
    else:
        state["days"].pop(k, None)
    for old in sorted(state["days"])[:-120]:
        state["days"].pop(old, None)


def week_stats(state):
    """Сегодня, за неделю (пн–вс) и лучший день этой недели."""
    today = datetime.date.today()
    mon = today - datetime.timedelta(days=today.weekday())
    vals = [state["days"].get((mon + datetime.timedelta(days=i)).isoformat(), 0)
            for i in range(7)]
    return {"today": state["days"].get(today.isoformat(), 0),
            "week": sum(vals), "best": max(vals),
            "vals": vals, "idx": today.weekday()}


def game_stats(state):
    """Последняя игра и рекорд за игру (только то, что посчитано по реплею)."""
    g = state["games"]
    return (g[-1][1], max(x[1] for x in g)) if g else (None, None)


def load_state():
    st = dict(DEFAULTS)
    st["days"], st["games"], st["replays_done"] = {}, [], []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return st

    def num(key, cast, lo, hi):
        try:
            st[key] = _clamp(cast(d[key]), lo, hi)
        except Exception:
            pass

    num("open", int, 0, MAX_COUNT)
    num("x", int, -5000, 20000)
    num("y", int, -5000, 20000)
    num("scale", float, SCALE_MIN, SCALE_MAX)
    num("alpha", float, 0.3, 1.0)
    num("radius", int, 0, 30)
    num("cooldown", float, 0.5, 5.0)
    if d.get("theme") in THEMES:
        st["theme"] = d["theme"]
    v = d.get("col_open")
    if isinstance(v, str) and HEX_RE.match(v):
        st["col_open"] = v.lower()
    st["show_bar"] = False
    st["pinned"] = bool(d.get("pinned", False))
    st["auto"] = bool(d.get("auto", False))
    if d.get("auto_mode") in ("game", "replay"):
        st["auto_mode"] = d["auto_mode"]
    dd = d.get("days")
    if isinstance(dd, dict):
        st["days"] = {k: v for k, v in dd.items()
                      if isinstance(k, str) and DAY_RE.match(k)
                      and isinstance(v, int) and not isinstance(v, bool) and v > 0}
    gl = d.get("games")
    if isinstance(gl, list):
        st["games"] = [[g[0], g[1]] for g in gl
                       if isinstance(g, (list, tuple)) and len(g) == 2
                       and isinstance(g[0], str) and isinstance(g[1], int)
                       and not isinstance(g[1], bool) and g[1] >= 0][-300:]
    rd = d.get("replays_done")
    if isinstance(rd, list):
        st["replays_done"] = [x for x in rd if isinstance(x, str)][-300:]
    st["panel"] = bool(d.get("panel", True))
    st["capture_visible"] = bool(d.get("capture_visible", True))
    st["obs_window"] = bool(d.get("obs_window", False))
    g = d.get("obs_geo")
    if isinstance(g, str) and GEO_RE.match(g):
        st["obs_geo"] = g
    num("menu_x", int, -1, 20000)
    num("menu_y", int, -1, 20000)
    st["notify"] = bool(d.get("notify", False))
    num("notify_x", int, -1, 20000)
    num("notify_y", int, -1, 20000)
    num("notify_sec", float, 0.5, 8.0)
    num("notify_scale", float, 0.7, 1.8)
    st["minimap"] = bool(d.get("minimap", False))
    st["map_lock"] = bool(d.get("map_lock", False))
    num("map_x", int, -1, 20000)
    num("map_y", int, -1, 20000)
    num("map_w", int, 200, 2000)
    num("map_alpha", float, 0.3, 1.0)
    num("map_r", int, 160, 700)
    num("map_zoom", float, 0.4, 3.0)
    st["map_track"] = bool(d.get("map_track", True))
    if d.get("map_mode") in ("radar", "full"):
        st["map_mode"] = d["map_mode"]

    ho = d.get("hk_open")
    if valid_hotkey(ho):
        st["hk_open"] = ho
    return st


# ---------------- Автоподсчёт по реплею ----------------
REPLAY_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                          "FortniteGame", "Saved", "Demos")
REPLAY_HELPER = "replay_scan.exe"
REPLAY_QUIET_SEC = 6       # реплей не менялся столько секунд — матч закончился
REPLAY_POLL_SEC = 2.0      # как часто смотреть папку с реплеями


def find_bundled(name):
    for d in (getattr(sys, "_MEIPASS", None),
              os.path.dirname(os.path.abspath(sys.argv[0])),
              os.path.dirname(os.path.abspath(__file__))):
        if d and os.path.isfile(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


def run_replay_helper(path):
    """replay_scan.exe <файл.replay> печатает одну JSON-строку:
    {"ok": true, "chests": 12, "br": true, "match_start": "..."}   — посчитано
    {"ok": false, "live": true}                                     — матч ещё пишется
    {"ok": false, "error": "..."}                                   — не прочитан"""
    exe = find_bundled(REPLAY_HELPER)
    if not exe:
        return {"ok": False, "error": "no_helper"}
    try:
        r = subprocess.run([exe, path], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=240, creationflags=0x08000000)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
        res = json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "helper_failed"}
    return res if isinstance(res, dict) else {"ok": False, "error": "bad_output"}


def replay_worker(q, stop_evt, evt, done):
    """Фоновый поток: пока evt включён, ждёт завершённые реплеи (появившиеся после включения)
    и отдаёт число сундуков из каждого в очередь. done — множество уже посчитанных."""
    armed = None
    warned = False
    fails = {}
    while not stop_evt.wait(REPLAY_POLL_SEC):
        if not evt.is_set():
            armed, warned = None, False
            continue
        if armed is None:
            armed = time.time() - 5
        try:
            files = []
            for name in os.listdir(REPLAY_DIR):
                if name.lower().endswith(".replay"):
                    path = os.path.join(REPLAY_DIR, name)
                    files.append((os.stat(path).st_mtime, name, path))
        except OSError:
            continue
        for mtime, name, path in sorted(files):
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            sig = "%s|%d|%d" % (name, size, int(mtime))
            if sig in done or mtime < armed or time.time() - mtime < REPLAY_QUIET_SEC:
                continue
            if stop_evt.is_set() or not evt.is_set():
                break
            res = run_replay_helper(path)
            # total = открытые (E) + разбитые: сканер иногда пишет открытый сундук как «разбитый»
            chests = res.get("total", res.get("chests"))
            if res.get("ok") and isinstance(chests, int) and not isinstance(chests, bool) \
                    and chests >= 0:
                done.add(sig)
                q.put(("replay", {"sig": sig, "chests": chests, "br": res.get("br", True)}))
            elif res.get("error") == "no_helper":
                if not warned:
                    warned = True
                    q.put(("replay_error", "Нет replay_scan.exe рядом с программой"))
                break
            elif res.get("live"):
                continue          # ещё пишется — попробуем в следующий проход
            else:
                fails[sig] = fails.get(sig, 0) + 1
                if fails[sig] >= 3:
                    done.add(sig)
                    q.put(("replay_error", "Реплей не прочитан: " + name))


def save_state(state):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError:
        pass


# ---------------- Горячие клавиши ----------------
def build_hotkeys(state):
    """Таблица {id: (имя, модификаторы, vk)} по текущим настройкам."""
    hk = {}
    for name, hid in HK_IDS.items():
        mods, vk = parse_combo(state["hk_" + name])
        hk[hid] = (name, mods | MOD_NOREPEAT, vk)
    for hid, (name, vk) in FIXED_HOTKEYS.items():
        hk[hid] = (name, MODS, vk)
    return hk


def hotkey_worker(q, stop_evt, cmd_q, hotkeys):
    """Слушает горячие клавиши Windows в отдельном потоке."""
    current = dict(hotkeys)
    registered = set()

    def reg(hid):
        _name, mod, vk = current[hid]
        if user32.RegisterHotKey(None, hid, mod, vk):
            registered.add(hid)
            return True
        return False

    def unreg(hid):
        if hid in registered:
            user32.UnregisterHotKey(None, hid)
            registered.discard(hid)

    def apply(token, changes):
        old = {hid: current[hid] for hid in changes}
        for hid in changes:
            unreg(hid)
        failed = None
        for hid, (mod, vk) in changes.items():
            current[hid] = (old[hid][0], mod, vk)
            if not reg(hid):
                failed = hid
                break
        if failed is None:
            q.put(("hk_result", (token, True, None)))
            return
        for hid in changes:
            unreg(hid)
            current[hid] = old[hid]
            reg(hid)
        q.put(("hk_result", (token, False, "Сочетание занято другой программой")))

    for hid in list(current):
        if not reg(hid):
            q.put(("hotkey_error", current[hid][0]))

    msg = wintypes.MSG()
    menu_prev = False
    while not stop_evt.is_set():
        try:
            while True:
                cmd = cmd_q.get_nowait()
                if cmd[0] == "apply":
                    apply(cmd[1], cmd[2])
        except queue.Empty:
            pass
        # Insert / Delete — меню. Опрашиваем клавиши, а не занимаем их: в других программах
        # Delete/Insert продолжают работать как обычно. Без Ctrl/Alt/Shift/Win.
        down = bool((user32.GetAsyncKeyState(0x2D) | user32.GetAsyncKeyState(0x2E)) & 0x8000)
        if down and any(key_down(m) for m in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            down = False
        if down and not menu_prev:
            q.put(("action", "menu"))
        menu_prev = down
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == WM_HOTKEY and msg.wParam in current:
                q.put(("action", current[msg.wParam][0]))
        else:
            time.sleep(0.03)
    for hid in list(registered):
        user32.UnregisterHotKey(None, hid)


# ---------------- Общие виджеты и помощники ----------------
def flat_button(parent, text, command, bg=None, fg=None, width=3,
                font=(FONT, 11, "bold"), hover=None):
    bg = bg or T["card_hi"]
    fg = fg or T["fg"]
    hover = hover or T["border"]
    b = tk.Label(parent, text=text, bg=bg, fg=fg, width=width, font=font,
                 cursor="hand2", pady=2)
    b.bind("<Button-1>", lambda _e: command())
    b.bind("<Enter>", lambda _e: b.config(bg=hover))
    b.bind("<Leave>", lambda _e: b.config(bg=bg))
    b._no_drag = True
    return b


def clickable(widget, command):
    widget._no_drag = True
    widget.bind("<Button-1>", lambda _e: command())


# ---------------- Библиотеки Python: проверка и установка из меню ----------------
LIB_MODULES = (("opencv-python", "cv2"), ("numpy", "numpy"), ("mss", "mss"), ("pillow", "PIL"))


def missing_libs():
    """pip-имена библиотек, которых нет на диске. В собранном exe всё уже внутри."""
    if getattr(sys, "frozen", False):
        return []
    import importlib
    import importlib.util
    importlib.invalidate_caches()
    out = []
    for pkg, mod in LIB_MODULES:
        try:
            if importlib.util.find_spec(mod) is None:
                out.append(pkg)
        except (ImportError, ValueError):
            out.append(pkg)
    return out


def lib_status():
    """('bundled'|'ok'|'restart'|'need', [чего не хватает])."""
    if getattr(sys, "frozen", False):
        return "bundled", []
    miss = missing_libs()
    if miss:
        return "need", miss
    # на диске всё есть, но эта копия программы стартовала без них — нужен перезапуск
    if not AUTO_OK or MAP_ERR:
        return "restart", []
    return "ok", []


def install_libs(pkgs):
    """pip install в отдельном окне CMD, чтобы было видно ход установки."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            exe = cand
    inner = ('chcp 65001 >nul & "%s" -m pip install %s & echo. & '
             'echo Если выше нет ошибок, закрой это окно и перезапусти программу. & pause'
             % (exe, " ".join(pkgs)))
    subprocess.Popen('start "Установка библиотек" cmd /c "%s"' % inner, shell=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def lib_row(box, purpose):
    """Строка «Библиотека Python»: Скачать (ставит в CMD) или Скачана (неактивна)."""
    row = tk.Frame(box, bg=T["card"])
    row.pack(fill="x", padx=18, pady=(8, 4))
    left = tk.Frame(row, bg=T["card"])
    left.pack(side="left", fill="x", expand=True)
    tk.Label(left, text="Библиотека Python", bg=T["card"], fg=T["fg"],
             font=(FONT, 10, "bold"), anchor="w").pack(anchor="w")
    sub = tk.Label(left, text="", bg=T["card"], fg=T["dim"], font=(FONT, 8), anchor="w")
    sub.pack(anchor="w")
    btn = flat_button(row, "Скачать", lambda: None, width=10, font=(FONT, 9, "bold"))
    btn.pack(side="right", ipady=2)

    def disable(text):
        btn.config(text=text, fg=T["dim"], bg=T["card_hi"], cursor="arrow")
        for ev in ("<Button-1>", "<Enter>", "<Leave>"):
            btn.unbind(ev)

    def alive():
        try:
            return bool(row.winfo_exists())
        except tk.TclError:
            return False

    def poll():
        if not alive():
            return
        if lib_status()[0] == "need":
            row.after(2000, poll)
        else:
            sub.config(text=purpose + " · готово, перезапусти программу")
            disable("Скачана")

    def go():
        code, miss = lib_status()
        if code != "need":
            return
        try:
            install_libs(miss)
        except Exception as e:  # noqa: BLE001
            _log(f"ОШИБКА установки библиотек: {e!r}")
            sub.config(text="не получилось запустить CMD", fg=ALERT)
            return
        sub.config(text="идёт установка в окне CMD…", fg=T["dim"])
        disable("Идёт…")
        row.after(2000, poll)

    code, miss = lib_status()
    if code == "need":
        sub.config(text=purpose + " · не хватает: " + ", ".join(miss))
        btn.bind("<Button-1>", lambda _e: go())
    elif code == "restart":
        sub.config(text=purpose + " · установлена, перезапусти программу")
        disable("Скачана")
    elif code == "bundled":
        sub.config(text=purpose + " · встроена в программу")
        disable("Скачана")
    else:
        sub.config(text=purpose)
        disable("Скачана")
    return row


def make_rounded(win, get_radius, hold=None):
    """Скруглённые углы через SetWindowRgn. hold() == True — регион не трогаем
    (его в это время сам ведёт анимация)."""
    st = {"hwnd": None, "job": None, "last": None}

    def apply():
        st["job"] = None
        try:
            if not win.winfo_exists():
                return
            if hold is not None and hold():
                return
            if st["hwnd"] is None:
                st["hwnd"] = user32.GetParent(win.winfo_id()) or win.winfo_id()
            r = int(get_radius()) * 2
            w, h = win.winfo_width(), win.winfo_height()
            if st["last"] == (w, h, r):          # просто сдвинули окно — регион тот же
                return
            st["last"] = (w, h, r)
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r)
            user32.SetWindowRgn(st["hwnd"], rgn, True)
        except Exception:
            pass

    def on_cfg(e):
        if e.widget is win and st["job"] is None:
            st["job"] = win.after_idle(apply)

    win.bind("<Configure>", on_cfg, add="+")
    apply.reset = lambda: st.update(last=None)
    return apply


def bind_drag(widget, win, on_move=None, on_end=None):
    """Перетаскивание окна за любой элемент, кроме кнопок/полей/ползунков."""
    off = {"dx": 0, "dy": 0}

    def start(e):
        off["dx"] = e.x_root - win.winfo_x()
        off["dy"] = e.y_root - win.winfo_y()

    def move(e):
        x, y = e.x_root - off["dx"], e.y_root - off["dy"]
        win.geometry(f"+{x}+{y}")
        if on_move:
            on_move(x, y)

    def end(_e):
        if on_end:
            on_end()

    def rec(w):
        if getattr(w, "_no_drag", False) or isinstance(w, tk.Entry):
            return
        w.bind("<ButtonPress-1>", start, add="+")
        w.bind("<B1-Motion>", move, add="+")
        w.bind("<ButtonRelease-1>", end, add="+")
        for ch in w.winfo_children():
            rec(ch)

    rec(widget)


class Slider(tk.Canvas):
    """Красивый ползунок на Canvas."""

    def __init__(self, parent, lo, hi, value, step, command, width=200):
        super().__init__(parent, width=width, height=26, bg=T["card"],
                         highlightthickness=0, cursor="hand2")
        self.lo, self.hi, self.step, self.command, self.w = lo, hi, step, command, width
        self.value = _clamp(value, lo, hi)
        self._no_drag = True
        self.bind("<Button-1>", self._on)
        self.bind("<B1-Motion>", self._on)
        self._draw()

    def _draw(self):
        pad = 10
        self.delete("all")
        frac = (self.value - self.lo) / (self.hi - self.lo)
        x = pad + frac * (self.w - 2 * pad)
        self.create_line(pad, 13, self.w - pad, 13, fill=T["card_hi"], width=6,
                         capstyle="round")
        if x > pad:
            self.create_line(pad, 13, x, 13, fill=T["open"], width=6, capstyle="round")
        self.create_oval(x - 10, 3, x + 10, 23, fill=_mix(T["card"], T["open"], 0.28),
                         outline="")                                    # ореол
        self.create_oval(x - 7, 6, x + 7, 20, fill=T["fg"], outline=T["open"], width=3)

    def _on(self, e):
        pad = 10
        frac = _clamp((e.x - pad) / (self.w - 2 * pad), 0.0, 1.0)
        v = self.lo + frac * (self.hi - self.lo)
        v = round(round(v / self.step) * self.step, 3)
        v = _clamp(v, self.lo, self.hi)
        if v != self.value:
            self.value = v
            self._draw()
            self.command(v)


# ---------------- Предпросмотр панели ----------------
PV_W, PV_H = 250, 200
PV_PW, PV_PH = 190, 110
PV_GROUND = 155
PV_BANDS = ["#ff7a3d", "#ffd23f", "#3ddc84", "#2bb3ff", "#9b5cff", "#ffffff"]


def _hex_rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def render_preview(alpha, radius):
    """Пиксели мини-панели поверх пёстрой «сцены»."""
    cache = {}

    def blend(bg, fg):
        k = (bg, fg)
        v = cache.get(k)
        if v is None:
            b, f = _hex_rgb(bg), _hex_rgb(fg)
            v = "#%02x%02x%02x" % tuple(
                int(round(b[i] * (1 - alpha) + f[i] * alpha)) for i in range(3))
            cache[k] = v
        return v

    sky = [PV_BANDS[x * len(PV_BANDS) // PV_W] for x in range(PV_W)]
    ground = []
    for c in sky:
        r_, g_, b_ = _hex_rgb(c)
        ground.append("#%02x%02x%02x" % (int(r_ * 0.45), int(g_ * 0.45), int(b_ * 0.45)))

    pw, ph = PV_PW, PV_PH
    ox, oy = (PV_W - pw) // 2, (PV_H - ph) // 2
    r = int(round(radius * pw / PANEL_W))
    r = max(0, min(r, pw // 2, ph // 2))

    o_c = T["open"]
    rects = [
        (12, 12, 20, 20, o_c),
        (26, 14, 84, 18, T["dim"]),
        (pw - 26, 13, pw - 14, 19, T["dim"]),
        (10, 34, pw - 10, 74, T["card"]),
        (10, 34, 14, 74, o_c),
        (22, 42, 56, 45, T["dim"]),
        (22, 51, 66, 67, o_c),
        (pw - 72, 48, pw - 56, 62, T["card_hi"]),
        (pw - 48, 44, pw - 18, 66, o_c),
    ]

    rows = []
    for y in range(PV_H):
        base = ground if y >= PV_GROUND else sky
        ly = y - oy
        if ly < 0 or ly >= ph:
            rows.append(base)
            continue
        inset = 0.0
        if r > 0 and ly < r:
            dy = r - ly - 0.5
            inset = r - math.sqrt(max(0.0, r * r - dy * dy))
        elif r > 0 and ly >= ph - r:
            dy = ly - (ph - r) + 0.5
            inset = r - math.sqrt(max(0.0, r * r - dy * dy))
        k = int(math.ceil(inset))
        panel = [T["bg"]] * pw
        for x0, y0, x1, y1, col in rects:
            if y0 <= ly < y1:
                panel[x0:x1] = [col] * (x1 - x0)
        row = list(base)
        for px in range(k, pw - k):
            x = ox + px
            row[x] = blend(base[x], panel[px])
        rows.append(row)
    return rows


# ======================= Блок настроек =======================
def build_settings(parent, state, on_change, on_restyle, app, sections=None, preview=True):
    """Панель настроек. sections — какие блоки показать (None — все), preview — предпросмотр."""
    def want(name):
        return sections is None or name in sections

    card = T["card"]
    outer_f = tk.Frame(parent, bg=card)
    f = tk.Frame(outer_f, bg=card)
    f.pack(side="left", fill="y")

    # --- Предпросмотр справа ---
    pv_box = tk.Frame(outer_f, bg=card)
    if preview:
        pv_box.pack(side="right", anchor="n", padx=(4, 18), pady=(14, 0))
    tk.Label(pv_box, text="ПРЕДПРОСМОТР", bg=card, fg=T["dim"],
             font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 6))
    pv_img = tk.PhotoImage(width=PV_W, height=PV_H)
    pv_lbl = tk.Label(pv_box, image=pv_img, bd=0, highlightthickness=1,
                      highlightbackground=T["card_hi"])
    pv_lbl.image = pv_img
    pv_lbl.pack()
    pv_cap = tk.Label(pv_box, text="", bg=card, fg=T["dim"], font=(FONT, 9))
    pv_cap.pack(anchor="w", pady=(6, 0))
    pv_job = [None]

    def draw_preview():
        pv_job[0] = None
        try:
            rows = render_preview(state["alpha"], state["radius"])
            pv_img.put(" ".join("{" + " ".join(r_) + "}" for r_ in rows))
            pv_cap.config(text=f"Прозрачность {int(round(state['alpha'] * 100))}%"
                               f" · скругление {int(state['radius'])} px")
        except tk.TclError:
            pass

    def preview_soon():
        if pv_job[0] is None:
            pv_job[0] = f.after(15, draw_preview)

    if preview:
        draw_preview()

    def title(text):
        tk.Label(f, text=text, bg=card, fg=T["dim"],
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=18, pady=(12, 4))

    def choose(key, val):
        on_change(key, val)
        on_restyle()

    def safe_restyle():
        try:
            on_restyle()
        except tk.TclError:
            pass

    # --- Сколько сундуков открыто (можно вписать своё число) ---
    if want("count"):
        title("СЧЁТЧИК")
        cr = tk.Frame(f, bg=card)
        cr.pack(fill="x", padx=18, pady=(0, 4))
        tk.Label(cr, text="Сколько сундуков вы открыли", bg=card, fg=T["fg"],
                 font=(FONT, 10), anchor="w").pack(side="left")
        cmsg = tk.Label(f, text="Впишите число и нажмите Enter или «Задать»", bg=card,
                        fg=T["dim"], font=(FONT, 9), anchor="w")
        cent = tk.Entry(cr, width=7, bd=0, bg=T["card_hi"], fg=T["open"],
                        insertbackground=T["fg"], font=(FONT, 11, "bold"), justify="center")
        cent.insert(0, str(state["open"]))

        def apply_count(_e=None):
            txt = cent.get().strip()
            try:
                n = int(txt)
            except ValueError:
                cmsg.config(text="Нужно целое число, например 25", fg=ALERT)
                return "break"
            n = _clamp(n, 0, MAX_COUNT)
            app.set_count(n)
            cent.delete(0, "end")
            cent.insert(0, str(n))
            cmsg.config(text="Готово: теперь %d" % n, fg=T["open"])
            return "break"

        flat_button(cr, "Задать", apply_count, width=8,
                    font=(FONT, 9, "bold")).pack(side="right", ipady=2)
        cent.pack(side="right", padx=(0, 8), ipady=5)
        cent.bind("<Button-1>", lambda _e: cent.focus_force())
        cent.bind("<Return>", apply_count)
        cent.bind("<KP_Enter>", apply_count)
        cmsg.pack(anchor="w", padx=18, pady=(0, 2))
        app.count_entry = cent

    # --- Тема ---
    if want("theme"):
        title("ТЕМА")
        row = tk.Frame(f, bg=card)
        row.pack(fill="x", padx=18)
        for name in THEMES:
            sel = state["theme"] == name
            edge = T["open"] if sel else T["card_hi"]
            chip = tk.Label(row, text=name, bg=T["card_hi"], fg=T["fg"],
                            font=(FONT, 10, "bold"), padx=10, pady=4, cursor="hand2",
                            highlightthickness=2, highlightbackground=edge,
                            highlightcolor=edge)
            chip.pack(side="left", padx=(0, 6))
            clickable(chip, lambda n=name: choose("theme", n))

    # --- Цвет открытых ---
    if want("color"):
        title("ЦВЕТ")

        def color_row(label, key):
            r = tk.Frame(f, bg=card)
            r.pack(fill="x", padx=18, pady=5)
            tk.Label(r, text=label, bg=card, fg=T["fg"], font=(FONT, 10),
                     width=9, anchor="w").pack(side="left")
            for c in PALETTE:
                edge = T["fg"] if state[key].lower() == c else card
                sw = tk.Label(r, bg=c, width=2, height=1, cursor="hand2",
                              highlightthickness=2, highlightbackground=edge,
                              highlightcolor=edge)
                sw.pack(side="left", padx=2)
                clickable(sw, lambda c=c: choose(key, c))
            ent = tk.Entry(r, width=8, bd=0, bg=T["card_hi"], fg=T["fg"],
                           insertbackground=T["fg"], font=(FONT, 10))
            ent.insert(0, state[key])
            ent.pack(side="left", padx=(8, 0), ipady=5)
            ent.bind("<Button-1>", lambda _e: ent.focus_force())

            def commit(_e=None):
                v = ent.get().strip()
                if not v.startswith("#"):
                    v = "#" + v
                if HEX_RE.match(v):
                    choose(key, v.lower())

            ent.bind("<Return>", commit)

        color_row("Открытые", "col_open")

    # --- Клавиша ---
    if want("hotkey"):
        title("КЛАВИША")

        def hotkey_row(label, action):
            key = "hk_" + action
            r = tk.Frame(f, bg=card)
            r.pack(fill="x", padx=18, pady=5)
            tk.Label(r, text=label, bg=card, fg=T["fg"], font=(FONT, 10),
                     width=9, anchor="w").pack(side="left")
            btn = tk.Label(r, text=state[key], bg=T["card_hi"], fg=T[action],
                           font=(FONT, 10, "bold"), padx=10, pady=4, cursor="hand2",
                           highlightthickness=2, highlightbackground=T["card_hi"],
                           highlightcolor=T["card_hi"], takefocus=1)
            btn.pack(side="left")
            msg = tk.Label(r, text="", bg=card, fg=T["dim"], font=(FONT, 9))
            msg.pack(side="left", padx=8)

            def begin():
                active = [True]
                app.capturing = True
                btn.config(text="Нажмите клавиши…", fg=AMBER,
                           highlightbackground=AMBER, highlightcolor=AMBER)
                msg.config(text="Esc — отмена", fg=T["dim"])
                btn.focus_force()

                def finish():
                    if not active[0]:
                        return False
                    active[0] = False
                    app.capturing = False
                    app.capture_conflict = None
                    try:
                        btn.unbind("<KeyPress>")
                        btn.unbind("<FocusOut>")
                    except tk.TclError:
                        pass
                    return True

                def conflict(name):
                    try:
                        msg.config(text="Занято: " + ACTION_RU.get(name, name), fg=ALERT)
                    except tk.TclError:
                        pass

                app.capture_conflict = conflict

                def done(ok, err):
                    try:
                        if ok:
                            on_restyle()
                        else:
                            btn.config(text=state[key], fg=T[action],
                                       highlightbackground=T["card_hi"],
                                       highlightcolor=T["card_hi"])
                            msg.config(text=err or "Не удалось", fg=ALERT)
                    except tk.TclError:
                        pass

                def on_key(e):
                    if e.keysym in MODIFIER_KEYSYMS:
                        return "break"
                    if e.keysym == "Escape":
                        if finish():
                            safe_restyle()
                        return "break"
                    combo, err = combo_from_event(e)
                    if err:
                        msg.config(text=err, fg=ALERT)
                        return "break"
                    if not finish():
                        return "break"
                    btn.config(text=combo)
                    msg.config(text="Применяю…", fg=T["dim"])
                    app.apply_hotkeys({action: combo}, done)
                    return "break"

                def lost(_e):
                    if finish():
                        safe_restyle()

                btn.bind("<KeyPress>", on_key)
                btn.bind("<FocusOut>", lost)

            clickable(btn, begin)

        hotkey_row("Открытые", "open")

        kr = tk.Frame(f, bg=card)
        kr.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(kr, text="Клик по сочетанию → нажмите новые клавиши",
                 bg=card, fg=T["dim"], font=(FONT, 9)).pack(side="left")
        flat_button(kr, "По умолчанию",
                    lambda: app.apply_hotkeys(
                        {"open": DEFAULTS["hk_open"]},
                        lambda ok, _err: safe_restyle() if ok else None),
                    width=12, font=(FONT, 9, "bold")).pack(side="right")

    # --- Вид ---
    def slider_row(label, key, lo, hi, step, fmt):
        r = tk.Frame(f, bg=card)
        r.pack(fill="x", padx=18, pady=4)
        tk.Label(r, text=label, bg=card, fg=T["fg"], font=(FONT, 10),
                 width=15, anchor="w").pack(side="left")
        val = tk.Label(r, text=fmt(state[key]), bg=card, fg=T["dim"],
                       font=(FONT, 10, "bold"), width=6, anchor="e")
        val.pack(side="right")

        def cmd(v):
            val.config(text=fmt(v))
            on_change(key, v)
            if preview and key in ("alpha", "radius"):
                preview_soon()

        Slider(r, lo, hi, state[key], step, cmd).pack(side="right", padx=6)

    if want("view"):
        title("ВИД")
        slider_row("Прозрачность", "alpha", 0.3, 1.0, 0.05, lambda v: f"{int(round(v * 100))}%")
        slider_row("Размер", "scale", SCALE_MIN, SCALE_MAX, 0.05, lambda v: f"{int(round(v * 100))}%")
        slider_row("Скругление", "radius", 0, 30, 1, lambda v: f"{int(v)} px")
    if want("cooldown"):
        slider_row("Защита от дубля", "cooldown", 0.5, 5.0, 0.1, lambda v: f"{v:.1f} с")

    if want("reset"):
        flat_button(f, "Сбросить оформление", lambda: choose("__reset__", None),
                    width=22, font=(FONT, 10, "bold")).pack(pady=(12, 14), ipady=5)
    return outer_f


# ======================= Стартовое окно =======================
class StartDialog:
    """Окно при запуске: счётчик + (по желанию) настройки."""

    def __init__(self, app):
        self.app = app
        self.ok = False
        self.expanded = False
        self.var_open = tk.StringVar(value=str(app.state["open"]))

        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.region = make_rounded(self.win, lambda: app.state["radius"])
        self.build()
        self.win.update_idletasks()
        x = (self.win.winfo_screenwidth() - self.win.winfo_reqwidth()) // 2
        y = (self.win.winfo_screenheight() - self.win.winfo_reqheight()) // 3
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.focus_force()

    def build(self):
        w = self.win
        for ch in w.winfo_children():
            ch.destroy()
        w.configure(bg=T["bg"])
        outer = tk.Frame(w, bg=T["bg"])
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=T["bg"])
        head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(head, text="●", bg=T["bg"], fg=T["open"], font=(FONT, 8)).pack(side="left")
        tk.Label(head, text=" CHEST COUNTER", bg=T["bg"], fg=T["dim"],
                 font=(FONT, 8, "bold")).pack(side="left")
        flat_button(head, "✕", self.cancel, bg=T["bg"], fg=T["dim"], width=2,
                    font=(FONT, 9, "bold"), hover=ALERT).pack(side="right")

        tk.Label(outer, text="Fortnite Chest Counter", bg=T["bg"], fg=T["fg"],
                 font=(FONT, 16, "bold")).pack(padx=34, pady=(8, 2))
        tk.Label(outer, text="Сколько сундуков у вас уже есть?", bg=T["bg"], fg=T["dim"],
                 font=(FONT, 10)).pack(padx=34, pady=(0, 14))

        self._row(outer, "Открытые сундуки", self.var_open)

        gear = "⚙  Настройки  ▴" if self.expanded else "⚙  Настройки  ▾"
        flat_button(outer, gear, self.toggle_settings, width=30,
                    font=(FONT, 10, "bold")).pack(padx=26, pady=(12, 4), ipady=4, fill="x")

        if self.expanded:
            build_settings(outer, self.app.state, self.app.apply_setting,
                           self.restyle, self.app).pack(fill="x", padx=26, pady=(4, 4))

        btns = tk.Frame(outer, bg=T["bg"])
        btns.pack(fill="x", padx=26, pady=(8, 22))
        flat_button(btns, "Обнулить", self.zero, width=11,
                    font=(FONT, 10, "bold")).pack(side="left", ipady=5)
        flat_button(btns, "Старт  ▶", self.start, bg=T["open"], fg=on_color(T["open"]),
                    width=16, font=(FONT, 10, "bold"), hover=T["open"]
                    ).pack(side="right", ipady=5)

        bind_drag(outer, w)
        self.win.update_idletasks()
        self._fit_on_screen()

    def _fit_on_screen(self):
        w = self.win
        h = w.winfo_reqheight()
        y = _clamp(w.winfo_y(), 0, max(0, w.winfo_screenheight() - h - 30))
        w.geometry(f"+{w.winfo_x()}+{y}")

    def _row(self, parent, title, var):
        color = kcolor()
        card = tk.Frame(parent, bg=T["card"])
        card.pack(fill="x", padx=26, pady=5)
        tk.Frame(card, bg=color, width=4).pack(side="left", fill="y")
        tk.Label(card, text=title, bg=T["card"], fg=T["fg"],
                 font=(FONT, 10)).pack(side="left", padx=12, pady=12)

        def step(d):
            var.set(str(_clamp(to_int(var.get()) + d, 0, MAX_COUNT)))

        flat_button(card, "+", lambda: step(1), bg=color, fg=on_color(color),
                    hover=color).pack(side="right", padx=(4, 10))
        ent = tk.Entry(card, textvariable=var, width=6, justify="center", bd=0,
                       bg=T["card_hi"], fg=color, insertbackground=T["fg"],
                       font=(FONT, 15, "bold"))
        ent.pack(side="right", ipady=3)
        ent.bind("<Button-1>", lambda _e: ent.focus_force())
        ent.bind("<Return>", lambda _e: self.start())
        flat_button(card, "−", lambda: step(-1)).pack(side="right", padx=(10, 4))

    def toggle_settings(self):
        self.app.capturing = False
        self.expanded = not self.expanded
        self.win.after(1, self.build)

    def restyle(self):
        self.win.after(1, self.build)
        self.win.after(5, self.region)

    def zero(self):
        self.var_open.set("0")

    def start(self):
        self.app.capturing = False
        self.app.state["open"] = to_int(self.var_open.get())
        self.ok = True
        self.win.destroy()

    def cancel(self):
        self.app.capturing = False
        self.ok = False
        self.win.destroy()

    def result(self):
        self.win.wait_window(self.win)
        return self.ok


# ======================= Окно настроек (из панели) =======================
class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.region = make_rounded(self.win, lambda: app.state["radius"])
        self.build()
        self.win.update_idletasks()
        rw, rh = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        px = app.root.winfo_x() + app.root.winfo_width() + 12
        if px + rw > sw:
            px = max(0, app.root.winfo_x() - rw - 12)
        py = _clamp(app.root.winfo_y(), 0, max(0, sh - rh - 40))
        self.win.geometry(f"+{px}+{py}")
        self.win.deiconify()

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def build(self):
        if not self.alive():
            return
        w = self.win
        for ch in w.winfo_children():
            ch.destroy()
        w.configure(bg=T["bg"])
        outer = tk.Frame(w, bg=T["bg"])
        outer.pack(fill="both", expand=True)
        head = tk.Frame(outer, bg=T["bg"])
        head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(head, text="⚙", bg=T["bg"], fg=T["open"], font=(FONT, 9)).pack(side="left")
        tk.Label(head, text=" НАСТРОЙКИ", bg=T["bg"], fg=T["dim"],
                 font=(FONT, 8, "bold")).pack(side="left")
        flat_button(head, "✕", self.close, bg=T["bg"], fg=T["dim"], width=2,
                    font=(FONT, 9, "bold"), hover=ALERT).pack(side="right")
        build_settings(outer, self.app.state, self.app.apply_setting,
                       self.restyle, self.app).pack(fill="x", padx=14, pady=(0, 14))
        bind_drag(outer, w)
        w.update_idletasks()
        h = w.winfo_reqheight()
        y = _clamp(w.winfo_y(), 0, max(0, w.winfo_screenheight() - h - 40))
        if y != w.winfo_y():
            w.geometry(f"+{w.winfo_x()}+{y}")

    def restyle(self):
        self.win.after(1, self.build)
        self.win.after(5, self.region)

    def close(self):
        self.app.capturing = False
        if self.alive():
            self.win.destroy()



# ======================= Меню (Insert / Delete) =======================
MENU_W = 520
# Как раскрываются настройки по ПКМ (впишите одно из трёх слов):
#   "push" — настройки выдвигаются из функции, строки ниже плавно съезжают вниз.
#   "wipe" — «шторка»: строки встают на место сразу, а настройки проявляются сверху вниз.
#   "none" — вообще без анимации: настройки появляются мгновенно.
MENU_ANIM = "none"


def ease_out(t):
    return 1.0 - (1.0 - t) ** 3


def _mix(c1, c2, t):
    """Смесь двух цветов '#rrggbb'."""
    try:
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, TypeError, IndexError):
        return c2
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def animate(widget, ms, fn, done=None, ease=ease_out):
    """Анимация по времени (не по числу кадров): fn(0..1). Возвращает {'on': True};
    поставьте 'on' = False, чтобы остановить."""
    tok = {"on": True}
    t0 = time.monotonic()

    def step():
        if not tok["on"]:
            return
        t = min(1.0, (time.monotonic() - t0) * 1000.0 / max(1, ms))
        try:
            fn(ease(t))
            if t < 1.0:
                widget.after(8, step)
            elif done:
                done()
        except tk.TclError:
            tok["on"] = False

    step()
    return tok


class Switch(tk.Canvas):
    """Переключатель-«таблетка» с плавным движением."""
    W, H = 46, 26

    def __init__(self, parent, on, command, bg=None):
        super().__init__(parent, width=self.W, height=self.H, bg=bg or T["card"],
                         highlightthickness=0, cursor="hand2")
        self.on = bool(on)
        self.p = 1.0 if self.on else 0.0
        self.command = command
        self._no_drag = True
        self._skip_row = True            # клик по самому переключателю — свой обработчик
        self._tok = None
        self.bind("<Button-1>", lambda _e: self.command())
        self._draw()

    def set(self, on, animated=True):
        on = bool(on)
        self.on = on
        target = 1.0 if on else 0.0
        if self._tok:
            self._tok["on"] = False
        if not animated or self.p == target:
            self.p = target
            self._draw()
            return
        start = self.p

        def fn(e):
            self.p = start + (target - start) * e
            self._draw()

        self._tok = animate(self, 140, fn)

    def _draw(self):
        self.delete("all")
        w, h = self.W, self.H
        col = _mix(T["border"], T["open"], self.p)
        self.create_oval(0, 0, h, h, fill=col, outline=col)
        self.create_oval(w - h, 0, w, h, fill=col, outline=col)
        self.create_rectangle(h / 2, 0, w - h / 2, h, fill=col, outline=col)
        cx = h / 2 + (w - h) * self.p
        r = h / 2 - 4
        self.create_oval(cx - r, h / 2 - r + 1.5, cx + r, h / 2 + r + 1.5,
                         fill=_mix(col, "#000000", 0.35), outline="")      # тень
        self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r,
                         fill=_mix(T["dim"], "#ffffff", self.p), outline="")


def bind_tree(widget, seq, fn, skip_attr=None):
    """Повесить обработчик на виджет и всех его потомков (кроме помеченных skip_attr)."""
    widget.bind(seq, fn, add="+")
    for ch in widget.winfo_children():
        if skip_attr and getattr(ch, skip_attr, False):
            continue
        bind_tree(ch, seq, fn, skip_attr)


def toggle_row(parent, label, value, command):
    r = tk.Frame(parent, bg=T["card"])
    r.pack(fill="x", padx=18, pady=6)
    tk.Label(r, text=label, bg=T["card"], fg=T["fg"], font=(FONT, 10),
             anchor="w").pack(side="left")
    sw = Switch(r, value, lambda: None)
    sw.pack(side="right")

    def click():
        new = not sw.on
        sw.set(new)
        command(new)

    sw.command = click
    return sw


def menu_slider(parent, label, lo, hi, step, value, fmt, command):
    r = tk.Frame(parent, bg=T["card"])
    r.pack(fill="x", padx=18, pady=4)
    tk.Label(r, text=label, bg=T["card"], fg=T["fg"], font=(FONT, 10),
             width=15, anchor="w").pack(side="left")
    val = tk.Label(r, text=fmt(value), bg=T["card"], fg=T["open"],
                   font=(FONT, 10, "bold"), width=6, anchor="e")
    val.pack(side="right")

    def cmd(v):
        val.config(text=fmt(v))
        command(v)

    Slider(r, lo, hi, value, step, cmd).pack(side="right", padx=6)


def round_poly(c, x0, y0, x1, y1, r, **kw):
    """Скруглённый прямоугольник на Canvas."""
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def gradient_bar(parent, c1, c2, w, h, bg=None):
    """Полоска-градиент слева направо."""
    c = tk.Canvas(parent, width=w, height=h, bg=bg or T["bg"], highlightthickness=0)
    n = 48
    seg = w / float(n)
    for i in range(n):
        c.create_rectangle(int(i * seg), 0, int((i + 1) * seg) + 1, h,
                           fill=_mix(c1, c2, i / float(n - 1)), outline="")
    c._no_drag = False
    return c


def draw_chest(c, size, ink, seam):
    """Рисует сундучок ink на фоне seam (цвет подложки; им же прорисованы швы и ремни)."""
    k = size / 46.0

    def P(v):
        return v * k

    w2 = max(1, int(round(2 * k)))
    c.create_arc(P(11), P(12), P(35), P(36), start=0, extent=180, style="chord",
                 fill=ink, outline=ink)                                      # крышка
    c.create_rectangle(P(11), P(24), P(35), P(35), fill=ink, outline=ink)    # корпус
    c.create_line(P(11), P(24), P(35), P(24), fill=seam, width=w2)           # шов
    for x in (16, 30):                                                       # ремни
        c.create_line(P(x), P(14), P(x), P(35), fill=seam, width=w2)
    c.create_rectangle(P(20), P(21), P(26), P(28), fill=ink, outline=seam, width=w2)   # замок
    c.create_rectangle(P(22), P(23), P(24), P(26), fill=seam, outline=seam)


def chest_badge(parent, size, badge_bg, ink, canvas_bg):
    """Скруглённая плашка с сундучком внутри."""
    c = tk.Canvas(parent, width=size, height=size, bg=canvas_bg, highlightthickness=0)
    round_poly(c, 1, 1, size - 1, size - 1, size * 0.28, fill=badge_bg, outline="")
    draw_chest(c, size, ink, badge_bg)
    return c


def logo_badge(parent, size=46):
    """Значок-сундучок в акцентном цвете."""
    return chest_badge(parent, size, T["open"], on_color(T["open"]), T["bg"])

class MenuWindow:
    """Меню на Insert / Delete. Создаётся ОДИН раз при запуске (скрытым) и дальше только
    показывается/прячется — поэтому открывается мгновенно. Настройки функций построены заранее;
    ПКМ плавно раскрывает готовую панель. Состояние переключателей обновляется на месте."""

    MODULES = (
        ("panel", "Панель счётчика", "Счётчик открытых сундуков поверх игры"),
        ("notify", "Уведомление", "Окошко «+1 сундук добавлен», которое можно поставить куда угодно"),
        ("auto", "Автоподсчёт", "Считает сам, когда в игре всплывает опыт за сундук"),
        ("minimap", "Мини-карта", "Радар сундуков: карта едет за тобой по миникарте игры, есть режим «вся карта»"),
    )
    KEYS = {"panel": "", "notify": "", "auto": "Ctrl+Alt+A", "minimap": "Ctrl+Alt+M"}
    ALPHA = 0.97

    def __init__(self, app):
        self.app = app
        self.expanded = None
        self.visible = False
        self.prev_fg = None
        self.rows = {}
        self.hero = {}
        self.pin_sw = None
        self.obs_sw = None
        self._anims = {}
        self._pending = []
        self._warmed = False
        self._a = 0.0
        self._x = self._y = 0
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self._holding = True             # скругление окна ведём сами (_clip), не make_rounded
        self._hwnd = None
        self.region = make_rounded(self.win, lambda: app.state["radius"],
                                   hold=lambda: self._holding)
        self.build()
        self.win.update_idletasks()
        try:                      # попадает ли меню в скриншоты — решает настройка
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            user32.SetWindowDisplayAffinity(hwnd, app._affinity())
        except Exception:  # noqa: BLE001
            pass

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # ---------- анимации ----------
    def _anim(self, name, ms, fn, done=None):
        old = self._anims.get(name)
        if old:
            old["on"] = False
        self._anims[name] = animate(self.win, ms, fn, done)

    def _cancel_anims(self):
        for tok in self._anims.values():
            tok["on"] = False
        self._anims = {}

    # ---------- построение ----------
    ICONS = {"panel": "▣", "notify": "✦", "auto": "◎", "minimap": "⌖"}

    def build(self):
        if not self.alive():
            return
        app = self.app
        w = self.win
        self._cancel_anims()
        for ch in w.winfo_children():
            ch.destroy()
        w.configure(bg=T["bg"])
        self.rows = {}
        self.hero = {}
        self.pin_sw = None
        self.obs_sw = None
        outer = tk.Frame(w, bg=T["bg"])
        outer.pack(fill="both", expand=True)
        tk.Frame(outer, bg=T["bg"], width=MENU_W, height=1).pack()

        head = tk.Frame(outer, bg=T["bg"])
        head.pack(fill="x", padx=16, pady=(16, 0))
        logo = logo_badge(head)
        logo.pack(side="left")
        titles = tk.Frame(head, bg=T["bg"])
        titles.pack(side="left", padx=12)
        ttl = tk.Label(titles, text="Chest Counter", bg=T["bg"], fg=T["fg"],
                       font=(FONT, 17, "bold"))
        ttl.pack(anchor="w")
        sub = tk.Label(titles, text="счётчик  ·  автоподсчёт  ·  мини-карта", bg=T["bg"],
                       fg=T["dim"], font=(FONT, 9))
        sub.pack(anchor="w")
        flat_button(head, "✕", self.hide, bg=T["bg"], fg=T["dim"], width=2,
                    font=(FONT, 10, "bold"), hover=ALERT).pack(side="right")

        hero = self._hero(outer)

        cap = tk.Frame(outer, bg=T["bg"])
        cap.pack(fill="x", padx=20, pady=(14, 0))
        tk.Label(cap, text="ФУНКЦИИ", bg=T["bg"], fg=T["dim"],
                 font=(FONT, 8, "bold")).pack(side="left")
        tk.Label(cap, text="ЛКМ — вкл / выкл   ·   ПКМ — настройки", bg=T["bg"],
                 fg=T["dim"], font=(FONT, 8)).pack(side="right")

        for key, name, desc in self.MODULES:
            self._row(outer, key, name, desc)

        foot = tk.Frame(outer, bg=T["bg"])
        foot.pack(fill="x", padx=18, pady=(10, 14))
        tk.Label(foot, text="Insert / Delete — скрыть меню", bg=T["bg"], fg=T["dim"],
                 font=(FONT, 9)).pack(side="left")
        flat_button(foot, "Выход", app.quit, width=8, font=(FONT, 9, "bold"),
                    hover=ALERT).pack(side="right", ipady=2)

        def moved(x, y):
            app.state["menu_x"], app.state["menu_y"] = x, y
            self._x, self._y = x, y

        for wd in (head, cap, foot, hero):
            bind_drag(wd, w, on_move=moved, on_end=lambda: save_state(app.state))

        # настройки функций строим заранее, по одной, чтобы ничего не подвисало
        self._pending = [k for k, _n, _d in self.MODULES]
        if self.expanded:
            self._ensure_pane(self.expanded)
            self._set_open(self.expanded)
        w.after(40, self._build_next)

    @staticmethod
    def _pill_style(on):
        if on:
            return " ВКЛ ", T["open"], on_color(T["open"])
        return " ВЫКЛ ", T["card_hi"], T["dim"]

    @staticmethod
    def _icon_colors(on):
        if on:
            return _mix(T["card"], T["open"], 0.22), T["open"]
        return T["card_hi"], T["dim"]

    def _style_row(self, r, on):
        txt, pbg, pfg = self._pill_style(on)
        r["pill"].config(text=txt, bg=pbg, fg=pfg)
        fill, glyph = self._icon_colors(on)
        r["icon"].itemconfig(r["ibg"], fill=fill)
        r["icon"].itemconfig(r["igl"], fill=glyph)

    def _hero(self, parent):
        """Карточка с большим числом открытых сундуков и статусом функций."""
        wrap = tk.Frame(parent, bg=T["bg"])
        wrap.pack(fill="x", padx=14, pady=(14, 0))
        card = tk.Frame(wrap, bg=T["card"])
        card.pack(fill="x")
        body = tk.Frame(card, bg=T["card"])
        body.pack(fill="x", padx=18, pady=(10, 12))
        left = tk.Frame(body, bg=T["card"])
        left.pack(side="left")
        num = tk.Label(left, text=str(self.app.state["open"]), bg=T["card"], fg=T["open"],
                       font=(FONT, 32, "bold"))
        num.pack(anchor="w")
        tk.Label(left, text="сундуков открыто", bg=T["card"], fg=T["dim"],
                 font=(FONT, 9)).pack(anchor="w")
        right = tk.Frame(body, bg=T["card"])
        right.pack(side="right")
        for k, label in (("auto", "автоподсчёт"), ("map", "мини-карта")):
            line = tk.Frame(right, bg=T["card"])
            line.pack(anchor="e", pady=3)
            tk.Label(line, text=label, bg=T["card"], fg=T["dim"],
                     font=(FONT, 9)).pack(side="left", padx=(0, 8))
            pl = tk.Label(line, font=(FONT, 7, "bold"), padx=5, pady=1)
            pl.pack(side="left")
            self.hero[k] = pl
        self.hero["num"] = num
        tk.Frame(card, bg=T["card_hi"], height=1).pack(fill="x", padx=18)
        stats = tk.Frame(card, bg=T["card"])
        stats.pack(fill="x", padx=18, pady=(8, 0))
        for k, label in (("s_today", "сегодня"), ("s_week", "за неделю"),
                         ("s_best", "лучший день недели")):
            col = tk.Frame(stats, bg=T["card"])
            col.pack(side="left", expand=True, fill="x")
            v = tk.Label(col, text="0", bg=T["card"], fg=T["fg"], font=(FONT, 14, "bold"))
            v.pack(anchor="w")
            lb = tk.Label(col, text=label, bg=T["card"], fg=T["dim"], font=(FONT, 8))
            lb.pack(anchor="w")
            self.hero[k] = v
            self.hero[k + "_lbl"] = lb
        chart = tk.Canvas(card, width=self.CHART_W, height=self.CHART_H, bg=T["card"],
                          highlightthickness=0)
        chart.pack(padx=18, pady=(10, 0))
        self.hero["chart"] = chart
        gl = tk.Label(card, text="", bg=T["card"], fg=T["dim"], font=(FONT, 8), anchor="w")
        gl.pack(fill="x", padx=18, pady=(6, 10))
        self.hero["games"] = gl
        self._update_hero()
        return wrap

    CHART_W = MENU_W - 28 - 36          # ширина графика = ширина карточки внутри отступов
    CHART_H = 112
    DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

    def _draw_week(self, ws):
        """Столбики по дням недели (пн–вс): число над столбиком, самый высокий — акцентным
        цветом, сегодняшний день подписан ярче."""
        c = self.hero.get("chart")
        if c is None:
            return
        c.delete("all")
        vals, best, today = ws["vals"], ws["best"], ws["idx"]
        W, H = self.CHART_W, self.CHART_H
        top, base = 24, H - 20                      # место под число / линия под столбиками
        col = W / 7.0
        bw = 34
        usable = base - top
        c.create_line(0, base + 1, W, base + 1, fill=T["card_hi"], width=1)
        norm = _mix(T["card_hi"], T["open"], 0.35)
        for i, v in enumerate(vals):
            cx = col * i + col / 2.0
            is_best = best > 0 and v == best
            hgt = max(4, int(round(usable * v / float(best)))) if best > 0 and v > 0 else 3
            if is_best:
                fill = T["open"]
            elif v > 0:
                fill = _mix(norm, T["open"], 0.35) if i == today else norm
            else:
                fill = T["card_hi"]
            r = min(5, hgt // 2)
            round_poly(c, cx - bw / 2.0, base - hgt, cx + bw / 2.0, base, max(1, r),
                       fill=fill, outline="")
            c.create_text(cx, base - hgt - 9, text=str(v),
                          fill=T["open"] if is_best else (T["fg"] if v else T["dim"]),
                          font=(FONT, 10 if is_best else 9, "bold" if (is_best or i == today) else "normal"))
            c.create_text(cx, H - 8, text=self.DAYS[i],
                          fill=T["fg"] if i == today else T["dim"],
                          font=(FONT, 8, "bold" if i == today else "normal"))

    def _update_hero(self):
        h = self.hero
        if not h:
            return
        st = self.app.state
        try:
            h["num"].config(text=str(st["open"]))
            ws = week_stats(st)
            h["s_today"].config(text=str(ws["today"]))
            h["s_week"].config(text=str(ws["week"]))
            h["s_best"].config(text=str(ws["best"]))
            h["s_best_lbl"].config(
                text="лучший день · %s" % self.DAYS[ws["vals"].index(ws["best"])]
                if ws["best"] else "лучший день недели")
            self._draw_week(ws)
            last, best = game_stats(st)
            h["games"].config(
                text="за одну игру: последняя %d · рекорд %d  (по реплею)" % (last, best)
                if last is not None else
                "за одну игру: появится после первого реплея (режим «по реплею»)")
            for k, on in (("auto", st["auto"]), ("map", st["minimap"])):
                txt, bg, fg = self._pill_style(on)
                h[k].config(text=txt, bg=bg, fg=fg)
        except tk.TclError:
            pass

    def _build_next(self):
        if not self.alive() or not self._pending:
            return
        self._ensure_pane(self._pending.pop(0))
        if self._pending:
            self.win.after(30, self._build_next)
        else:
            self.win.after(60, self._warm)

    def _warm(self):
        """Один раз «прогреваем» меню невидимо (почти прозрачным и за краем экрана): Windows
        создаёт окна всех виджетов и рисует настройки сразу. Раньше это происходило на лету
        при первом ПКМ — поэтому сначала появлялся фон, а содержимое подгружалось потом."""
        if not self.alive() or self.visible or self._warmed or self._pending:
            return
        self._warmed = True
        w = self.win
        try:
            w.attributes("-alpha", 0.01)
            w.geometry("+-6000+-6000")
            w.deiconify()
            for r in self.rows.values():
                if r["holder"] is not None:
                    r["holder"].configure(height=r["h"])
            w.update()
            hs = [r["h"] for r in self.rows.values() if r["holder"] is not None]
            for r in self.rows.values():
                if r["holder"] is not None:
                    r["holder"].configure(height=1)
            w.update_idletasks()
            base = w.winfo_reqheight()
            for r in self.rows.values():
                if r["holder"] is not None:
                    r["holder"].configure(height=max(1, int(round(r["cur"]))))
            w.update_idletasks()
            if hs:
                # Окно один раз делаем таким высоким, чтобы влезала самая большая панель
                # настроек, и закрашиваем. Дальше его размер не меняется — отсюда нет
                # «чёрной пустоты» при выдвижении (видимую форму подрезает _clip).
                full = min(base + max(hs), w.winfo_screenheight() - 40)
                w.geometry(f"{w.winfo_reqwidth()}x{full}")
                w.update()
        except tk.TclError:
            pass
        finally:
            if not self.visible and self.alive():
                w.attributes("-alpha", 0.0)
                w.withdraw()

    def _enabled(self, key):
        st = self.app.state
        return {"panel": st["panel"], "notify": st["notify"], "auto": st["auto"],
                "minimap": st["minimap"]}[key]

    def _row(self, parent, key, name, desc):
        enabled = self._enabled(key)
        avail = AUTO_OK or key in ("panel", "notify")
        wrap = tk.Frame(parent, bg=T["bg"])
        wrap.pack(fill="x", padx=14, pady=4)
        card = tk.Frame(wrap, bg=T["card"], cursor="hand2")
        card.pack(fill="x")
        bar = tk.Frame(card, bg=T["open"] if enabled else T["border"], width=4)
        bar.pack(side="left", fill="y")
        icon = tk.Canvas(card, width=44, height=44, bg=T["card"], highlightthickness=0,
                         cursor="hand2")
        icon.pack(side="left", padx=(12, 0), pady=10)
        fill, glyph = self._icon_colors(enabled)
        ibg = round_poly(icon, 1, 1, 43, 43, 12, fill=fill, outline="")
        igl = icon.create_text(22, 22, text=self.ICONS.get(key, "●"), fill=glyph,
                               font=(FONT, 15))
        mid = tk.Frame(card, bg=T["card"])
        mid.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        top = tk.Frame(mid, bg=T["card"])
        top.pack(anchor="w")
        nm = tk.Label(top, text=name, bg=T["card"], fg=T["fg"] if enabled else T["dim"],
                      font=(FONT, 11, "bold"), anchor="w")
        nm.pack(side="left")
        ptxt, pbg, pfg = self._pill_style(enabled)
        pill = tk.Label(top, text=ptxt, bg=pbg, fg=pfg, font=(FONT, 7, "bold"),
                        padx=4, pady=1)
        pill.pack(side="left", padx=(8, 0))
        ds = tk.Label(mid, text=desc if avail else (AUTO_ERR or ""), bg=T["card"],
                      fg=T["dim"] if avail else ALERT, font=(FONT, 9), anchor="w",
                      justify="left", wraplength=MENU_W - 250)
        ds.pack(anchor="w", pady=(2, 0))
        right = tk.Frame(card, bg=T["card"])
        right.pack(side="right", padx=12)
        if self.KEYS[key]:
            tk.Label(right, text=self.KEYS[key], bg=T["card_hi"], fg=T["dim"],
                     font=(FONT, 8, "bold"), padx=6, pady=2).pack(side="left", padx=(0, 10))
        gear = tk.Label(right, text="⚙", bg=T["card"],
                        fg=T["open"] if self.expanded == key else T["dim"], font=(FONT, 12))
        gear.pack(side="left", padx=(0, 10))
        sw = Switch(right, enabled, lambda k=key: self.toggle(k))
        sw.pack(side="left")
        self.rows[key] = {"wrap": wrap, "card": card, "bar": bar, "name": nm, "sw": sw,
                          "gear": gear, "pill": pill, "icon": icon, "ibg": ibg, "igl": igl,
                          "bgw": [card, icon, mid, top, right, nm, ds, gear],
                          "bg": T["card"], "hov": False, "on": enabled,
                          "holder": None, "pane": None, "h": 1, "cur": 1}
        bind_tree(card, "<Button-1>", lambda _e, k=key: self.toggle(k), skip_attr="_skip_row")
        bind_tree(card, "<Button-3>", lambda _e, k=key: self.expand(k))
        bind_tree(card, "<Enter>", lambda _e, k=key: self._hover(k, True))
        bind_tree(card, "<Leave>", lambda _e, k=key: self.win.after(25, self._hover_check, k))

    # ---------- панель настроек функции (строится заранее) ----------
    def _ensure_pane(self, key):
        r = self.rows.get(key)
        if r is None or r["pane"] is not None:
            return
        holder = tk.Frame(r["wrap"], bg=T["card"], height=1)
        holder.pack_propagate(False)
        pane = tk.Frame(holder, bg=T["card"])
        accent = tk.Frame(pane, bg=T["open"], height=2)
        accent.pack(fill="x")
        tk.Frame(pane, bg=T["card"], height=8).pack(fill="x")
        self._settings(pane, key)
        pane.place(x=0, y=0, relwidth=1.0)
        curtain = tk.Frame(holder, bg=T["card"])      # накрывает ещё не раскрытую часть
        pane.update_idletasks()
        r["holder"], r["pane"], r["accent"], r["curtain"] = holder, pane, accent, curtain
        r["h"] = max(1, pane.winfo_reqheight())
        # Блок всегда «на месте» высотой в 1 px (внутри обрезается) — его не нужно
        # заново показывать/прятать, поэтому при ПКМ ничего не подгружается.
        holder.pack(fill="x")

    def _set_open(self, key):
        r = self.rows[key]
        r["holder"].configure(height=r["h"])
        r["cur"] = r["h"]

    def _set_h(self, r, h):
        """Высота блока настроек — одним махом, без покадровой анимации."""
        r["cur"] = h
        r["holder"].configure(height=max(1, int(h)))

    def _win_w(self):
        w = self.win
        ww = w.winfo_width()
        return ww if ww > 1 else w.winfo_reqwidth()

    def _clip(self, h, redraw=False):
        """Видимая (скруглённая) форма окна = высота h. Само окно при этом не меняет размер:
        оно заранее сделано высоким и закрашено, поэтому «чёрной пустоты» при выдвижении нет."""
        try:
            if self._hwnd is None:
                self._hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            r = int(self.app.state["radius"]) * 2
            rgn = gdi32.CreateRoundRectRgn(0, 0, self._win_w() + 1, int(h) + 1, r, r)
            user32.SetWindowRgn(self._hwnd, rgn, redraw)
        except Exception:  # noqa: BLE001
            pass

    def _settle(self):
        """Подогнать видимую форму под содержимое (и окно, если оно вдруг оказалось низким)."""
        if not self.alive():
            return
        w = self.win
        w.update_idletasks()
        need = w.winfo_reqheight()
        if w.winfo_ismapped() and need > w.winfo_height():
            w.geometry(f"{self._win_w()}x{need}+{w.winfo_x()}+{w.winfo_y()}")
            w.update()
        self._clip(need, True)
        self._clamp_screen(False)

    _relayout = _settle

    def _instant(self, changes):
        """Без анимации: новая высота ставится сразу, раскладка и форма окна — один раз."""
        for k, t in changes:
            self.rows[k]["curtain"].place_forget()
            self._set_h(self.rows[k], t)
        self._settle()

    def _wipe(self, changes, ms=230):
        """Раскрытие «шторкой» (MENU_ANIM = "wipe"). Раскладка меняется ОДИН раз, дальше
        двигается только один лёгкий виджет-шторка, а настройки проявляются сверху вниз."""
        rows = self.rows
        w = self.win
        if not self.visible:
            for k, t in changes:
                self._set_h(rows[k], t)
            return
        opening = [(k, t) for k, t in changes if t > rows[k]["cur"]]
        closing = [(k, t) for k, t in changes if t <= rows[k]["cur"]]
        anim_close = not opening           # просто закрыли — плавно; переключили — сразу
        for k, t in closing:
            c = rows[k]["curtain"]
            c.place_forget()
            if anim_close:
                c.place(x=0, y=0, relwidth=1.0, height=1)
                c.lift()
            else:
                self._set_h(rows[k], t)
        for k, t in opening:
            c = rows[k]["curtain"]
            c.place(x=0, y=0, relwidth=1.0, height=max(1, int(t)))   # сразу закрыто шторкой
            c.lift()
            self._set_h(rows[k], t)
        if not anim_close:
            self._settle()
            try:
                w.update()
            except tk.TclError:
                return

        def fn(e):
            for k, t in opening:
                h = max(1, int(t))
                y = int(h * e)
                c = rows[k]["curtain"]
                if y >= h:
                    c.place_forget()
                else:
                    c.place_configure(y=y, height=h - y)
            if anim_close:
                for k, _t in closing:
                    rows[k]["curtain"].place_configure(
                        height=max(1, int(rows[k]["h"] * e)))
            try:
                w.update()
            except tk.TclError:
                pass

        def finish():
            for k, _t in opening:
                rows[k]["curtain"].place_forget()
            if anim_close:
                for k, t in closing:
                    rows[k]["curtain"].place_forget()
                    self._set_h(rows[k], t)
                self._settle()

        self._anim("grow", ms, fn, finish)

    def _grow(self, changes, ms=250):
        """Раскрытие с «выдавливанием» строк ниже (MENU_ANIM = "push"). Окно заранее высокое и
        закрашенное (см. _warm), поэтому на кадрах не меняется ни размер окна, ни его форма:
        меняется только высота блока, строки ниже съезжают."""
        if MENU_ANIM == "none":
            return self._instant(changes)
        if MENU_ANIM != "push":
            return self._wipe(changes)
        if not changes:
            return
        rows = self.rows
        starts = {k: rows[k]["cur"] for k, _t in changes}
        if not self.visible:
            for k, t in changes:
                self._set_h(rows[k], t)
            return
        w = self.win
        w.update_idletasks()
        Hc = w.winfo_reqheight()                  # высота содержимого сейчас
        Hneed = max(Hc, Hc + sum(t - starts[k] for k, t in changes))
        x, y = w.winfo_x(), w.winfo_y()
        sh = w.winfo_screenheight()
        if y + Hneed > sh - 20:
            y = max(0, sh - 20 - Hneed)
            w.geometry(f"+{x}+{y}")
        self._x, self._y = x, y
        if Hneed > w.winfo_height():              # заранее увеличить не успели (редко)
            self._clip(Hc, True)
            w.geometry(f"{self._win_w()}x{Hneed}+{x}+{y}")
            try:
                w.update()
            except tk.TclError:
                return
        self._clip(Hneed, True)                   # видно и старую, и новую высоту

        def fn(e):
            for k, t in changes:
                self._set_h(rows[k], starts[k] + (t - starts[k]) * e)
            try:
                w.update_idletasks()
                w.update()
            except tk.TclError:
                pass

        def finish():
            for k, t in changes:
                self._set_h(rows[k], t)
            self._settle()

        self._anim("grow", ms, fn, finish)

    def _clamp_screen(self, update=True):
        """Окно растёт вниз — если упёрлось в низ экрана, сдвигаем вверх."""
        if not self.visible:
            return
        w = self.win
        if update:
            w.update_idletasks()
        sh = w.winfo_screenheight()
        h = w.winfo_reqheight()
        if self._y + h > sh - 20:
            self._y = max(0, sh - 20 - h)
            w.geometry(f"+{self._x}+{int(self._y)}")

    def expand(self, key):
        """ПКМ: из функции плавно выдвигается её блок настроек (готовая панель уже построена),
        предыдущий в это же время сворачивается, акцентная полоска проявляется."""
        prev = self.expanded
        self.expanded = None if prev == key else key
        changes = []
        if prev and prev != self.expanded and prev in self.rows \
                and self.rows[prev]["holder"] is not None:
            changes.append((prev, 1))
        if self.expanded:
            self._ensure_pane(key)
            changes.append((key, self.rows[key]["h"]))
        self._grow(changes)
        if self.expanded:
            acc = self.rows[key].get("accent")
            if acc is not None:
                if MENU_ANIM == "none":
                    acc.config(bg=T["open"])
                else:
                    self._anim("acc_" + key, 300,
                               lambda e, a=acc: a.config(bg=_mix(T["card"], T["open"], e)))
        for k, r in self.rows.items():
            r["gear"].config(fg=T["open"] if self.expanded == k else T["dim"])

    def _settings(self, box, key):
        app, st = self.app, self.app.state
        if key == "panel":
            toggle_row(box, "Видно на скриншотах и в трансляции",
                       st["capture_visible"],
                       lambda v: app.apply_setting("capture_visible", v))
            tk.Label(box, text="Скриншот, запись, демонстрация экрана в Дискорде. С включённым "
                               "автоподсчётом само уведомление в кадр не попадает — чтобы "
                               "программа не считала себя.",
                     bg=T["card"], fg=T["dim"], font=(FONT, 8), justify="left", anchor="w",
                     wraplength=MENU_W - 60).pack(anchor="w", padx=18, pady=(0, 4))
            self.pin_sw = toggle_row(box, "Закрепить (клики проходят сквозь панель)",
                                     app.pinned, lambda v: app.set_pinned(v))
            self.obs_sw = toggle_row(box, "Окно для OBS (отдельное окно)",
                                     st["obs_window"], lambda v: app.set_obs(v))
            tk.Label(box, text="В OBS: Источники → + → Захват окна → «Fortnite Counter (OBS)». "
                               "Метод захвата — Windows 10 (1903 и новее). Окно не сворачивайте: "
                               "OBS не снимает свёрнутые окна. Размер меняется, число растягивается.",
                     bg=T["card"], fg=T["dim"], font=(FONT, 8), justify="left", anchor="w",
                     wraplength=MENU_W - 60).pack(anchor="w", padx=18, pady=(0, 4))
            build_settings(box, st, app.apply_setting, self.restyle, app,
                           sections={"count", "theme", "color", "hotkey", "view", "cooldown", "reset"},
                           preview=False).pack(fill="x", pady=(0, 6))
        elif key == "notify":
            menu_slider(box, "Время показа", 1.0, 6.0, 0.5, st["notify_sec"],
                        lambda v: f"{v:.1f} с",
                        lambda v: app.apply_setting("notify_sec", v))
            menu_slider(box, "Размер", 0.7, 1.6, 0.05, st["notify_scale"],
                        lambda v: f"{int(round(v * 100))}%",
                        lambda v: app.apply_setting("notify_scale", v))
            r = tk.Frame(box, bg=T["card"])
            r.pack(fill="x", padx=18, pady=(6, 4))
            flat_button(r, "Переместить окно", lambda: app.notifier.edit(True), width=17,
                        font=(FONT, 9, "bold")).pack(side="left", ipady=3)
            flat_button(r, "Показать пример", lambda: app.notifier.demo(), width=15,
                        font=(FONT, 9, "bold")).pack(side="left", padx=8, ipady=3)
            tk.Label(box, text="Появляется, когда счётчик вырос (кнопкой, клавишей или автоподсчётом). "
                               "Клики проходят сквозь окно, игру оно не перехватывает.",
                     bg=T["card"], fg=T["dim"], font=(FONT, 8), justify="left", anchor="w",
                     wraplength=MENU_W - 60).pack(anchor="w", padx=18, pady=(4, 12))
        elif key == "minimap":
            lib_row(box, "нужна для мини-карты и слежения")
            toggle_row(box, "Радар (карта едет за игроком; выкл — вся карта)",
                       st["map_mode"] == "radar",
                       lambda v: app.apply_setting("map_mode", "radar" if v else "full"))
            toggle_row(box, "Следить за игроком (по миникарте игры)", st["map_track"],
                       lambda v: app.apply_setting("map_track", v))
            menu_slider(box, "Размер радара", MAP_R_MIN, 600, 10, st["map_r"],
                        lambda v: f"{int(v)} px",
                        lambda v: app.apply_setting("map_r", v))
            menu_slider(box, "Приближение", 0.4, 3.0, 0.05, st["map_zoom"],
                        lambda v: f"x{v:.2f}",
                        lambda v: app.apply_setting("map_zoom", v))
            menu_slider(box, "Размер всей карты", MAP_W_MIN, 1100, 20, st["map_w"],
                        lambda v: f"{int(v)} px",
                        lambda v: app.apply_setting("map_w", v))
            menu_slider(box, "Прозрачность фона карты", 0.3, 1.0, 0.05, st["map_alpha"],
                        lambda v: f"{int(round(v * 100))}%",
                        lambda v: app.apply_setting("map_alpha", v))
            toggle_row(box, "Закрепить (клики проходят сквозь карту)", st["map_lock"],
                       lambda v: app.apply_setting("map_lock", v))
            r = tk.Frame(box, bg=T["card"])
            r.pack(fill="x", padx=18, pady=(6, 4))
            flat_button(r, "Перечитать картинку", lambda: app.mapwin.reload(), width=18,
                        font=(FONT, 9, "bold")).pack(side="left", ipady=3)
            flat_button(r, "Сбросить позицию", lambda: app.mapwin.reset_pos(), width=16,
                        font=(FONT, 9, "bold")).pack(side="left", padx=8, ipady=3)
            tk.Label(box, text="Тащи мышью, колесо — приближение (радар) или размер (вся карта), "
                               "двойной клик — смена режима. Нужны chest_map.png и map_ref.png "
                               "рядом со скриптом. Позицию берёт с твоей миникарты в игре — она "
                               "должна быть на экране и не вращаться. Пока идёт слежение, окно "
                               "скрыто от записи экрана. Ctrl+Alt+M — показать / скрыть.",
                     bg=T["card"], fg=T["dim"] if not MAP_ERR else ALERT, font=(FONT, 8),
                     justify="left", anchor="w",
                     wraplength=MENU_W - 60).pack(anchor="w", padx=18, pady=(4, 12))
        elif key == "auto":
            modes = (("game", "Автоподсчёт в игре",
                      "Бывает ложное срабатывание"),
                     ("replay", "Автоподсчёт после игры, по реплею",
                      "Считает точно, но только после игры"))
            sws = {}
            desc = tk.Label(box, bg=T["card"], font=(FONT, 9), justify="left", anchor="w",
                            wraplength=MENU_W - 60)

            def paint():
                mode, auto = app.state["auto_mode"], app.state["auto"]
                for m, sw in sws.items():
                    sw.set(auto and mode == m)
                if mode == "game":
                    bad = not AUTO_OK
                    desc.config(
                        text=AUTO_ERR if bad else
                        "Когда в игре появляется плашка опыта за открытый сундук, счётчик "
                        "увеличивается сам.\nОдновременно работает только один вариант. "
                        "Быстрый переключатель: Ctrl+Alt+A.",
                        fg=ALERT if bad else T["dim"])
                else:
                    desc.config(
                        text="Когда матч закончился, программа читает его реплей (replay_scan.exe) "
                             "и добавляет все открытые сундуки сразу. В Fortnite должна быть "
                             "включена запись реплеев.\nОдновременно работает только один "
                             "вариант. Быстрый переключатель: Ctrl+Alt+A.",
                        fg=T["dim"])

            def flip(m):
                app.set_auto_option(m, not (app.state["auto"] and app.state["auto_mode"] == m))
                paint()

            for m, title, sub in modes:
                r = tk.Frame(box, bg=T["card"])
                r.pack(fill="x", padx=18, pady=(8, 0))
                col = tk.Frame(r, bg=T["card"])
                col.pack(side="left", fill="x")
                tk.Label(col, text=title, bg=T["card"], fg=T["fg"], font=(FONT, 10),
                         anchor="w").pack(anchor="w")
                tk.Label(col, text=sub, bg=T["card"], fg=T["dim"], font=(FONT, 7),
                         anchor="w").pack(anchor="w")
                sw = Switch(r, False, lambda mm=m: flip(mm))
                sw.pack(side="right")
                sws[m] = sw
            app.auto_paint = paint
            lib_row(box, "нужна для автоподсчёта в игре")
            desc.pack(anchor="w", padx=18, pady=(6, 12))
            paint()

    # ---------- наведение ----------
    def _hover(self, key, on):
        r = self.rows.get(key)
        if r is None or r["hov"] == on:
            return
        r["hov"] = on
        c0, c1 = r["bg"], (T["card_hi"] if on else T["card"])

        def fn(e):
            col = _mix(c0, c1, e)
            r["bg"] = col
            for wd in r["bgw"]:
                wd.config(bg=col)
            r["sw"].config(bg=col)

        self._anim("hov_" + key, 140, fn)

    def _hover_check(self, key):
        if not self.alive():
            return
        try:
            x, y = self.win.winfo_pointerxy()
            wd = self.win.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            wd = None
        card = self.rows.get(key, {}).get("card")
        inside = False
        while wd is not None:
            if wd is card:
                inside = True
                break
            wd = getattr(wd, "master", None)
        self._hover(key, inside)

    # ---------- действия ----------
    def toggle(self, key):
        app = self.app
        {"panel": app.toggle_panel, "notify": app.toggle_notify,
         "auto": app.toggle_auto, "minimap": app.toggle_minimap}[key]()

    def sync(self):
        """Обновить состояние на месте (без пересборки): переключатели, цвета, кнопку закрепления."""
        if not self.alive():
            return
        for key, r in self.rows.items():
            on = self._enabled(key)
            if r["on"] == on:
                continue
            r["on"] = on
            r["sw"].set(on)
            b0, b1 = (T["border"], T["open"]) if on else (T["open"], T["border"])
            f0, f1 = (T["dim"], T["fg"]) if on else (T["fg"], T["dim"])

            def fn(e, r=r, b0=b0, b1=b1, f0=f0, f1=f1):
                r["bar"].config(bg=_mix(b0, b1, e))
                r["name"].config(fg=_mix(f0, f1, e))

            self._anim("tg_" + key, 160, fn)
            self._style_row(r, on)
        self._update_hero()
        if self.pin_sw is not None:
            self.pin_sw.set(self.app.pinned)
        if self.obs_sw is not None:
            self.obs_sw.set(self.app.state["obs_window"])

    refresh = sync

    def restyle(self):
        """Тема/цвета сменились — меню пересобирается (редкая операция)."""
        if self.alive():
            self.win.after(1, self.build)
            self.win.after(40, self._settle)

    # ---------- показать / спрятать ----------
    def _frame(self, a, dy):
        self._a = a
        self.win.attributes("-alpha", self.ALPHA * a)
        self.win.geometry(f"+{self._x}+{int(self._y + dy)}")

    def show(self):
        if not self.alive() or self.visible:
            return
        self.visible = True
        self._update_hero()
        try:
            self.prev_fg = user32.GetForegroundWindow()   # куда вернуть фокус (в игру)
        except Exception:  # noqa: BLE001
            self.prev_fg = None
        w = self.win
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x, y = self.app.state["menu_x"], self.app.state["menu_y"]
        if x < 0 or y < 0 or x > sw - 100 or y > sh - 100:
            x = (sw - w.winfo_reqwidth()) // 2
            y = max(20, (sh - w.winfo_reqheight()) // 4)
        self._x, self._y = x, y
        a0 = self._a
        self._frame(a0, 18 * (1 - a0))
        w.deiconify()
        w.lift()
        w.update_idletasks()
        self._clip(w.winfo_reqheight(), True)       # форма окна = по содержимому
        self._anim("win", 200, lambda e: self._frame(a0 + (1 - a0) * e, 18 * (1 - (a0 + (1 - a0) * e))))
        w.focus_force()

    def hide(self):
        if not self.alive() or not self.visible:
            return
        self.visible = False
        app = self.app
        app.capturing = False
        app.capture_conflict = None
        save_state(app.state)
        try:                              # фокус сразу назад в игру
            if self.prev_fg:
                user32.SetForegroundWindow(self.prev_fg)
        except Exception:  # noqa: BLE001
            pass
        a0 = self._a
        y0 = self.win.winfo_y()
        self._anim("win", 130,
                   lambda e: (self.win.attributes("-alpha", self.ALPHA * a0 * (1 - e)),
                              self.win.geometry(f"+{self._x}+{int(y0 + 10 * e)}"),
                              setattr(self, "_a", a0 * (1 - e))),
                   done=lambda: self.win.withdraw())


# ======================= Уведомление «+1 сундук» =======================
class NotifyToast:
    """Окошко-уведомление «+1 Сундук добавлен». Обычно пропускает клики и не забирает фокус
    у игры. Кнопка «Переместить окно» в меню включает режим перемещения: окно можно
    перетащить мышью куда угодно, позиция запоминается. Окно исключено из снимка экрана,
    чтобы автоподсчёт не принял его надпись за плашку из игры."""

    ALPHA = 0.96
    BASE_W = 270

    def __init__(self, app):
        self.app = app
        self.win = None
        self.hwnd = None
        self.region = None
        self.sig = None
        self.editing = False
        self.visible = False
        self._a = 0.0
        self._x = self._y = 0
        self._tok = None
        self._hide_job = None
        self._demo_job = None
        self._sub = self._title = self._ok = self._txt = None

    # ---------- служебное ----------
    def _px(self, v):
        return max(1, int(round(v * self.app.state["notify_scale"])))

    def _fs(self, v):
        return max(6, int(round(v * self.app.state["notify_scale"])))

    def _alive(self):
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _ensure(self):
        st = self.app.state
        if not self._alive():
            w = tk.Toplevel(self.app.root)
            w.withdraw()
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.attributes("-alpha", 0.0)
            self.win = w
            self.hwnd = None
            self.sig = None
            self.region = make_rounded(w, lambda: self._px(self.app.state["radius"]))
        sig = (st["theme"], st["col_open"], st["notify_scale"], st["radius"])
        if sig != self.sig:
            self.sig = sig
            self._build()

    def _build(self):
        w, st = self.win, self.app.state
        px, fs = self._px, self._fs
        for ch in w.winfo_children():
            ch.destroy()
        w.configure(bg=T["bg"])
        outer = tk.Frame(w, bg=T["bg"])
        outer.pack(fill="both", expand=True)
        tk.Frame(outer, bg=T["bg"], width=px(self.BASE_W), height=1).pack()
        card = tk.Frame(outer, bg=T["card"])
        card.pack(fill="both", expand=True, padx=px(8), pady=(0, px(8)))
        chest_badge(card, px(40), _mix(T["card"], T["open"], 0.22), T["open"],
                    T["card"]).pack(side="left", padx=(px(10), 0), pady=px(8))
        tk.Label(card, text="+1", bg=T["card"], fg=T["open"],
                 font=(FONT, fs(22), "bold")).pack(side="left", padx=(px(10), px(8)),
                                                   pady=px(8))
        self._ok = flat_button(card, "Готово", lambda: self.edit(False),
                               bg=T["open"], fg=on_color(T["open"]), width=7,
                               font=(FONT, fs(9), "bold"), hover=T["open"])
        self._txt = tk.Frame(card, bg=T["card"])
        self._txt.pack(side="left", fill="x", expand=True, padx=(0, px(12)))
        self._title = tk.Label(self._txt, text="Сундук добавлен", bg=T["card"], fg=T["fg"],
                               font=(FONT, fs(11), "bold"), anchor="w")
        self._title.pack(anchor="w")
        self._sub = tk.Label(self._txt, text="", bg=T["card"], fg=T["dim"],
                             font=(FONT, fs(9)), anchor="w")
        self._sub.pack(anchor="w")
        # сверху отступ под зазор (spacer занимает всю ширину, карточка ниже него)
        outer.pack_configure(pady=(px(8), 0))

        def moved(x, y):
            st["notify_x"], st["notify_y"] = x, y
            self._x, self._y = x, y

        bind_drag(outer, w, on_move=moved, on_end=lambda: save_state(st))
        if self.editing:
            self._enter_edit_ui()
        w.update_idletasks()
        if self.region:
            self.win.after(5, self.region)

    def _style(self, click_through):
        try:
            if self.hwnd is None:
                self.win.update_idletasks()
                self.hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
                try:                    # WDA_EXCLUDEFROMCAPTURE: не попадает в снимок экрана
                    user32.SetWindowDisplayAffinity(self.hwnd, self.app._affinity(True))
                except Exception:  # noqa: BLE001
                    pass
            ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            ex |= WS_EX_LAYERED
            if click_through:
                ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)
        except Exception:  # noqa: BLE001
            pass

    def _place(self):
        """Позиция из настроек; если её нет или она за экраном — сверху по центру."""
        w, st = self.win, self.app.state
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x, y = st["notify_x"], st["notify_y"]
        if x < 0 or y < 0 or x > sw - 40 or y > sh - 40:
            x = (sw - w.winfo_reqwidth()) // 2
            y = int(30 * sh / 1080.0)
        self._x, self._y = x, y

    def _frame(self, a, dy):
        self._a = a
        self.win.attributes("-alpha", self.ALPHA * a)
        self.win.geometry(f"+{self._x}+{int(self._y + dy)}")

    def _cancel(self):
        if self._tok:
            self._tok["on"] = False
        if self._hide_job is not None:
            try:
                self.app.root.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None

    def _appear(self):
        w = self.win
        a0 = self._a if self.visible else 0.0
        self.visible = True
        self._frame(a0, -14 * (1 - a0))
        w.deiconify()
        w.lift()

        def fn(e):
            a = a0 + (1 - a0) * e
            self._frame(a, -14 * (1 - a))

        self._tok = animate(w, 180, fn)

    # ---------- показать / спрятать ----------
    def show(self, sub="", hold=None):
        if self.editing:
            return
        try:
            self._ensure()
        except tk.TclError:
            return
        self._cancel()
        self._sub.config(text=sub)
        self._title.config(text="Сундук добавлен")
        self._style(True)
        self._place()
        self._appear()
        ms = int((hold if hold else self.app.state["notify_sec"]) * 1000) + 180
        self._hide_job = self.app.root.after(ms, self.hide)

    def hide(self):
        self._hide_job = None
        if self.editing or not self._alive() or not self.visible:
            return
        self._cancel()
        self.visible = False
        a0 = self._a
        w = self.win

        def fn(e):
            self._a = a0 * (1 - e)
            w.attributes("-alpha", self.ALPHA * self._a)
            w.geometry(f"+{self._x}+{int(self._y - 8 * e)}")

        self._tok = animate(w, 220, fn, done=w.withdraw)

    def demo(self, delay=0):
        """Показать пример (например, после смены размера — с небольшой задержкой)."""
        if self._demo_job is not None:
            try:
                self.app.root.after_cancel(self._demo_job)
            except tk.TclError:
                pass
            self._demo_job = None

        def go():
            self._demo_job = None
            if self.editing:
                return
            self.show("Так будет выглядеть уведомление", hold=2.5)

        if delay:
            self._demo_job = self.app.root.after(delay, go)
        else:
            go()

    # ---------- режим перемещения ----------
    def _enter_edit_ui(self):
        self._title.config(text="Перетащите окно")
        self._sub.config(text="Поставьте куда удобно и нажмите «Готово»")
        self._ok.pack(side="right", padx=(0, self._px(10)), before=self._txt)

    def edit(self, on):
        if on:
            try:
                self._ensure()
            except tk.TclError:
                return
            self._cancel()
            self.editing = True
            self._enter_edit_ui()
            self._style(False)
            self._place()
            self._appear()
        else:
            if not self.editing:
                return
            self.editing = False
            save_state(self.app.state)
            if self._alive():
                self._ok.pack_forget()
                self._style(True)
                self._title.config(text="Сундук добавлен")
                self._sub.config(text="Окно сохранено здесь")
                self._cancel()
                self._hide_job = self.app.root.after(1100, self.hide)

    def keep_on_top(self):
        if self.visible and self._alive():
            self.win.attributes("-topmost", True)

    def destroy(self):
        self._cancel()
        if self._alive():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None


# ======================= Мини-карта сундуков + трекинг игрока =======================
try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk
    MAP_ERR = None
    _RS = getattr(Image, "Resampling", Image)
    _TF = getattr(Image, "Transform", Image)
except Exception:  # noqa: BLE001
    Image = ImageTk = ImageChops = ImageDraw = ImageFont = _RS = _TF = None
    MAP_ERR = "Мини-карта: pip install pillow"

MAP_NAMES = ("chest_map.png", "chest_map.jpg", "chest_map.jpeg", "chest_map.webp")
REF_NAMES = ("map_ref.png", "map_ref.jpg", "map_ref.jpeg")   # чистая карта без сундуков
MAP_W_MIN, MAP_W_MAX = 200, 1400       # ширина режима «вся карта»
MAP_R_MIN, MAP_R_MAX = 160, 700        # сторона радара
MAP_ZOOM_MIN, MAP_ZOOM_MAX = 0.4, 3.0  # 1.0 = как у внутриигровой миникарты

# --- Трекинг: позицию берём по КАРТИНКЕ внутриигровой миникарты (снимок экрана).
# --- Память игры не читаем, ничего не пишем, никуда не внедряемся.
# Где миникарта на экране, доли кадра (x0, x1, y0, y1). Подобрано под 1920x1080, HUD 100%.
# FILL: другой HUD/разрешение -> python fortnite_counter.py --dumpmini, глянь вырез и поправь доли.
MINIMAP_REGION = (0.8525, 0.9843, 0.0297, 0.2641)
MINIMAP_NORM = 253                     # вырез приводим к 253x253 — масштаб всегда один
# Чистая карта (map_ref.png, 612x609) -> chest_map.png: x' = a*x + b*y + c ; y' = d*x + e*y + f
REF_TO_CHEST = (2.6544, -0.0089, -84.87, -0.0096, 2.6583, -62.04)
RADAR_SPAN = 285.0                     # сколько пикселей chest_map видит миникарта игры (zoom 1.0)
TRACK_SCALE = (0.30, 0.60)             # допустимый масштаб вырез -> карта (по калибровке 0.424)
TRACK_MIN_INLIERS = 8
TRACK_HZ = 20
TRACK_LOST_SEC = 1.5
TRACK_LOCAL_R = 150                    # после захвата ищем только рядом с прошлой точкой (px карты)
POS_SMOOTH = 0.6                       # 1.0 — без сглаживания
HEAD_MIN_MOVE = 5.0                    # от какого сдвига (px chest_map) обновляем направление стрелки


# --- Маска маркеров: фон карты полупрозрачный, сундуки / цифры / бейджи городов — всегда 100% ---
MARK_YEL = (20, 36, 140, 190)      # жёлтая обводка: H от, H до, S больше, V больше
MARK_RED = (7, 170, 150, 150)      # красные бейджи городов: H <= 7 или H >= 170, S, V больше
MARK_ORG = (8, 19, 180, 200)       # оранжевые цифры: H от, H до, S, V
MARK_DARK_V = 48                   # чёрная подложка маркера: яркость меньше
MARK_SIZE = (18, 75)               # допустимый размер подложки, px
MARK_MIN_AREA = 60                 # мельче контуры выбрасываем
MARK_RING = (28, 62)               # сундук без жёлтой обводки: тёмное кольцо, размер в px
MARK_RING_FILL = (0.2, 1.01)       # доля тёмных пикселей внутри (кольцо ~0.4, тёмный силуэт ~1)
MARK_PLATE = 2.5                   # цифра без жёлтой обводки: подложка = размер цифры x это
MARK_PLATE_DARK = 0.25             # ...если в этой подложке хотя бы столько тёмных пикселей

MARK_ICON_THR = 0.55              # иконка сундука: порог совпадения с шаблоном
MARK_ICON_DARK = 0.10             # ...и доля тёмных пикселей вокруг (рамка маркера)
MARK_ICON_R = 25                  # радиус круглой подложки вокруг иконки, px
MARK_ICON_B64 = (                 # шаблон иконки сундука 30x24 с chest_map.png
    "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAYCAIAAABiC9roAAAG0UlEQVRIDTXBSY8jVwEA4LdVvffqVbmq3La77ba720OSmZ5J"
    "QOGC4MCJCwdQDhxAuSAhwRE4c+C3ARJKDixShGaS6Zl2t9t7eal9ewsoEt8HP+iB8ehmPHpp21ZaPZZqRxzpCTYMe7Jui6w8"
    "xWmelnUDELQIsRG0ldKNlEoZYxT8P9/1tdYGQang6Vju9zv4i5+8uji/Cr2rsizj/JHwsj/i/Z5rGaOlkrUsqratVNUYJYHR"
    "mBCmlakqmSRJkVcQQvdb/X4/K4oib4pKRrvjYrGCf/7jz6HGZUzTpLKFnEzDi4nHmK6SI4YGAKQVlC2Q0igJjUZ13dalTOIs"
    "SdKmlpzzs7Mz3/eF5z4tt0+LbZ7VSVbudkf4pz/8qKmaZMtlQydXw49eTkUH5/lOtQm1oEWo1kA20hhoW4xSHkX7aLPfbqOm"
    "aQIvGAwGQRBYlO/20Zu72f39qm0AInZRNPCXn4UU27AecWtwPb159uxawXK9faDYcIf+j25lXdcE4eBby6fFbDZ7enzEGD+7"
    "mV5fXzuO07Tqy3/8+83d7OFhZ1EnDM7a1sDffD7pdrp1HNS53e12EYFJcYTQ+K6vlMIYIwTKImvrqtcLrq/G7+6+zvO0zFPX"
    "4bfPP7q6uorj49t3s7988c84ayFgftgjhOy2Mfzt5590PJFHtEzhWS/EFkyKCEALgY5WCGEAoanKTDZ54IvLUe902FVVUuUZ"
    "tdF4ctkLg8N+/242f786pIXCiHf8LgBgs9nD3//6pw63460s8npw0SFMJ3mkNTNmqKRtjDS6aapMykw4VjcUbZPKplBtQQkO"
    "A5cyO9qsHxabfW6KFto2E47XtnKz2sLf/eoz17HTQ9O29WgcMAFO6a6RjLGP2sZum7Jps6YuIKhcjoRDivQg2wIbKRwadBwD"
    "1G6zXWyiTSwbQ4ht2cRK0yzabODPfvzD0PfaCmBirqd95uBDfKha22bX0thtXZXFyaiGUdARFqPotF+3TUGADjqu6/CmLnfr"
    "zeaYZpJUBhgg67qO1vv4VMIfvJwO+n0CKOf05tk5ddjxWNYSl5oii2nVFnkCTd3xWOg5tg1O0VbVBcXEcx0MTHw8HQ6HtJSW"
    "P8hlnRfH/W63mEeyBvDT553RxSXDPufu9fXYZt7hZGqN0zZjHkPQFNlJq8pzWUcwAtR2uSTAuMwRnFdFud/uq6JEVNjdQVJn"
    "h+Ni+fSwmKfcBvC7z73RcMyIx5gzHo8t6iQnpSEqTeWHHiYmjU9NlXNGOCVate+/+ZpT1g96DhPpKd1tdggRv3sOnbO4zPfR"
    "43z+dr2Ke10Ax33r5atXo9HIIohSirDK8n1RVOPhs8ANETZRtI32awiMEJQx9vo/X3mePxyMCbGi7bHIK0odhNyicqXG0Xax"
    "3Lxp2/3lBMAzD96++nhyNeK25bmOZbV5vsjTbDJ4IVjXqHa9Wx5PESEoCF0hxHq9IsQKg75l0eMhyfLSKFS3+LBHALDjaR9t"
    "30pzuJ4CGHJw+/L2cjwS3BkEQRgwA06qagQeEiyqsliu56d4zxjxzzzH4UkWa61dt2NZ1mEfH+KkLsq0aBtDEeZZXG03j0pG"
    "kysAOxS8ePHh8OKcE/virHt12et1saC8ziwLiaLINtEqy06EEtfjFiUPDw8AwSAIELGXy9V6va3KplRtcN6FxIn3cjV/Sk+r"
    "Xs9Ah4BXr75zeX6OtOo6zuQiuLzgvaCjW0Kpo7XK87xVDeXMtm1lzN+/+JI5Yji8xMS+e3s/e5wraaADeZ9im8UbPLtbrJ/m"
    "ggPY9cEnH39vMhqApnaA6XlW4MmOQMhIzrllWcAgQqkjPER406q//u0LLvzxZGoz8e7u4f39o9YAuRAHymIiidj718v5+wcb"
    "S3gzPbu9vR31fNCUHkEhA9zOKYllvWbUIGhLDSnzhNsFUFSVfv3NHGF2fnHT8fur9Wn28FSWtSIShoqLMD+K+zfrp/czggv4"
    "4uVkOp2GHY7aqi+cC586dk7xQTf3EBVNbcpKQiQ4D43heQXjpClrEITD/uAqSevFcnM8xmmTGw8Kr1fFzsPb1Xx2ZyEJz0c+"
    "59wTzLeph0nAYegZX5TDXuk6irOOATgvQFaoukFas1NSVw3gooOJE6cZRERKPVsuG2pnucmPOI6SPN+dBwB2QkCwzYhlGcgM"
    "FjYKXRSIOhSZhYEQNAwvwu6Qso7SVtnofZTEeUFsii1bGc0crrV6XEdf36+SVJcJUHXB7fLDDywY+EAroFuga2ABYAPg2sCx"
    "QSgAxcB12eD8cnh5c9YbUCYMwsvlMilSgAx1LEwMJCBN48fl5l9fzasCQAW7AZ9Oxfc/Df8LLWLzsUOUdakAAAAASUVORK5C"
    "YII="
)


def _icon_template():
    try:
        import base64
        raw = base64.b64decode("".join(MARK_ICON_B64))
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


def build_marker_mask(img):
    """Маска маркеров на chest_map: 255 — маркер (останется непрозрачным), 0 — фон карты.
    Ищем по цвету картинки; нужен opencv. Нет opencv -> None (прозрачна вся карта)."""
    if cv2 is None or np is None or Image is None:
        return None
    try:
        bgr = np.ascontiguousarray(np.asarray(img.convert("RGB"))[:, :, ::-1])
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hh, ss, vv = (hsv[..., i].astype(np.int16) for i in range(3))
        yel = ((hh >= MARK_YEL[0]) & (hh <= MARK_YEL[1])
               & (ss > MARK_YEL[2]) & (vv > MARK_YEL[3]))
        red = (((hh <= MARK_RED[0]) | (hh >= MARK_RED[1]))
               & (ss > MARK_RED[2]) & (vv > MARK_RED[3]))
        org = ((hh >= MARK_ORG[0]) & (hh <= MARK_ORG[1])
               & (ss > MARK_ORG[2]) & (vv > MARK_ORG[3]))

        def ell(n):
            return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))

        seed = cv2.morphologyEx((yel | red).astype(np.uint8) * 255, cv2.MORPH_CLOSE, ell(9))
        dark = cv2.morphologyEx(((vv < MARK_DARK_V) | org).astype(np.uint8) * 255,
                                cv2.MORPH_OPEN, ell(5))
        n, lab, stats, _c = cv2.connectedComponentsWithStats(dark, connectivity=8)
        keep = np.zeros_like(seed)
        lo, hi = MARK_SIZE
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in stats[i])
            if (lo <= w <= hi and lo <= h <= hi and 0.6 <= w / h <= 1.7
                    and a >= 0.45 * w * h and a >= 150):
                sl = (slice(y, y + h), slice(x, x + w))
                keep[sl][lab[sl] == i] = 255
        # цифры без обводки: подложка вокруг цифры (иконка внутри не тёмная, блоб рвётся)
        dig = cv2.morphologyEx(((yel | red | org).astype(np.uint8)) * 255, cv2.MORPH_CLOSE, ell(3))
        n2, _l2, st2, _c2 = cv2.connectedComponentsWithStats(dig, connectivity=8)
        dk = vv < 60
        plates = np.zeros_like(seed)
        ih, iw = seed.shape
        for i in range(1, n2):
            x, y, w, h, a = (int(v) for v in st2[i])
            if not (6 <= w <= 30 and 8 <= h <= 26 and a >= 30):
                continue
            side = int(round(MARK_PLATE * max(w, h)))
            x0, y0 = max(0, x + w // 2 - side // 2), max(0, y + h // 2 - side // 2)
            x1, y1 = min(iw, x0 + side), min(ih, y0 + side)
            if dk[y0:y1, x0:x1].mean() >= MARK_PLATE_DARK:
                r = int(round(0.58 * side))     # подложка круглая, как у маркера
                cv2.ellipse(plates, ((x0 + x1) // 2, (y0 + y1) // 2), (r, r), 0, 0, 360, 255, -1)
        # сундуки без жёлтой обводки: тёмное кольцо вокруг светлой иконки — заливаем целиком
        dk8 = (vv < MARK_DARK_V).astype(np.uint8) * 255
        dk8 = cv2.morphologyEx(dk8, cv2.MORPH_CLOSE, ell(5))
        rc, _hh = cv2.findContours(dk8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rings = np.zeros_like(seed)
        for c in rc:
            x, y, w, h = cv2.boundingRect(c)
            if not (MARK_RING[0] <= w <= MARK_RING[1] and MARK_RING[0] <= h <= MARK_RING[1]
                    and 0.7 <= w / float(h) <= 1.45):
                continue
            area = cv2.contourArea(c)
            if area < 0.6 * w * h:
                continue
            tmp = np.zeros((h, w), np.uint8)
            cv2.drawContours(tmp, [c], -1, 255, -1, offset=(-x, -y))
            filled = int((tmp > 0).sum())
            dark_in = int(((tmp > 0) & (dk8[y:y + h, x:x + w] > 0)).sum())
            if filled and MARK_RING_FILL[0] <= dark_in / float(filled) <= MARK_RING_FILL[1]:
                cv2.drawContours(rings, [c], -1, 255, -1)
        # иконки сундуков по шаблону: ловит маркеры, у которых рамка слиплась с соседним
        icons = np.zeros_like(seed)
        tpl = _icon_template()
        if tpl is not None and bgr.shape[0] > tpl.shape[0] and bgr.shape[1] > tpl.shape[1]:
            res = cv2.matchTemplate(bgr, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= MARK_ICON_THR)
            th, tw = tpl.shape[:2]
            taken = []
            for i in np.argsort(-res[ys, xs]):
                cx, cy = int(xs[i]) + tw // 2, int(ys[i]) + th // 2
                if any((cx - px) ** 2 + (cy - py) ** 2 <= 400 for px, py in taken):
                    continue
                reg = vv[max(0, cy - 22):cy + 22, max(0, cx - 22):cx + 22]
                if (reg < 60).mean() >= MARK_ICON_DARK:
                    taken.append((cx, cy))
                    cv2.circle(icons, (cx, cy), MARK_ICON_R, 255, -1)
        m = cv2.morphologyEx(
            cv2.bitwise_or(cv2.bitwise_or(cv2.bitwise_or(cv2.bitwise_or(seed, keep), plates), rings), icons),
            cv2.MORPH_CLOSE, ell(7))
        cnts, _h = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = np.zeros_like(m)
        for c in cnts:
            if cv2.contourArea(c) >= MARK_MIN_AREA:
                cv2.drawContours(out, [c], -1, 255, -1)
        out = cv2.dilate(out, ell(3))
        return Image.fromarray(out, "L")
    except Exception as e:  # noqa: BLE001
        _log(f"ОШИБКА маски маркеров: {e!r}")
        return None


# --- Окно с попиксельной прозрачностью (UpdateLayeredWindow): фон карты — map_alpha,
# --- маркеры — 255. Tk -alpha на этом окне не вызываем: после него UpdateLayeredWindow не работает.
class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


_ULW_READY = False


def _ulw_setup():
    global _ULW_READY
    if _ULW_READY:
        return
    vp = ctypes.c_void_p
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = vp
    user32.ReleaseDC.argtypes = [wintypes.HWND, vp]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, vp, vp, vp, vp, vp,
                                           wintypes.DWORD, vp, wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [vp]
    gdi32.CreateCompatibleDC.restype = vp
    gdi32.CreateDIBSection.argtypes = [vp, vp, wintypes.UINT, vp, vp, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = vp
    gdi32.SelectObject.argtypes = [vp, vp]
    gdi32.SelectObject.restype = vp
    gdi32.DeleteObject.argtypes = [vp]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [vp]
    gdi32.DeleteDC.restype = wintypes.BOOL
    _ULW_READY = True


def present_layered(hwnd, rgba, alpha=255):
    """Показать RGBA-картинку (PIL) в окне hwnd с попиксельной прозрачностью.
    alpha 0..255 — общая прозрачность поверх попиксельной (для плавного появления)."""
    _ulw_setup()
    w, h = rgba.size
    r, g, b, a = rgba.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))     # premultiplied alpha
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()           # байты B, G, R, A
    bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    scr = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(scr)
    bmp = gdi32.CreateDIBSection(scr, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    try:
        if not bmp or not bits.value:
            raise ctypes.WinError()
        old = gdi32.SelectObject(mem, bmp)
        ctypes.memmove(bits.value, data, len(data))
        size, origin = wintypes.SIZE(w, h), wintypes.POINT(0, 0)
        blend = _BLEND(0, 0, max(0, min(255, int(alpha))), 1)     # AC_SRC_OVER, AC_SRC_ALPHA
        ok = user32.UpdateLayeredWindow(hwnd, scr, None, ctypes.byref(size), mem,
                                        ctypes.byref(origin), 0, ctypes.byref(blend), 2)
        gdi32.SelectObject(mem, old)
        if not ok:
            raise ctypes.WinError()
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, scr)


def ref_to_chest(x, y):
    a, b, c, d, e, f = REF_TO_CHEST
    return a * x + b * y + c, d * x + e * y + f


def find_file(names):
    """Ищем: папка настроек -> рядом с программой -> внутри exe (PyInstaller).
    Файл рядом с exe или в папке настроек важнее вшитого, так можно подменить карту."""
    dirs = [APP_DIR]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        if getattr(sys, "_MEIPASS", None):
            dirs.append(sys._MEIPASS)
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


def find_map_file():
    return find_file(MAP_NAMES)


class Locator:
    """Где игрок на карте: сопоставляем вырез миникарты с чистой картой (SIFT + RANSAC).
    Возвращает координаты центра выреза (там стрелка игрока) в пикселях чистой карты."""

    def __init__(self, ref_path):
        if cv2 is None or not hasattr(cv2, "SIFT_create"):
            raise RuntimeError("Нужен opencv-python 4.4+ (cv2.SIFT_create)")
        ref = _decode(ref_path)
        if ref is None:
            raise RuntimeError("Не открылась " + ref_path)
        sift = cv2.SIFT_create()
        kp, self.desc = sift.detectAndCompute(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), None)
        if self.desc is None or len(kp) < 50:
            raise RuntimeError("На чистой карте мало деталей")
        self.pts = np.float32([k.pt for k in kp])
        self.sift = cv2.SIFT_create(nfeatures=700)
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.last = None

    def locate(self, bgr):
        if bgr.shape[1] != MINIMAP_NORM or bgr.shape[0] != MINIMAP_NORM:
            bgr = cv2.resize(bgr, (MINIMAP_NORM, MINIMAP_NORM), interpolation=cv2.INTER_AREA)
        kp, d = self.sift.detectAndCompute(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), None)
        if d is None or len(kp) < TRACK_MIN_INLIERS:
            return None
        q = np.float32([k.pt for k in kp])
        idx = None
        if self.last is not None:
            near = np.where(np.hypot(self.pts[:, 0] - self.last[0],
                                     self.pts[:, 1] - self.last[1]) < TRACK_LOCAL_R)[0]
            if len(near) >= 40:
                idx = near
        for sub in ((idx, None) if idx is not None else (None,)):
            r = self._match(q, d, sub)
            if r is not None:
                self.last = r
                return r
        self.last = None
        return None

    def _match(self, q, d, idx):
        pts = self.pts if idx is None else self.pts[idx]
        des = self.desc if idx is None else self.desc[idx]
        if len(pts) < 2:
            return None
        good = [m[0] for m in self.bf.knnMatch(d, des, k=2)
                if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
        if len(good) < TRACK_MIN_INLIERS:
            return None
        src = q[[g.queryIdx for g in good]].reshape(-1, 1, 2)
        dst = pts[[g.trainIdx for g in good]].reshape(-1, 1, 2)
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                             ransacReprojThreshold=3.0)
        if M is None or inl is None or int(inl.sum()) < TRACK_MIN_INLIERS:
            return None
        s = math.hypot(M[0, 0], M[1, 0])
        ang = math.atan2(M[1, 0], M[0, 0])
        # миникарта «север сверху» (не вращающаяся): поворот ~0, масштаб постоянный
        if not (TRACK_SCALE[0] <= s <= TRACK_SCALE[1]) or abs(ang) > 0.25:
            return None
        c = M @ np.array([MINIMAP_NORM / 2.0, MINIMAP_NORM / 2.0, 1.0])
        return float(c[0]), float(c[1])


def make_region_grabber(frac):
    """Снимок только области миникарты основного монитора (BGR), быстро."""
    if _mss is not None:
        sct = _mss.mss()
        mon = sct.monitors[1]
        reg = {"left": mon["left"] + int(frac[0] * mon["width"]),
               "top": mon["top"] + int(frac[2] * mon["height"]),
               "width": max(16, int((frac[1] - frac[0]) * mon["width"])),
               "height": max(16, int((frac[3] - frac[2]) * mon["height"]))}

        def grab():
            shot = sct.grab(reg)
            bgra = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
            return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        return grab
    from PIL import ImageGrab
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    box = (int(frac[0] * sw), int(frac[2] * sh), int(frac[1] * sw), int(frac[3] * sh))

    def grab():
        rgb = np.array(ImageGrab.grab(bbox=box).convert("RGB"))
        return np.ascontiguousarray(rgb[:, :, ::-1])
    return grab


def crop_region(bgr, frac=MINIMAP_REGION):
    h, w = bgr.shape[:2]
    return bgr[int(frac[2] * h):int(frac[3] * h), int(frac[0] * w):int(frac[1] * w)]


def track_worker(q, stop_evt, enabled_evt):
    """Поток: читает миникарту с экрана и шлёт ('pos', (x, y)) в координатах чистой карты
    или ('pos', None), когда сигнал потерян (открыта большая карта, меню, лобби)."""
    loc = grab = None
    last_ok = 0.0
    lost_sent = True
    last_err = -1e9
    period = 1.0 / TRACK_HZ
    while not stop_evt.is_set():
        if not enabled_evt.is_set():
            time.sleep(0.25)
            continue
        if loc is None:
            try:
                ref = find_file(REF_NAMES)
                if ref is None:
                    raise RuntimeError("Нет map_ref.png (чистая карта) рядом со скриптом")
                loc = Locator(ref)
                grab = make_region_grabber(MINIMAP_REGION)
            except Exception as e:  # noqa: BLE001
                enabled_evt.clear()
                q.put(("track_error", str(e)))
                continue
        t0 = time.monotonic()
        try:
            pos = loc.locate(grab())
        except Exception as e:  # noqa: BLE001
            if time.monotonic() - last_err > 30:
                last_err = time.monotonic()
                _log(f"ОШИБКА трекинга: {e!r}")
            time.sleep(0.5)
            continue
        now = time.monotonic()
        if pos is not None:
            last_ok, lost_sent = now, False
            q.put(("pos", pos))
        elif not lost_sent and now - last_ok > TRACK_LOST_SEC:
            lost_sent = True
            q.put(("pos", None))
        time.sleep(max(0.0, period - (time.monotonic() - t0)))


class MapWindow:
    """Карта сундуков поверх игры. Два режима:
    «радар» — окно-квадрат, карта едет за игроком с тем же приближением, что у миникарты игры;
    «вся карта» — карта целиком, на ней точка-стрелка игрока.
    Тащить мышью, колесо — приближение (радар) / размер (вся карта), двойной клик — смена режима.
    «Закрепить» в меню — клики проходят сквозь окно."""

    def __init__(self, app):
        self.app = app
        self.win = None
        self.canvas = None
        self.hwnd = None
        self.region = None
        self.src = None
        self.err = None
        self.tkimg = None
        self.visible = False
        self._job = None
        self._items = {}
        self.mark = None                # маска маркеров chest_map (L, 255 = маркер)
        self._full = None               # режим «вся карта»: (картинка, маска) уже нужного размера
        self._size = None
        self._layered = False
        self._ulw_err = -1e9
        self._fa = 0.0                  # общая прозрачность окна: плавное появление / исчезновение
        self._fanim = None
        self._last = None               # последний собранный кадр (для анимации без пересборки)
        self._font = None
        self.pos = None                 # сглаженная позиция игрока, пиксели chest_map
        self.lost = False
        self.ang = -math.pi / 2         # куда смотрит стрелка (по направлению движения)
        self._hd = None

    # ---------- служебное ----------
    def _alive(self):
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _tracking(self):
        st = self.app.state
        return AUTO_OK and st["minimap"] and st["map_track"]

    def _load(self):
        self.src = self.tkimg = self.mark = None
        if MAP_ERR:
            self.err = MAP_ERR
            return
        p = find_map_file()
        if p is None:
            self.err = "Положи карту как chest_map.png в папку:\n" + APP_DIR
            return
        try:
            self.src = Image.open(p).convert("RGB")
            self.mark = build_marker_mask(self.src)
            self.err = None
        except Exception as e:  # noqa: BLE001
            self.err = f"Не открылась картинка: {e!r}"

    def _ensure(self):
        if self._alive():
            return
        st = self.app.state
        w = tk.Toplevel(self.app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=T["bg"])
        self.win = w
        self.hwnd = None
        self._layered = False
        self.canvas = tk.Canvas(w, bg=T["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.region = make_rounded(w, lambda: min(14, int(self.app.state["radius"])))

        def moved(x, y):
            st["map_x"], st["map_y"] = x, y

        bind_drag(self.canvas, w, on_move=moved, on_end=lambda: save_state(st))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Double-Button-1>", self._flip_mode)
        if self.src is None and self.err is None:
            self._load()

    def _flip_mode(self, _e=None):
        self.app.apply_setting(
            "map_mode", "full" if self.app.state["map_mode"] == "radar" else "radar")

    def _wheel(self, e):
        st = self.app.state
        up = e.delta > 0
        if st["map_mode"] == "radar":
            z = st["map_zoom"] * (1.1 if up else 1 / 1.1)
            self.app.apply_setting("map_zoom", _clamp(z, MAP_ZOOM_MIN, MAP_ZOOM_MAX))
        else:
            w = st["map_w"] * (1.06 if up else 1 / 1.06)
            self.app.apply_setting("map_w", _clamp(w, MAP_W_MIN, MAP_W_MAX))

    def _soon(self):
        if self._job is None and self._alive():
            self._job = self.win.after(30, self._render)

    def _pos(self, w, h):
        st = self.app.state
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = st["map_x"], st["map_y"]
        if x < 0 or y < 0 or x > sw - 60 or y > sh - 60:
            x, y = max(0, sw - w - 20), 320
            st["map_x"], st["map_y"] = x, y
        return x, y

    @staticmethod
    def _arrow(cx, cy, ang, r):
        pts = []
        for da, rr in ((0.0, 1.3 * r), (2.5, r), (math.pi, 0.35 * r), (-2.5, r)):
            a = ang + da
            pts += [cx + rr * math.cos(a), cy + rr * math.sin(a)]
        return pts

    # ---------- отрисовка ----------
    def _render(self):
        """Полная перерисовка (режим / размер). Позиция игрока двигается лёгкой _update()."""
        self._job = None
        if not self._alive():
            return
        st = self.app.state
        radar = st["map_mode"] == "radar"
        if radar:
            w = h = int(st["map_r"])
        else:
            w = int(st["map_w"])
            h = (max(1, int(round(w * self.src.height / float(self.src.width))))
                 if self.src is not None else max(80, int(w * 0.3)))
        self._size = (w, h)
        self._full = None
        if self.src is not None and not radar:
            self._full = (self.src.resize((w, h), _RS.LANCZOS),
                          self.mark.resize((w, h), _RS.LANCZOS) if self.mark is not None
                          else None)
        self.canvas.config(width=w, height=h)
        x, y = self._pos(w, h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.update_idletasks()
        self._update()
        if self.region:
            self.region.reset()
            self.region()

    def _get_font(self, size):
        try:
            return ImageFont.truetype("segoeuib.ttf", size)
        except Exception:  # noqa: BLE001
            return ImageFont.load_default()

    def _bg_alpha(self, mk, size):
        """Альфа-канал: фон = map_alpha, маркеры (по маске) = 100%."""
        bg = int(round(_clamp(self.app.state["map_alpha"], 0.3, 1.0) * 255))
        if mk is None:
            return Image.new("L", size, bg)
        lut = [bg + (255 - bg) * v // 255 for v in range(256)]
        return mk.point(lut)

    def _compose(self):
        """Кадр карты в RGBA: картинка + стрелка игрока + рамка + подпись."""
        st = self.app.state
        w, h = self._size
        if self.src is None:
            img = Image.new("RGBA", (w, h), T["bg"])
            d = ImageDraw.Draw(img)
            import textwrap
            lines = textwrap.wrap((self.err or "").replace("\n", " "), max(10, (w - 24) // 7))
            d.multiline_text((12, 12), "\n".join(lines), fill=ALERT, font=self._get_font(12),
                             spacing=4)
            return img
        radar = st["map_mode"] == "radar"
        if radar:
            side = int(st["map_r"])
            sw, sh = self.src.size
            px, py = self.pos if self.pos else (sw / 2.0, sh / 2.0)
            half = RADAR_SPAN / max(0.1, st["map_zoom"]) / 2.0
            box = (px - half, py - half, px + half, py + half)
            rgb = self.src.transform((side, side), _TF.EXTENT, box, _RS.BILINEAR)
            mk = (self.mark.transform((side, side), _TF.EXTENT, box, _RS.BILINEAR)
                  if self.mark is not None else None)
        else:
            rgb, mk = self._full
        img = rgb.convert("RGBA")
        img.putalpha(self._bg_alpha(mk, img.size))
        d = ImageDraw.Draw(img)
        if self.pos is not None:
            if radar:
                pts = self._arrow(w / 2.0, h / 2.0, self.ang, max(7.0, w * 0.045))
            else:
                k = int(st["map_w"]) / float(self.src.width)
                pts = self._arrow(self.pos[0] * k, self.pos[1] * k, self.ang, 11.0)
            xy = [(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)]
            d.polygon(xy, fill="#ffffff")
            d.line(xy + [xy[0]], fill="#000000", width=2)
        d.rectangle([0, 0, w - 1, h - 1], width=2,
                    outline=ALERT if (self.lost and self.pos) else T["open"])
        if not self._tracking():
            txt = ""
        elif self.pos is None:
            txt = "поиск игрока…"
        elif self.lost:
            txt = "сигнал потерян"
        else:
            txt = ""
        if txt:
            if self._font is None:
                self._font = self._get_font(12)
            try:
                d.text((8, h - 8), txt, fill=AMBER, font=self._font, anchor="ls")
            except Exception:  # noqa: BLE001
                d.text((8, h - 22), txt, fill=AMBER, font=self._font)
        return img

    def _present(self, img):
        if not self._alive():
            return
        if not self.hwnd or not self._layered:
            self._style()                       # ставит WS_EX_LAYERED и находит hwnd
            self._layered = bool(self.hwnd)
        if not self.hwnd:
            return
        try:
            present_layered(self.hwnd, img, int(round(255 * self._fa)))
        except Exception as e:  # noqa: BLE001
            if time.monotonic() - self._ulw_err > 30:
                self._ulw_err = time.monotonic()
                _log(f"ОШИБКА окна карты (UpdateLayeredWindow): {e!r}")

    def _update(self):
        """Быстрое обновление под новую позицию (~20 раз в секунду)."""
        if self._size is None or not self._alive():
            return
        if not self.visible and self._fanim is None:
            return
        self._last = self._compose()
        self._present(self._last)

    # ---------- позиция игрока ----------
    def set_pos(self, p):
        """p — (x, y) на чистой карте или None (сигнал потерян)."""
        if p is None:
            if self.pos is not None:
                self.lost = True
        else:
            x, y = ref_to_chest(*p)
            if self.pos is None or self.lost:
                self.pos = (x, y)
                self._hd = (x, y)
            else:
                self.pos = (self.pos[0] + POS_SMOOTH * (x - self.pos[0]),
                            self.pos[1] + POS_SMOOTH * (y - self.pos[1]))
            self.lost = False
            dx, dy = self.pos[0] - self._hd[0], self.pos[1] - self._hd[1]
            if dx * dx + dy * dy > HEAD_MIN_MOVE ** 2:
                a = math.atan2(dy, dx)
                self.ang += 0.5 * ((a - self.ang + math.pi) % (2 * math.pi) - math.pi)
                self._hd = self.pos
        if self.visible:
            self._update()

    def clear_pos(self):
        self.pos = self._hd = None
        self.lost = False
        if self.visible:
            self._update()

    # ---------- окно ----------
    def _aff(self):
        a = self.app._affinity(True)
        # пока идёт трекинг, окно не должно попадать в захват — иначе оно читает само себя
        if a == 0 and self._tracking():
            a = 0x11
        return a

    def apply_affinity(self):
        if not self.hwnd:
            return
        try:
            user32.SetWindowDisplayAffinity(self.hwnd, self._aff())
        except Exception:  # noqa: BLE001
            pass

    def _style(self):
        """Режим «клики насквозь» и скрытие от захвата."""
        try:
            if self.hwnd is None:
                self.win.update_idletasks()
                self.hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            self.apply_affinity()
            ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) | WS_EX_LAYERED
            if self.app.state["map_lock"]:
                ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)
        except Exception:  # noqa: BLE001
            pass

    def _fade(self, target, ms, done=None):
        """Плавно меняем общую прозрачность окна (как у панели счётчика)."""
        if self._fanim:
            self._fanim["on"] = False
        a0 = self._fa

        def step(e):
            self._fa = a0 + (target - a0) * e
            if self._last is not None:
                self._present(self._last)

        def finish():
            self._fa = target
            self._fanim = None
            if self._last is not None:
                self._present(self._last)
            if done:
                done()

        self._fanim = animate(self.win, ms, step, done=finish)

    def show(self):
        if self.src is None:
            self._load()
        self._ensure()
        self.visible = True
        self._render()                  # рисуем с текущей прозрачностью (0 при первом показе)
        self.win.deiconify()
        self.win.lift()
        self._style()
        self._update()
        self._fade(1.0, 220)

    def hide(self):
        self.visible = False
        if not self._alive():
            return

        def gone():
            self._fa = 0.0
            if self._alive() and not self.visible:
                self.win.withdraw()

        self._fade(0.0, 160, done=gone)

    def refresh(self, key):
        """Сменилась настройка карты."""
        if not self._alive():
            return
        if key == "map_lock":
            self._style()
        elif key == "map_alpha":
            self._update()
        elif key == "map_zoom":
            self._update()
        elif key == "map_track":
            self._style()
            self._update()
        else:                           # map_mode, map_w, map_r
            self._soon()

    def reload(self):
        """Перечитать картинку с диска."""
        self._load()
        if self._alive():
            self._render()

    def reset_pos(self):
        self.app.state["map_x"] = self.app.state["map_y"] = -1
        save_state(self.app.state)
        if self._alive():
            self._render()

    def restyle(self):
        if self._alive():
            self.win.configure(bg=T["bg"])
            self.canvas.config(bg=T["bg"])
            self._soon()

    def keep_on_top(self):
        if self.visible and self._alive():
            self.win.attributes("-topmost", True)

    def destroy(self):
        if self._fanim:
            self._fanim["on"] = False
        if self._alive():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None


# ======================= Главная панель =======================
class ObsWindow:
    """Отдельное обычное окно со счётчиком — для захвата в OBS («Захват окна»).

    Панель поверх игры для OBS неудобна: у неё нет рамки, она полупрозрачная,
    со скруглёнными углами и может пропускать клики — OBS показывает такое окно
    с чёрными углами, мерцающим или вообще не находит. Это окно самое обычное:
    с заголовком, непрозрачное, прямоугольное, не скрыто от захвата. Размер можно
    менять — число растягивается вместе с окном. Надпись намеренно НЕ «СУНДУК»,
    чтобы автоподсчёт не принял окно за плашку из игры."""

    TITLE = "Fortnite Counter (OBS)"
    DEFAULT_GEO = "300x170+80+80"

    def __init__(self, app):
        self.app = app
        self.win = None
        self.num = None
        self.cap = None
        self._sizes = None

    def alive(self):
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _safe_geo(self):
        """Сохранённое положение; если оно ушло за экран (отключили монитор) — угол."""
        geo = self.app.state.get("obs_geo") or self.DEFAULT_GEO
        try:
            m = re.match(r"^(\d+)x(\d+)[+-](-?\d+)[+-](-?\d+)$", geo)
            vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            if m and vw > 0 and vh > 0:
                x, y = int(m.group(3)), int(m.group(4))
                if not (vx - 150 < x < vx + vw - 60 and vy - 10 < y < vy + vh - 40):
                    return self.DEFAULT_GEO
        except Exception:  # noqa: BLE001
            return self.DEFAULT_GEO
        return geo

    def _build(self):
        w = tk.Toplevel(self.app.root)
        w.title(self.TITLE)
        w.configure(bg=T["bg"])
        w.minsize(140, 90)
        w.geometry(self._safe_geo())
        self.num = tk.Label(w, text=str(self.app.state["open"]), bg=T["bg"], fg=kcolor(),
                            font=(FONT, -60, "bold"))
        self.num.place(relx=0.5, rely=0.44, anchor="center")
        self.cap = tk.Label(w, text="ОТКРЫТО", bg=T["bg"], fg=T["dim"],
                            font=(FONT, -14, "bold"))
        self.cap.place(relx=0.5, rely=0.84, anchor="center")
        self._sizes = None
        w.bind("<Configure>", self._on_configure)
        w.protocol("WM_DELETE_WINDOW", self._user_closed)
        self.win = w

    def _layout(self, w, h):
        """Размер шрифта по размеру окна (в пикселях: отрицательный размер у Tk)."""
        if w < 20 or h < 20:
            return
        n = max(1, len(self.num.cget("text")))
        px = max(18, int(min(h * 0.50, w * 0.92 / (0.62 * n))))
        cpx = max(9, min(int(h * 0.11), 28))
        if (px, cpx) == self._sizes:
            return
        self._sizes = (px, cpx)
        self.num.config(font=(FONT, -px, "bold"))
        self.cap.config(font=(FONT, -cpx, "bold"))

    def _on_configure(self, e):
        if e.widget is not self.win:
            return
        self._layout(e.width, e.height)
        if e.width >= 100 and e.height >= 60:        # не запоминаем размер «1x1» при создании
            try:
                self.app.state["obs_geo"] = self.win.geometry()
                self.app._save_soon()
            except tk.TclError:
                pass

    def _user_closed(self):
        self.app.set_obs(False)          # закрыли крестиком — выключаем и в меню

    def show(self):
        if not self.alive():
            self._build()
        self.win.deiconify()
        self.win.lift()
        self.update()

    def hide(self):
        if self.alive():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = self.num = self.cap = None

    def update(self):
        """Число изменилось — обновить окно."""
        if not self.alive():
            return
        txt = str(self.app.state["open"])
        if self.num.cget("text") != txt:
            self.num.config(text=txt)
        self._layout(self.win.winfo_width(), self.win.winfo_height())

    def restyle(self):
        """Тема или цвет сменились."""
        if not self.alive():
            return
        self.win.configure(bg=T["bg"])
        self.num.config(bg=T["bg"], fg=kcolor())
        self.cap.config(bg=T["bg"], fg=T["dim"])


class CounterApp:
    def __init__(self):
        self.ok = False
        self.ready = False
        self.state = load_state()
        apply_theme(self.state)
        self.scale = self.state["scale"]
        self.pinned = False
        self.menu = None
        self._panel_a = 1.0          # текущая «видимость» панели 0..1 (для плавного вкл/выкл)
        self._panel_anim = None
        self._fading = False
        self.hwnd = None
        self.history = []
        self.last_count_t = 0.0
        self.reset_armed_until = 0.0
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self._flash_job = None
        self._rebuild_job = None
        self._save_job = None
        self.content = None
        self.nums = {}
        self.entries = {}
        self.count_entry = None       # поле «сколько открыто» в меню (обновляется само)
        self.settings_win = None
        # изменение размера
        self._ghost = None
        self._ghost_lbl = None
        self._gnew = None
        # горячие клавиши
        self.capturing = False
        self.capture_conflict = None
        self.hk_cmd_q = None
        self._hk_token = 0
        self._hk_pending = {}
        # автоподсчёт
        self.auto_evt = threading.Event()
        # уведомление «+1 сундук»
        self.notifier = NotifyToast(self)
        self.obs = ObsWindow(self)
        self.mapwin = MapWindow(self)
        self.track_evt = threading.Event()      # слежение за игроком по миникарте

        self.root = tk.Tk()
        self.root.withdraw()

        # Стартового окна нет: сразу панель; меню — по Insert / Delete
        self.ok = True
        save_state(self.state)
        self.scale = self.state["scale"]

        # 2) Панель поверх игры
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=T["bg"])
        self.region = make_rounded(self.root, lambda: self.px(self.state["radius"]))

        self.grip = tk.Label(self.root, text="◢", bg=T["bg"], fg=T["dim"],
                             cursor="size_nw_se", font=(FONT, 9))
        self.grip.bind("<ButtonPress-1>", self._grip_start)
        self.grip.bind("<B1-Motion>", self._grip_move)
        self.grip.bind("<ButtonRelease-1>", self._grip_end)

        self._build_ui()
        self._apply_alpha()
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.apply_capture_mode()
        self.ready = True
        self._fit_window()

        if self.state["pinned"]:
            self.set_pinned(True)
        else:
            self._place_grip(True)
        self.root.after(1000, self._pin_watchdog)
        if not self.state["panel"]:
            self.root.withdraw()
            self._panel_a = 0.0

        self.hk_cmd_q = queue.Queue()
        threading.Thread(
            target=hotkey_worker,
            args=(self.q, self.stop_evt, self.hk_cmd_q, build_hotkeys(self.state)),
            daemon=True).start()
        self.replay_evt = threading.Event()
        threading.Thread(target=replay_worker,
                         args=(self.q, self.stop_evt, self.replay_evt,
                               set(self.state["replays_done"])),
                         daemon=True).start()
        if AUTO_OK:
            threading.Thread(target=auto_worker,
                             args=(self.q, self.stop_evt, self.auto_evt),
                             daemon=True).start()
            threading.Thread(target=track_worker,
                             args=(self.q, self.stop_evt, self.track_evt),
                             daemon=True).start()
        elif self.state["auto_mode"] == "game":
            self.state["auto"] = False
        self._auto_apply()
        self.root.after(50, self._poll)
        self.root.after(2000, self._keep_on_top)
        self.root.after(400, self._prebuild_menu)
        if OBS_FLAG and not self.state["obs_window"]:
            self.state["obs_window"] = True
            save_state(self.state)
        if self.state["obs_window"]:
            self.root.after(700, self.obs.show)
        if self.state["minimap"]:
            self.root.after(900, self.mapwin.show)
            self.root.after(950, self._sync_track)

    # ---------- Масштаб ----------
    def px(self, v):
        return max(0, int(round(v * self.scale)))

    def fs(self, size):
        return max(6, int(round(size * self.scale)))

    # ---------- Сохранение ----------
    def _save_soon(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self._save_now)

    def _save_now(self):
        self._save_job = None
        save_state(self.state)

    # ---------- UI ----------
    def hint(self):
        if self.pinned:
            return "📌 Закреплено · Ctrl+Alt+P"
        if self.state["auto"]:
            return "🤖 Авто вкл · Ctrl+Alt+A"
        return "Insert — меню"

    def _refresh_hint(self):
        if self.ready and self._flash_job is None:
            self.status.config(text=self.hint(),
                               fg=AMBER if self.pinned else T["dim"])

    def _build_ui(self):
        old = self.content
        self.entries.clear()
        self.nums = {}
        px, fs = self.px, self.fs
        outer = tk.Frame(self.root, bg=T["bg"])

        # Шапка
        head = tk.Frame(outer, bg=T["bg"])
        head.pack(fill="x", padx=px(14), pady=(px(12), px(4)))
        logo_badge(head, px(24)).pack(side="left")
        tk.Label(head, text="Chest Counter", bg=T["bg"], fg=T["fg"],
                 font=(FONT, fs(10), "bold")).pack(side="left", padx=(px(8), 0))
        flat_button(head, "✕", self.quit, bg=T["bg"], fg=T["dim"], width=2,
                    font=(FONT, fs(9), "bold"), hover=ALERT).pack(side="right")
        flat_button(head, "⚙", self.toggle_menu, bg=T["bg"], fg=T["dim"], width=2,
                    font=(FONT, fs(10), "bold")).pack(side="right")
        self.pin_btn = flat_button(
            head, "📌", self.toggle_pin, bg=AMBER if self.pinned else T["bg"],
            fg="#1a1200" if self.pinned else T["dim"], width=2,
            font=("Segoe UI Emoji", fs(9)), hover=AMBER)
        self.pin_btn.pack(side="right")

        # Карточка открытых сундуков
        self._card(outer, "open", "ОТКРЫТЫЕ")

        # Подвал
        foot = tk.Frame(outer, bg=T["bg"])
        foot.pack(fill="x", padx=(px(14), px(26)), pady=(px(6), px(12)))
        flat_button(foot, "⟲", self.reset, width=2, font=(FONT, fs(10), "bold")
                    ).pack(side="right", padx=(px(4), 0))
        flat_button(foot, "↶", self.undo, width=2, font=(FONT, fs(10), "bold")
                    ).pack(side="right")
        self.status = tk.Label(foot, text=self.hint(), bg=T["bg"], anchor="w",
                               fg=AMBER if self.pinned else T["dim"], font=(FONT, fs(8)))
        self.status.pack(side="left")

        outer.pack(fill="both", expand=True)
        self.content = outer
        if old is not None:
            old.destroy()

        bind_drag(outer, self.root, on_move=self._on_move, on_end=self._drag_end)
        self._refresh()
        self.grip.lift()
        if self.ready:
            self._fit_window()

    def _card(self, parent, kind, title):
        px, fs = self.px, self.fs
        color = kcolor()
        card = tk.Frame(parent, bg=T["card"])
        card.pack(fill="x", padx=px(12), pady=px(4))
        chest_badge(card, px(46), _mix(T["card"], color, 0.22), color,
                    T["card"]).pack(side="left", padx=(px(12), 0), pady=px(8))

        body = tk.Frame(card, bg=T["card"])
        body.pack(side="left", padx=px(10), pady=px(7))
        tk.Label(body, text=title, bg=T["card"], fg=T["dim"],
                 font=(FONT, fs(8), "bold")).grid(row=0, column=0, sticky="w")
        num = tk.Label(body, text="0", bg=T["card"], fg=color, width=4, anchor="w",
                       font=(FONT, fs(26), "bold"), cursor="xterm")
        num.grid(row=1, column=0, sticky="w")
        num.bind("<Double-Button-1>", lambda _e, k=kind: self._edit(k))
        self.nums[kind] = num

        btns = tk.Frame(card, bg=T["card"])
        btns.pack(side="right", padx=px(10))
        flat_button(btns, "−", lambda k=kind: self.minus(k), width=2,
                    font=(FONT, fs(11), "bold")).pack(side="left", padx=(0, px(6)),
                                                      ipady=px(3))
        flat_button(btns, "+", lambda k=kind: self.count(k), bg=color, fg=on_color(color),
                    width=3, font=(FONT, fs(14), "bold"), hover=color
                    ).pack(side="left", ipady=px(3))

    def _fit_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        self.root.geometry(f"{w}x{h}+{self.state['x']}+{self.state['y']}")
        self.root.update_idletasks()
        self.region()

    # ---------- Перетаскивание ----------
    def _on_move(self, x, y):
        self.state["x"], self.state["y"] = x, y

    def _drag_end(self):
        save_state(self.state)

    # ---------- Изменение размера ----------
    def _kill_ghost(self):
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except tk.TclError:
                pass
        self._ghost = None
        self._ghost_lbl = None

    def _grip_start(self, e):
        self._gx = e.x_root
        self._gscale = self.state["scale"]
        self._gnew = self._gscale
        self._gw = max(1, self.root.winfo_width())
        self._gh = max(1, self.root.winfo_height())
        self._gx0, self._gy0 = self.root.winfo_x(), self.root.winfo_y()
        self._kill_ghost()
        g = tk.Toplevel(self.root)
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        g.attributes("-alpha", 0.45)
        g.configure(bg=T["open"])
        lbl = tk.Label(g, text=f"{int(round(self._gscale * 100))}%", bg=T["open"],
                       fg=on_color(T["open"]), font=(FONT, 14, "bold"))
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        g.geometry(f"{self._gw}x{self._gh}+{self._gx0}+{self._gy0}")
        self._ghost, self._ghost_lbl = g, lbl

    def _grip_move(self, e):
        if self._ghost is None:
            return
        new = self._gscale * (self._gw + (e.x_root - self._gx)) / self._gw
        new = round(round(_clamp(new, SCALE_MIN, SCALE_MAX) / 0.05) * 0.05, 2)
        if new == self._gnew:
            return
        self._gnew = new
        k = new / self._gscale
        self._ghost.geometry(
            f"{max(40, int(self._gw * k))}x{max(40, int(self._gh * k))}"
            f"+{self._gx0}+{self._gy0}")
        self._ghost_lbl.config(text=f"{int(round(new * 100))}%")

    def _grip_end(self, _e):
        had_ghost = self._ghost is not None
        self._kill_ghost()
        if not had_ghost:
            return
        if self._gnew is not None and self._gnew != self.state["scale"]:
            self.state["scale"] = self._gnew
            if self._rebuild_job is not None:
                self.root.after_cancel(self._rebuild_job)
                self._rebuild_job = None
            self._do_rebuild()
        save_state(self.state)
        if self.settings_win and self.settings_win.alive():
            self.settings_win.restyle()

    def _schedule_rebuild(self, delay=150):
        if self._rebuild_job is not None:
            self.root.after_cancel(self._rebuild_job)
        self._rebuild_job = self.root.after(delay, self._do_rebuild)

    def _do_rebuild(self):
        self._rebuild_job = None
        self.scale = self.state["scale"]
        self._build_ui()

    def _place_grip(self, show):
        if show:
            self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-5, y=-3)
            self.grip.lift()
        else:
            self.grip.place_forget()

    # ---------- Настройки ----------
    def apply_setting(self, key, value):
        if key == "__reset__":
            for k in STYLE_KEYS:
                self.state[k] = DEFAULTS[k]
            apply_theme(self.state)
            save_state(self.state)
            if self.ready:
                self._restyle_panel()
            return
        if key in ("map_w", "map_r", "map_zoom", "map_alpha", "map_lock", "map_track",
                   "map_mode"):
            if key in ("map_lock", "map_track"):
                value = bool(value)
            elif key in ("map_w", "map_r"):
                value = int(value)
            elif key == "map_mode":
                value = "full" if value == "full" else "radar"
            else:
                value = round(float(value), 2)
            self.state[key] = value
            if self.ready:
                self._save_soon()
                if key == "map_track":
                    self._sync_track()
                self.mapwin.refresh(key)
            else:
                save_state(self.state)
            return
        if key in ("notify_sec", "notify_scale"):
            self.state[key] = round(float(value), 2)
            if self.ready:
                self._save_soon()
                if key == "notify_scale":
                    self.notifier.demo(delay=250)
            else:
                save_state(self.state)
            return
        if key in ("alpha", "scale"):
            value = round(float(value), 2)
        elif key in ("radius",):
            value = int(value)
        elif key == "cooldown":
            value = round(float(value), 1)
        self.state[key] = value
        if key in ("theme", "col_open"):
            apply_theme(self.state)
        if key in ("alpha", "scale", "radius", "cooldown"):
            if self.ready:
                self._save_soon()
        else:
            save_state(self.state)
        if not self.ready:
            return
        if key == "capture_visible":
            self.apply_capture_mode()
        elif key in ("theme", "col_open"):
            self._restyle_panel()
        elif key == "alpha":
            self._apply_alpha()
        elif key == "scale":
            self._schedule_rebuild()
        elif key == "radius":
            self._fit_window()

    def _affinity(self, toast=False):
        """0 — окно видно на скриншотах/в трансляции; 0x11 — скрыто от захвата экрана."""
        if not self.state.get("capture_visible", True):
            return 0x11
        if toast and self.state.get("auto") and self.state.get("auto_mode") == "game":
            return 0x11          # всплывашка рядом с игрой могла бы засчитаться как сундук
        return 0

    def apply_capture_mode(self):
        pairs = [(self.hwnd, False)]
        try:
            if self.menu is not None and self.menu.alive():
                w = self.menu.win
                pairs.append((user32.GetParent(w.winfo_id()) or w.winfo_id(), False))
        except Exception:  # noqa: BLE001
            pass
        pairs.append((getattr(self.notifier, "hwnd", None), True))
        self.mapwin.apply_affinity()
        for hw, toast in pairs:
            if not hw:
                continue
            try:
                user32.SetWindowDisplayAffinity(hw, self._affinity(toast))
            except Exception:  # noqa: BLE001
                pass

    def _restyle_panel(self):
        self.obs.restyle()
        self.mapwin.restyle()
        self.root.configure(bg=T["bg"])
        self.grip.config(bg=T["bg"], fg=T["dim"])
        self._apply_alpha()
        self._build_ui()

    # ---------- Меню (Insert / Delete) ----------
    def toggle_menu(self):
        if self.menu is None or not self.menu.alive():
            self.menu = MenuWindow(self)
        if self.menu.visible:
            self.menu.hide()
        else:
            self.menu.show()

    def _prebuild_menu(self):
        """Меню строим заранее (скрытым), чтобы Insert открывал его мгновенно."""
        if self.menu is None:
            try:
                self.menu = MenuWindow(self)
            except Exception as e:  # noqa: BLE001
                _log(f"ОШИБКА меню: {e!r}")

    def _menu_sync(self):
        if self.menu is not None and self.menu.alive():
            self.menu.refresh()
        paint = getattr(self, "auto_paint", None)
        if paint is not None:
            try:
                paint()
            except tk.TclError:
                self.auto_paint = None

    def toggle_panel(self):
        on = not self.state["panel"]
        self.state["panel"] = on
        save_state(self.state)
        target = min(self.state["alpha"], PINNED_ALPHA_CAP) if self.pinned else self.state["alpha"]
        if self._panel_anim:
            self._panel_anim["on"] = False
        a0 = self._panel_a
        self._fading = True          # пока идёт плавность, _apply_alpha не трогает прозрачность

        def fade(k):
            self._panel_a = k
            self.root.attributes("-alpha", target * k)

        def finish():
            self._fading = False
            self._panel_anim = None
            self._apply_alpha()

        if on:
            # Окно делаем невидимым ДО показа и до перестройки: раньше оно на миг
            # появлялось с полной непрозрачностью и только потом плавно проявлялось.
            fade(a0)
            self.root.deiconify()
            self.set_pinned(self.pinned)
            fade(a0)
            self._panel_anim = animate(self.root, 220, lambda e: fade(a0 + (1 - a0) * e),
                                       done=finish)
        else:
            def gone():
                self.root.withdraw()
                self._panel_a = 0.0
                finish()

            self._panel_anim = animate(self.root, 160, lambda e: fade(a0 * (1 - e)),
                                       done=gone)
        self._menu_sync()

    def open_settings(self):
        if self.settings_win and self.settings_win.alive():
            self.settings_win.win.lift()
            return
        self.settings_win = SettingsWindow(self)

    def _apply_alpha(self):
        if self._fading:
            return
        a = self.state["alpha"]
        if self.pinned:
            a = min(a, PINNED_ALPHA_CAP)
        self.root.attributes("-alpha", a)
        if self.ready:
            self._pin_style()

    # ---------- Смена горячих клавиш ----------
    def apply_hotkeys(self, new_map, done):
        full = {a: self.state["hk_" + a] for a in HK_IDS}
        full.update(new_map)
        parsed = {}
        for a, combo in full.items():
            p = parse_combo(combo)
            if p is None:
                done(False, "Неверное сочетание")
                return
            parsed[a] = p
        for p in parsed.values():
            if p in FIXED_LOOKUP:
                done(False, "Занято: " + ACTION_RU[FIXED_LOOKUP[p]])
                return

        if self.hk_cmd_q is None:
            for a, c in new_map.items():
                self.state["hk_" + a] = c
            save_state(self.state)
            done(True, None)
            return

        self._hk_token += 1
        tok = self._hk_token
        self._hk_pending[tok] = (dict(new_map), done)
        changes = {HK_IDS[a]: (parsed[a][0] | MOD_NOREPEAT, parsed[a][1])
                   for a in new_map}
        self.hk_cmd_q.put(("apply", tok, changes))

    def _on_hk_result(self, tok, ok, err):
        item = self._hk_pending.pop(tok, None)
        if item is None:
            return
        new_map, done = item
        if ok:
            for a, c in new_map.items():
                self.state["hk_" + a] = c
            save_state(self.state)
            self._refresh_hint()
        done(ok, err)

    # ---------- Закрепление ----------
    def toggle_pin(self):
        self.set_pinned(not self.pinned)

    def _pin_style(self):
        """Привести стили окна в соответствие с self.pinned. Tk иногда сбрасывает их сам
        (смена прозрачности, скрытие/показ окна) - раньше из-за этого закрепление "слетало",
        хотя скрепка горела, или не включалось с первого клика."""
        try:
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        except tk.TclError:
            return
        self.hwnd = hwnd
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        want = ex | WS_EX_LAYERED
        if self.pinned:
            want |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        else:
            want &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        if want != ex:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, want)

    def _pin_watchdog(self):
        """Раз в секунду проверяем, что реальное состояние окна совпадает с кнопкой."""
        try:
            if self.root.winfo_exists():
                if self.ready and self.root.state() != "withdrawn":
                    self._pin_style()
                self.root.after(1000, self._pin_watchdog)
        except tk.TclError:
            pass

    def set_pinned(self, on):
        self.pinned = on
        self._place_grip(not on)
        self._apply_alpha()
        self.state["pinned"] = on
        save_state(self.state)
        self._build_ui()
        self._pin_style()          # после прозрачности и перестройки, чтобы ничего не сбросило
        self._menu_sync()

    # ---------- Служебное ----------
    def _refresh(self):
        for kind, lbl in self.nums.items():
            lbl.config(text=str(self.state[kind]))
        self.obs.update()
        mn = getattr(self, "menu", None)
        if mn is not None and mn.alive():
            mn._update_hero()
        ce = self.count_entry              # в меню число тоже обновляем (если там не печатают)
        if ce is not None:
            try:
                if ce.winfo_exists() and self.root.focus_get() is not ce:
                    ce.delete(0, "end")
                    ce.insert(0, str(self.state["open"]))
            except Exception:  # noqa: BLE001
                pass

    def set_count(self, n, kind="open"):
        """Своё число из меню: «сколько сундуков вы открыли»."""
        n = _clamp(int(n), 0, MAX_COUNT)
        self.state[kind] = n
        self.history.clear()
        self.last_count_t = 0.0
        save_state(self.state)
        self._refresh()
        self._pop(kind)
        self._flash("Счётчик: %d" % n, kcolor())

    def _flash(self, text, color=None, ms=1400):
        self.status.config(text=text, fg=color or T["dim"])
        if self._flash_job:
            self.root.after_cancel(self._flash_job)
        self._flash_job = self.root.after(ms, self._flash_end)

    def _flash_end(self):
        self._flash_job = None
        self.status.config(text=self.hint(), fg=AMBER if self.pinned else T["dim"])

    def _pop(self, kind):
        lbl = self.nums.get(kind)
        if lbl:
            lbl.config(fg="#ffffff")
            self.root.after(
                160, lambda: self.nums.get(kind) and self.nums[kind].config(fg=kcolor()))

    def _keep_on_top(self):
        self.root.attributes("-topmost", True)
        if self.menu is not None and self.menu.alive():
            self.menu.win.attributes("-topmost", True)
        self.notifier.keep_on_top()
        self.mapwin.keep_on_top()
        self.root.after(2000, self._keep_on_top)

    # ---------- Ручное редактирование числа ----------
    def _edit(self, kind):
        if kind in self.entries or self.pinned:
            return
        lbl = self.nums[kind]
        ent = tk.Entry(lbl.master, width=5, bd=0, bg=T["card_hi"], fg=kcolor(),
                       insertbackground=T["fg"], font=(FONT, self.fs(22), "bold"))
        ent.insert(0, str(self.state[kind]))
        ent.select_range(0, "end")
        lbl.grid_remove()
        ent.grid(row=1, column=0, sticky="w")
        self.entries[kind] = ent
        self.root.focus_force()
        ent.focus_force()

        def done(apply):
            if self.entries.get(kind) is not ent:
                return
            if apply:
                self.state[kind] = to_int(ent.get(), self.state[kind])
                self.history.clear()
                save_state(self.state)
            del self.entries[kind]
            ent.destroy()
            lbl.grid()
            self._refresh()

        ent.bind("<Return>", lambda _e: done(True))
        ent.bind("<Escape>", lambda _e: done(False))
        ent.bind("<FocusOut>", lambda _e: done(True))

    # ---------- Логика счётчика ----------
    def _auto_apply(self):
        """Включить нужный движок автоподсчёта: по плашке в игре или по реплею."""
        on, mode = self.state["auto"], self.state["auto_mode"]
        if on and mode == "game" and AUTO_OK:
            self.auto_evt.set()
        else:
            self.auto_evt.clear()
        if on and mode == "replay":
            self.replay_evt.set()
        else:
            self.replay_evt.clear()

    def toggle_auto(self):
        if not AUTO_OK and self.state["auto_mode"] == "game":
            self._flash(AUTO_ERR, ALERT, 5000)
            return
        on = not self.state["auto"]
        self.state["auto"] = on
        save_state(self.state)
        self._auto_apply()
        self._refresh_hint()
        self.apply_capture_mode()
        self._menu_sync()
        self._flash("Автоподсчёт включён" if on else "Автоподсчёт выключен",
                    T["open"] if on else None)

    def set_auto_mode(self, mode):
        if mode not in ("game", "replay") or mode == self.state["auto_mode"]:
            return
        self.state["auto_mode"] = mode
        if mode == "game" and not AUTO_OK:
            self.state["auto"] = False
        save_state(self.state)
        self._auto_apply()
        self._refresh_hint()
        self.apply_capture_mode()
        self._menu_sync()
        self._flash("Автоподсчёт: в игре" if mode == "game" else "Автоподсчёт: по реплею")

    def set_auto_option(self, mode, on):
        """Включить (или выключить) один из двух вариантов; включённым может быть только один."""
        if on and mode == "game" and not AUTO_OK:
            self._flash(AUTO_ERR, ALERT, 5000)
            return
        if on:
            self.state["auto_mode"], self.state["auto"] = mode, True
        elif self.state["auto_mode"] == mode:
            self.state["auto"] = False
        save_state(self.state)
        self._auto_apply()
        self._refresh_hint()
        self.apply_capture_mode()
        self._menu_sync()
        self._flash(("Автоподсчёт в игре" if mode == "game" else "Автоподсчёт по реплею")
                    + (" включён" if on else " выключен"), T["open"] if on else None)

    def on_replay(self, res):
        """Реплей прочитан: добавляем все сундуки матча разом."""
        st = self.state
        n = res["chests"]
        st["replays_done"] = (st["replays_done"] + [res["sig"]])[-300:]
        if res.get("br") is False:      # не Battle Royale (творческий и т.п.)
            save_state(st)
            return
        st["games"] = (st["games"] + [[time.strftime("%Y-%m-%d %H:%M:%S"), n]])[-300:]
        if n:
            st["open"] = min(MAX_COUNT, st["open"] + n)
            day_bump(st, n)
        save_state(st)
        self._refresh()
        self._pop("open")
        self._flash("Реплей: +%d" % n, kcolor())
        if st["notify"]:
            self.notifier.show("Игра: %d · всего %d" % (n, st["open"]))

    def set_obs(self, on):
        """Окно для OBS: включить / выключить (меню, крестик окна, флаг --obs)."""
        on = bool(on)
        self.state["obs_window"] = on
        save_state(self.state)
        if on:
            self.obs.show()
        else:
            self.obs.hide()
        self._menu_sync()
        self._flash("Окно для OBS открыто" if on else "Окно для OBS закрыто",
                    T["open"] if on else None)

    def _sync_track(self):
        """Слежение включено, только когда карта показана и опция «следить» стоит."""
        if AUTO_OK and self.state["minimap"] and self.state["map_track"]:
            self.track_evt.set()
        else:
            self.track_evt.clear()
            self.mapwin.clear_pos()
        self.mapwin.apply_affinity()

    def toggle_minimap(self):
        on = not self.state["minimap"]
        self.state["minimap"] = on
        save_state(self.state)
        if on:
            self.mapwin.show()
        else:
            self.mapwin.hide()
        self._sync_track()
        self._menu_sync()
        self._flash("Мини-карта включена" if on else "Мини-карта выключена",
                    T["open"] if on else None)

    def toggle_notify(self):
        on = not self.state["notify"]
        self.state["notify"] = on
        save_state(self.state)
        if on:
            self.notifier.demo()
        else:
            self.notifier.hide()
        self._menu_sync()
        self._flash("Уведомление включено" if on else "Уведомление выключено",
                    T["open"] if on else None)

    def count(self, kind="open", auto=False):
        now = time.monotonic()
        if not auto and now - self.last_count_t < self.state["cooldown"]:
            self._flash("Уже засчитано", ALERT)
            return
        if self.state[kind] >= MAX_COUNT:
            return
        self.last_count_t = now
        self.state[kind] += 1
        self.history.append(kind)
        if kind == "open":
            day_bump(self.state, 1)
        save_state(self.state)
        self._refresh()
        self._pop(kind)
        tail = " (авто)" if auto else ""
        self._flash("+1 открытый" + tail, kcolor())
        if self.state["notify"]:
            self.notifier.show("Всего открыто: %d" % self.state[kind])

    def minus(self, kind="open"):
        if self.state[kind] <= 0:
            return
        self.state[kind] -= 1
        if kind == "open":
            day_bump(self.state, -1)
        if kind in self.history:
            idx = len(self.history) - 1 - self.history[::-1].index(kind)
            self.history.pop(idx)
        save_state(self.state)
        self._refresh()
        self._flash("−1")

    def undo(self):
        if not self.history:
            self._flash("Нечего отменять")
            return
        kind = self.history.pop()
        self.state[kind] = max(0, self.state[kind] - 1)
        if kind == "open":
            day_bump(self.state, -1)
        self.last_count_t = 0.0
        save_state(self.state)
        self._refresh()
        self._flash("Отменено")

    def reset(self):
        now = time.monotonic()
        if now < self.reset_armed_until:
            self.state["open"] = 0
            self.history.clear()
            self.reset_armed_until = 0.0
            save_state(self.state)
            self._refresh()
            self._flash("Счётчик сброшен", T["open"])
        else:
            self.reset_armed_until = now + RESET_CONFIRM_SEC
            self._flash("Ещё раз ⟲ — сбросить", ALERT, int(RESET_CONFIRM_SEC * 1000))

    def quit(self):
        self.stop_evt.set()
        self.notifier.destroy()
        self.mapwin.destroy()
        self._kill_ghost()
        save_state(self.state)
        self.root.destroy()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "action":
                    if self.capturing:
                        if self.capture_conflict:
                            self.capture_conflict(payload)
                        continue
                    if payload == "menu":
                        self.toggle_menu()
                    elif payload == "open":
                        self.count("open")
                    elif payload == "undo":
                        self.undo()
                    elif payload == "reset":
                        self.reset()
                    elif payload == "pin":
                        self.toggle_pin()
                    elif payload == "auto":
                        self.toggle_auto()
                    elif payload == "map":
                        self.toggle_minimap()
                    elif payload == "quit":
                        self.quit()
                        return
                elif kind == "replay":
                    self.on_replay(payload)
                elif kind == "replay_error":
                    self._flash(payload, ALERT, 6000)
                elif kind == "auto":
                    if (self.state["auto"] and self.state["auto_mode"] == "game"
                            and not self.capturing
                            and not self.entries and payload == "open"):
                        self.count("open", auto=True)
                elif kind == "auto_error":
                    self.state["auto"] = False
                    self.auto_evt.clear()
                    self._refresh_hint()
                    self._flash(payload, ALERT, 5000)
                elif kind == "pos":
                    self.mapwin.set_pos(payload)
                elif kind == "track_error":
                    self.track_evt.clear()
                    self._flash("Слежение: " + payload, ALERT, 6000)
                elif kind == "hk_result":
                    self._on_hk_result(*payload)
                elif kind == "hotkey_error":
                    self._flash(f"Занято: {ACTION_RU.get(payload, payload)}", ALERT, 4000)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--tracktest":
        # python fortnite_counter.py --tracktest скриншот.png — где программа видит игрока
        if not AUTO_OK:
            raise SystemExit(AUTO_ERR)
        _shot = _decode(sys.argv[2])
        _ref = find_file(REF_NAMES)
        if _shot is None or _ref is None:
            raise SystemExit("Нет скриншота или map_ref.png")
        _p = Locator(_ref).locate(crop_region(_shot))
        if _p is None:
            raise SystemExit("Миникарта не опознана — проверь MINIMAP_REGION (--dumpmini)")
        print("карта-эталон: (%.1f, %.1f)  chest_map: (%.1f, %.1f)" % (_p + ref_to_chest(*_p)))
        raise SystemExit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--dumpmini":
        # сохраняет вырез миникарты с текущего экрана в minimap_crop.png (папка настроек)
        if not AUTO_OK:
            raise SystemExit(AUTO_ERR)
        os.makedirs(APP_DIR, exist_ok=True)
        _out = os.path.join(APP_DIR, "minimap_crop.png")
        cv2.imencode(".png", make_region_grabber(MINIMAP_REGION)())[1].tofile(_out)
        raise SystemExit("Сохранено: " + _out)
    if os.name != "nt":
        raise SystemExit("Программа работает только в Windows.")
    app = CounterApp()
    if app.ok:
        app.run()
