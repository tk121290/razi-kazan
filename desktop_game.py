from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import socket
import tempfile
import threading
import time
import urllib.request as _urllib
import webbrowser
from enum import Enum, auto
from pathlib import Path

import pygame
import qrcode
import websockets

# .env dosyasından GEMINI_API_KEY vb. yükle (opsiyonel)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PORT = 8000
SCORES_FILE = Path(__file__).parent / "scores.json"


def local_network_host() -> str:
    configured_host = os.environ.get("RAZI_HOST")
    if configured_host:
        return configured_host
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


NETWORK_HOST = local_network_host()
SERVER_URL = f"ws://{NETWORK_HOST}:{PORT}"
PLAY_URL = f"http://{NETWORK_HOST}:{PORT}/play"
WIDTH, HEIGHT = 1100, 700
# FLOOR_Y Game.__init__ içinde _make_background çağrısından sonra self.floor_y olarak hesaplanır.
# Zemin yüzdesi: piksel analizi → taş zemin y≈90% from top (502/558)
# Contain modunda 1100×599 render + 50px top-offset → floor_y ≈ 589
BG_FLOOR_PCT = 0.95   # Kalibrasyonu buradan ayarla (büyüt → aşağı, küçült → yukarı)

# ─── 30 Tarihi Simya Elementi ────────────────────────────────────────────────
def _load_elements():
    json_path = Path(__file__).parent / "razi_elements.json"
    if json_path.exists():
        try:
            items = json.loads(json_path.read_text(encoding="utf-8"))
            mats = tuple(it["id"] for it in items)
            names = {it["id"]: it["name"] for it in items}
            symbols = {it["id"]: it["symbol"] for it in items}
            notes = {it["id"]: it.get("fact", "") for it in items}
            colors = {it["id"]: tuple(it["color"]) for it in items}
            colors_lt = {it["id"]: tuple(it.get("color_lt", [min(255, c + 45) for c in it["color"]])) for it in items}
            return mats, names, symbols, notes, colors, colors_lt
        except Exception as e:
            print("razi_elements.json yüklenemedi:", e)
    # Fallback
    mats = ("civa", "kukurt", "antimon", "tuz", "demir", "bakir", "fosfor", "arsenik")
    names = {m: m.capitalize() for m in mats}
    symbols = {m: "🜔" for m in mats}
    notes = {m: "" for m in mats}
    colors = {m: (150, 150, 150) for m in mats}
    colors_lt = {m: (200, 200, 200) for m in mats}
    return mats, names, symbols, notes, colors, colors_lt

MATERIALS, MATERIAL_NAMES, MATERIAL_SYMBOLS, MATERIAL_NOTES, COLORS, COLORS_LT = _load_elements()

# ─── Tarihi Ebû Bekir er-Râzî Reçeteleri (Dönüm Noktası Seviyeleri) ───────────
HISTORICAL_RECIPES = {
    5:   ("Tuz Ruhu Damıtımı", ("tuz", "kukurt", "tuz")),
    10:  ("Zaç & Demir Sentezi", ("tuz", "demir", "bakir", "civa")),
    25:  ("Sirke Ruhu Ayini", ("sirke", "sap", "tuz", "bakir", "civa")),
    50:  ("El-Kühül Damıtımı", ("sirke", "kukurt", "civa", "nisadir", "altin")),
    75:  ("Tıbbi Panzehir Sentezi", ("altin", "gumus", "civa", "safran", "afyon", "kafur")),
    100: ("Büyük İksir (İksir-i Âzam)", ("civa", "kukurt", "altin", "gumus", "buyuk_iksir")),
}

# ─── Renk Paleti ──────────────────────────────────────────────────────────────
BG       = (17, 13, 12)          # çok koyu zemin
PANEL    = (35, 24, 20)          # kart arka planı
PANEL_LT = (52, 37, 30)          # açık panel
BORDER   = (100, 68, 42)         # altın-kahve kenarlık
TEXT     = (240, 228, 200)       # ana metin — fildişi
TEXT_DIM = (160, 145, 118)       # ikincil metin
GOLD     = (212, 168, 72)        # vurgu altın
GOLD_LT  = (240, 200, 110)       # parlak altın
GREEN    = (82, 185, 130)        # başarı yeşili
GREEN_LT = (120, 220, 165)
RED      = (200, 75, 60)         # hata kırmızısı
RED_LT   = (235, 110, 90)
SHADOW   = (0, 0, 0)



class GameMode(Enum):
    SINGLE = "single"
    DUEL   = "duel"


class GameState(Enum):
    MODE_SELECT        = auto()
    WAITING_FOR_PLAYER = auto()
    PROLOGUE           = auto()
    DUEL_LOBBY         = auto()
    RHAZI_TURN         = auto()
    PLAYER_TURN        = auto()
    RESOLUTION         = auto()
    GAME_OVER          = auto()
    DUEL_MATCH_OVER    = auto()
    CREDITS_VIEW       = auto()


BEYHEKIM_PROLOGUE_SINGLE = (
    "Hoş geldin hekim namzedi! Ben Tabîb Ekmeleddin, nam-ı diğer Bey Hekim. "
    "Konya Dârüşşifası'nın ve kadim hekimliğin sırlarını öğrenmek için kazanın başına geçtin. "
    "Birazdan kazana şifalı cevherler, bitkiler ve cevherler ekleyeceğim. Bu sırayı dikkatle aklında tut! "
    "Sıra sana geldiğinde telefonundaki butonlarla aynı sırayla kazana ekle. 3 şişe kırma hakkın var. "
    "Her 3 elementte bir süren artacak. Zihnini topla ve hazır olduğunda Başla'ya bas!"
)

BEYHEKIM_PROLOGUE_DUEL = (
    "Huzuruma hoş geldiniz çıraklar! Ben Tabîb Ekmeleddin, nam-ı diğer Bey Hekim. "
    "Hanginizin dârüşşifanın yeni baş hekimi olacağını görmek için bu yarışı tertip ettim. "
    "Kazana atacağım şifalı malzemeleri dikkatle izleyin. Sıra size geldiğinde aynı sırayı ilk ve eksiksiz "
    "tamamlayan çırak raundu ve 1 yıldızı ⭐ kazanır. Yanlış malzeme seçen 1.2 saniye sersemler ve sırası başa döner! "
    "Toplam 3 raunt kazanan şampiyon ilan edilir. Hazırsanız Başla'ya basın!"
)

RHAZI_PROLOGUE_SINGLE = BEYHEKIM_PROLOGUE_SINGLE
RHAZI_PROLOGUE_DUEL = BEYHEKIM_PROLOGUE_DUEL

# ── ERÜ Anadolu Tıp Tarihi Topluluğu — Kayan Jenerik (Film Sonu Credits Roll) ──
CREDITS_ROLL_DATA = [
    {
        "badge": "🏛️ PROJE SAHİBİ VE ORGANİZASYON",
        "title": "ERÜ ANADOLU TIP TARİHİ TOPLULUĞU",
        "desc": "Erciyes Üniversitesi Tıp Fakültesi & Kadim Kültür Mirası",
        "names": [
            "Erciyes Üniversitesi Anadolu Tıp Tarihi Topluluğu",
        ]
    },
    {
        "badge": "👑 TOPLULUK YÖNETİMİ & KOORDİNASYON",
        "title": "GENEL YÖNETİM HEYETİ",
        "desc": "Yönetim Kurulu Başkanı ve Temsilciler Heyeti",
        "names": [
            "Topluluk Yönetim Kurulu Başkanı",
            "Yönetim Kurulu Başkan Yardımcısı",
            "Genel Sekreter & Organizasyon Sorumlusu",
            "Denetim ve İdare Kurulu Heyeti",
        ]
    },
    {
        "badge": "💻 YAZILIM VE OYUN MİMARİSİ",
        "title": "OYUN MOTORU & BİLİŞİM KURULU",
        "desc": "FastAPI WebSockets, Python Pygame & Mobil Kumanda",
        "names": [
            "Oyun Motoru & Mekanik Geliştiricileri",
            "WebSockets Ağ Mimarisi & Sunucu Ekibi",
            "Mobil Web Kumandası & Dokunsal Arayüz Ekibi",
        ]
    },
    {
        "badge": "🩺 AKADEMİK TIP TARİHİ & DANIŞMANLIK",
        "title": "KLİNİK SEMİYOLOJİ VE TARİH TETKİK KURULU",
        "desc": "Tabîb Ekmeleddin (Bey Hekim) Doktrini & Selçuklu Tıbbı",
        "names": [
            "Tıp Tarihi & Deontoloji Danışmanları",
            "Selçuklu Dârüşşifaları ve Tabîb Ekmeleddin Araştırma Ekibi",
            "Klinik Sfigmoloji, Uroskopi ve Galenik Farmakoloji Masası",
        ]
    },
    {
        "badge": "🎨 SANAT, TASARIM VE TEZHİP GRAFİKLERİ",
        "title": "GÖRSEL İLETİŞİM & TEZHİP SANATI",
        "desc": "Selçuklu Altın Varak & Turkuazı, Piksel Çizimler",
        "names": [
            "Tezhipli Selçuklu Risalesi Sanat Ekibi",
            "Piksel Çizim, Sprite & Karakter Animatörleri",
            "Simya Kazanı & Parşömen Arayüz Tasarımcıları",
        ]
    },
    {
        "badge": "👥 EMEĞİ GEÇEN ARKADAŞLARIMIZ",
        "title": "TOPLULUK ÜYELERİ & HEKİM ADAYLARI",
        "desc": "Test, Geri Bildirim ve Katkı Sağlayan Hekim Adayları",
        "names": [
            "ERÜ Anadolu Tıp Tarihi Topluluğu Aktif Üyeleri",
            "Erciyes Üniversitesi Tıp Fakültesi Öğrencileri",
            "Tüm Katkı ve Destek Veren Arkadaşlarımız",
        ]
    },
    {
        "badge": "🌟 ÖZEL TEŞEKKÜR",
        "title": "KADİM İLHAM VE MİRAS",
        "desc": "Tıbbın, Hikmetin ve Şefkatin Işığında",
        "names": [
            "Erciyes Üniversitesi Rektörlüğü ve Tıp Fakültesi Dekanlığı",
            "Tabîb Ekmeleddin el-Nahcivânî (Bey Hekim) Aziz Ruhuna",
            "Hz. Mevlânâ Celâleddîn-i Rûmî ve Konya Dârüşşifası Hekimleri",
        ]
    },
    {
        "badge": "📜 ŞİFA VE HİKMET DÜSTURU",
        "title": "TABÎB EKMELEDDİN'İN SÖZÜ",
        "desc": "«Gerçek hekim odur ki hastanın derdine derman, ruhuna ve gönlüne şifa ola...»",
        "names": [
            "— ERÜ ANADOLU TIP TARİHİ TOPLULUĞU —",
            "KAYSERİ · 2026",
        ]
    }
]




def make_room_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(random.choice(alphabet) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"


def load_scores() -> list[dict]:
    if SCORES_FILE.exists():
        try:
            return json.loads(SCORES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def get_alchemical_title(lvl: int) -> str:
    if lvl >= 100: return "İksir-i Âzam Üstadı 🌌"
    if lvl >= 75:  return "Şeyhü'l-Etıbbâ 👑"
    if lvl >= 50:  return "Büyük Hekim 📜"
    if lvl >= 25:  return "Usta Simyager ⚗️"
    if lvl >= 10:  return "Kalfa Tabip 🧪"
    return "Çırak Simyacı 🕯️"


def save_score(level: int, room_id: str = "", player_name: str = "Simyacı", max_combo: int = 0) -> None:
    """Skoru yerel JSON'a ve sunucu liderlik tablosuna kaydeder."""
    # Yerel kayıt
    scores = load_scores()
    scores.append({
        "level": level,
        "player_name": player_name,
        "max_combo": max_combo,
        "ts": int(time.time()),
    })
    scores = sorted(scores, key=lambda s: s["level"], reverse=True)[:10]
    SCORES_FILE.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")

    # Sunucu liderlik tablosuna gönder (arka planda, hata sessizce yutulur)
    def _post() -> None:
        try:
            data = json.dumps({
                "level": level,
                "room_id": room_id,
                "player_name": player_name,
                "max_combo": max_combo,
            }).encode()
            req = _urllib.Request(
                f"http://localhost:{PORT}/api/scores",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _urllib.urlopen(req, timeout=2)
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


# ─── Network ──────────────────────────────────────────────────────────────────

class NetworkBridge:
    def __init__(self, server_url: str, room_id: str, events: queue.Queue[dict]) -> None:
        self.server_url = server_url.rstrip("/")
        self.room_id = room_id
        self.events = events
        self.outgoing: queue.Queue[dict] = queue.Queue()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        self.thread.join(timeout=2)

    def send(self, message: dict) -> None:
        self.outgoing.put(message)

    def _run(self) -> None:
        asyncio.run(self._listen())

    async def _listen(self) -> None:
        url = f"{self.server_url}/ws/{self.room_id}?role=desktop"
        while not self.stop_requested.is_set():
            try:
                async with websockets.connect(url) as socket:
                    self.events.put({"type": "server_connected"})

                    async def sender() -> None:
                        while not self.stop_requested.is_set():
                            try:
                                outgoing = self.outgoing.get_nowait()
                                await socket.send(json.dumps(outgoing))
                            except queue.Empty:
                                await asyncio.sleep(0.05)

                    async def receiver() -> None:
                        while not self.stop_requested.is_set():
                            message = json.loads(await socket.recv())
                            self.events.put(message)

                    send_task = asyncio.create_task(sender())
                    recv_task = asyncio.create_task(receiver())
                    done, pending = await asyncio.wait(
                        [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
            except (OSError, websockets.WebSocketException):
                if not self.stop_requested.is_set():
                    self.events.put({"type": "server_disconnected"})
                    await asyncio.sleep(1)


# ─── Sprite helpers ───────────────────────────────────────────────────────────

def decorate_forge(frame: pygame.Surface) -> None:
    frame.fill((0, 0, 0, 0), (0, 0, 64, 47))
    c_x, c_y, c_w, c_h = 8, 30, 48, 28
    pygame.draw.ellipse(frame, (28, 30, 33), (c_x, c_y, c_w, c_h))
    pygame.draw.ellipse(frame, (45, 48, 52), (c_x + 5, c_y + 5, c_w - 10, c_h - 10))
    pygame.draw.rect(frame, (65, 68, 72), (c_x + 8, c_y + 6, 8, 4))
    pygame.draw.ellipse(frame, (40, 42, 46), (c_x - 4, c_y - 5, c_w + 8, 14))
    pygame.draw.ellipse(frame, (18, 20, 22), (c_x, c_y - 3, c_w, 10))
    pygame.draw.ellipse(frame, (30, 185, 80), (c_x + 2, c_y - 2, c_w - 4, 8))
    pygame.draw.ellipse(frame, (80, 230, 130), (c_x + 12, c_y - 3, 12, 4))
    pygame.draw.circle(frame, (150, 255, 180), (20, c_y - 1), 2)
    pygame.draw.circle(frame, (120, 240, 160), (38, c_y - 2), 1)
    pygame.draw.circle(frame, (180, 255, 200), (50, c_y), 1)


def remove_connected_light_background(frame: pygame.Surface) -> None:
    width, height = frame.get_size()
    pending = [(x, 0) for x in range(width)]
    pending.extend((x, height - 1) for x in range(width))
    pending.extend((0, y) for y in range(1, height - 1))
    visited: set[tuple[int, int]] = set()
    while pending:
        x, y = pending.pop()
        if (x, y) in visited or not (0 <= x < width and 0 <= y < height):
            continue
        visited.add((x, y))
        red, green, blue, _ = frame.get_at((x, y))
        if min(red, green, blue) < 232:
            continue
        frame.set_at((x, y), (red, green, blue, 0))
        pending.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))


class SpriteAnimation:
    def __init__(
        self,
        filepath: str,
        frame_width: int,
        frame_height: int,
        scale: float = 4,
        columns: int | None = None,
        rows: int = 1,
        remove_light_background: bool = False,
        align_bottom_to_first: bool = False,
        decorator=None,
    ):
        self.frames: list[pygame.Surface] = []
        try:
            sheet = pygame.image.load(filepath).convert_alpha()
            sheet_width, sheet_height = sheet.get_size()
            columns = columns or max(1, sheet_width // frame_width)
            background_color = sheet.get_at((0, 0))
            for row in range(rows):
                for column in range(columns):
                    x = round(column * sheet_width / columns)
                    nx = round((column + 1) * sheet_width / columns)
                    y = round(row * sheet_height / rows)
                    ny = round((row + 1) * sheet_height / rows)
                    frame = sheet.subsurface(pygame.Rect(x, y, nx - x, ny - y)).copy()
                    if remove_light_background:
                        remove_connected_light_background(frame)
                    elif len(background_color) < 4 or background_color[3] != 0:
                        frame.set_colorkey(background_color)
                    if decorator:
                        decorator(frame)
                    frame = pygame.transform.scale(
                        frame, (round(frame_width * scale), round(frame_height * scale))
                    )
                    self.frames.append(frame)
            if align_bottom_to_first and self.frames:
                reference_bottom = self.frames[0].get_bounding_rect().bottom
                for idx, frame in enumerate(self.frames):
                    bounds = frame.get_bounding_rect()
                    aligned = pygame.Surface(frame.get_size(), pygame.SRCALPHA)
                    aligned.blit(frame, (0, reference_bottom - bounds.bottom))
                    self.frames[idx] = aligned
        except Exception as e:
            print(f"Failed to load sprite {filepath}: {e}")

    def get_frame(self, time_sec: float, fps: float = 8.0) -> pygame.Surface | None:
        if not self.frames:
            return None
        return self.frames[int(time_sec * fps) % len(self.frames)]

    def get_frame_at(self, frame_idx: int) -> pygame.Surface | None:
        if not self.frames:
            return None
        return self.frames[frame_idx % len(self.frames)]


# ─── Sound ────────────────────────────────────────────────────────────────────

def _synth_sound(freq: float, duration: float, volume: float = 0.4,
                 wave: str = "sine", decay: float = 0.7) -> pygame.mixer.Sound:
    """Generates a simple synthesized sound without external files."""
    import math, array
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    buf = array.array("h")
    for i in range(n_samples):
        t = i / sample_rate
        env = (1.0 - t / duration) ** decay
        if wave == "sine":
            val = math.sin(2 * math.pi * freq * t)
        elif wave == "square":
            val = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
        elif wave == "saw":
            val = 2 * (t * freq - math.floor(t * freq + 0.5))
        else:
            val = math.sin(2 * math.pi * freq * t)
        buf.append(int(val * env * volume * 32767))
    # Stereo
    stereo = array.array("h")
    for s in buf:
        stereo.extend([s, s])
    return pygame.sndarray.make_sound(
        __import__("numpy").frombuffer(stereo, dtype="int16").reshape(-1, 2)
    )


class Sounds:
    def __init__(self) -> None:
        self.enabled = False
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            import numpy  # noqa — required for sndarray
            self.correct  = _synth_sound(660,  0.18, wave="sine",   decay=1.5)
            self.wrong    = _synth_sound(180,  0.32, wave="square",  decay=0.8)
            self.level_up = _synth_sound(880,  0.25, wave="sine",   decay=1.2)
            self.tick     = _synth_sound(1050, 0.06, volume=0.18, wave="sine", decay=2)
            self.gameover = _synth_sound(120,  0.55, wave="saw",    decay=0.5)
            self.drop     = _synth_sound(520,  0.12, volume=0.25, wave="sine", decay=2.5)
            self.enabled  = True
        except Exception as e:
            print(f"Sound disabled: {e}")

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        snd = getattr(self, name, None)
        if snd:
            snd.play()


# ─── Gemini Hint Engine ───────────────────────────────────────────────────────

class HintEngine:
    """Gemini API ile yanlış seçim sonrası dönemsel Türkçe ipucu üretir."""

    HINT_DURATION = 5.0  # saniye — ekranda gösterim süresi

    def __init__(self) -> None:
        self.enabled     = False
        self._result_q:  queue.Queue[dict] = queue.Queue()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print("HintEngine: GEMINI_API_KEY bulunamadı — ipuçları devre dışı.")
            return
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self._model  = genai.GenerativeModel("gemini-1.5-flash")
            self.enabled = True
            print("HintEngine: Gemini API bağlantısı kuruldu.")
        except Exception as e:
            print(f"HintEngine: Gemini başlatılamadı — {e}")

    # Oyun ipucu isteği: arkaplanda thread başlatır, bloklamaz
    def request(self, correct: str, wrong: str, level: int) -> None:
        if not self.enabled:
            return
        threading.Thread(
            target=self._fetch,
            args=(correct, wrong, level),
            daemon=True,
        ).start()

    def _fetch(self, correct: str, wrong: str, level: int) -> None:
        correct_tr = MATERIAL_NAMES.get(correct, correct)
        wrong_tr   = MATERIAL_NAMES.get(wrong,   wrong)
        prompt = (
            f"Sen Tabîb Ekmeleddin'in (Bey Hekim) ruhusun. 13. yüzyıl Selçuklu başhekimi ve Mevlânâ'nın tabibi.\n"
            f"Oyuncu '{wrong_tr}' seçti ama doğrusu '{correct_tr}' idi.\n"
            f"'{correct_tr}' hakkında tek cümle, dönemsel ve hikâyeli, "
            f"maksimum 18 kelime, sade Türkçe ipucu ver.\n"
            f"Sadece ipucunu yaz, başka hiçbir şey ekleme."
        )
        try:
            response = self._model.generate_content(prompt)
            text = response.text.strip().strip('"').strip("'")
            self._result_q.put({"text": text, "correct": correct})
        except Exception as e:
            print(f"HintEngine: API hatası — {e}")

    # Her frame çağrılır; yeni ipucu varsa (text, material) döndürür
    def poll(self) -> tuple[str, str] | None:
        try:
            item = self._result_q.get_nowait()
            return item["text"], item["correct"]
        except queue.Empty:
            return None


# ─── Voice Engine (edge-tts) ─────────────────────────────────────────────────

class VoiceEngine:
    """edge-tts ile Türkçe sesli anlatım. Arkaplanda çalışır, bloklamaz."""

    VOICE = "tr-TR-AhmetNeural"   # Erkek Türkçe ses
    CHANNEL = 5                    # pygame ses kanalı (0-4 oyun sesleri için)

    def __init__(self) -> None:
        self.enabled  = False
        # Kullanıcı isteği doğrultusunda sesli konuşma (TTS) iptal edildi;
        # Râzî'nin tüm diyalogları konuşma balonunda gösterilir.

    def speak(self, text: str, interrupt: bool = False) -> None:
        pass

    def _worker(self) -> None:
        """Arkaplan thread: kuyruktaki metinleri TTS'e çevirir ve çalar."""
        while True:
            text = self._queue.get()
            if text is None:
                break
            asyncio.run(self._synth_and_play(text))

    async def _synth_and_play(self, text: str) -> None:
        try:
            communicate = self._edge_tts.Communicate(text, self.VOICE)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name
            await communicate.save(tmp_path)
            # pygame.mixer.Sound mp3 yükleyemeyebilir; pygame.mixer.music kullan
            sound = pygame.mixer.Sound(tmp_path)
            if self._channel:
                self._channel.play(sound)
                # Ses bitene kadar bekle
                while self._channel.get_busy():
                    await asyncio.sleep(0.05)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        except Exception as e:
            print(f"VoiceEngine: TTS hatası — {e}")



class Game:
    # Bilgi kartı gösterim süresi (saniye)
    NOTE_DURATION = 3.5
    # Oyuncu bağlantı zaman aşımı (saniye) — bu kadar sonra QR ekranına dön
    PLAYER_TIMEOUT = 120.0

    def __init__(self, server_url: str = "ws://localhost:8000"):
        pygame.init()
        # FULLSCREEN | SCALED: 1100×700 mantıksal çözünürlüğü tam ekrana ölçekler,
        # oran bozulmaz (siyah kenarlık eklenebilir). ESC ile çıkılır.
        self.screen = pygame.display.set_mode(
            (WIDTH, HEIGHT), pygame.FULLSCREEN | pygame.SCALED
        )
        pygame.display.set_caption("Tabîb Ekmeleddin'in Kazanı (Bey Hekim)")
        self.clock = pygame.time.Clock()
        self.pixel_surface = pygame.Surface((WIDTH, HEIGHT))

        # ── Fontlar ──────────────────────────────────────────────────────────
        _fp = os.path.join(os.path.dirname(__file__), "assets", "PressStart2P.ttf")
        self.font_title  = pygame.font.Font(_fp, 18)   # Ekran başlığı
        self.font_large  = pygame.font.Font(_fp, 13)   # Seviye / durum
        self.font_medium = pygame.font.Font(_fp, 10)   # Malzeme adı, notlar
        self.font_small  = pygame.font.Font(_fp, 8)    # Yardımcı bilgi
        self.font_tiny   = pygame.font.Font(_fp, 7)    # Timer, ipucu
        self.font_symbol = pygame.font.SysFont("segoeuisymbol,segoeuiemoji,arial", 16)
        self.font_symbol_large = pygame.font.SysFont("segoeuisymbol,segoeuiemoji,arial", 28)
        self.font_body = pygame.font.SysFont("segoeui,arial,sans-serif", 15)
        self.font_body_bold = pygame.font.SysFont("segoeui,arial,sans-serif", 16, bold=True)
        self.font_body_large = pygame.font.SysFont("segoeui,arial,sans-serif", 20, bold=True)

        # ── Sprite Animasyonları ─────────────────────────────────────────────
        self.anim_master = SpriteAnimation(
            "assets/PNG/razi_anim/razi_spritesheet.png",
            frame_width=280,
            frame_height=230,
            scale=1.0,
            columns=6,
            rows=1,
        )
        # Sade simya kazanı (normal ateş ve duman)
        self.anim_forge = SpriteAnimation(
            "assets/PNG/cauldron_clean/cauldron_normal_spritesheet.png",
            frame_width=180,
            frame_height=240,
            scale=1.0,
            columns=6,
            rows=1,
        )
        # Hata anında aşırı alevlenme ve duman patlaması
        self.anim_forge_surge = SpriteAnimation(
            "assets/PNG/cauldron_clean/cauldron_surge_spritesheet.png",
            frame_width=180,
            frame_height=240,
            scale=1.0,
            columns=6,
            rows=1,
        )

        # ── Ses ──────────────────────────────────────────────────────────────
        self.sounds = Sounds()

        # ── Yapay Zeka & Ses Motoru ──────────────────────────────────────────
        self.hint_engine = HintEngine()
        self.voice       = VoiceEngine()

        # ── Durum ────────────────────────────────────────────────────────────
        self.events: queue.Queue[dict] = queue.Queue()
        self.room_id   = make_room_id()
        self.state     = GameState.MODE_SELECT
        self.level     = 1
        self.lives     = 3
        self.combo     = 0
        self.max_combo = 0
        self.recipe_name = ""
        self.best      = max((s["level"] for s in load_scores()), default=0)
        self.sequence: list[str] = []
        self.player_index  = 0
        self.phase_started = time.monotonic()
        self.phase_cursor  = 0
        self.last_message  = "Oyun modu seçin"
        self.round_success = False

        # Prologue (Anlatım) Durumu
        self.prologue_started = 0.0
        self.prologue_text    = ""
        self.prologue_readies = {"player_1": False, "player_2": False}
        self._prev_credits_state = GameState.MODE_SELECT
        self.credits_scroll_y    = 0.0
        self.credits_paused      = False
        self.credits_last_time   = 0.0

        # Konuşma balonu & Alev patlaması & Bilgi kartları
        self.bubble_text     = ""
        self.bubble_started  = 0.0
        self.bubble_duration = 0.0
        self.fire_surge_until= 0.0
        self.info_card_mat   = None
        self.info_card_until = 0.0
        self.last_unlocked_count = 4

        # Parçacıklar
        self.particles: list[dict] = []

        # Flash / shake
        self.flash_color   = (0, 0, 0)
        self.flash_started = 0.0
        self.shake_started = 0.0
        self.shake_offset  = (0, 0)

        # Malzeme kartı
        self.note_material: str | None = None
        self.note_started  = 0.0

        # Gemini ipucu
        self.hint_text:     str | None = None
        self.hint_material: str | None = None
        self.hint_started   = 0.0

        # Bekle saati (player disconnect timeout)
        self.wait_started = time.monotonic()
        self.player_connected = False

        # Skor tablosu
        self.game_over_time = 0.0
        self.final_level    = 1

        # ── 1v1 Çırak Düellosu (Multiplayer) Durumu ───────────────────────────
        self.mode = GameMode.SINGLE
        self.players: dict[str, dict] = {
            "player_1": {"name": "Çırak 1", "emblem": "☿", "ready": False, "connected": False},
            "player_2": {"name": "Çırak 2", "emblem": "🜍", "ready": False, "connected": False},
        }
        self.duel_scores = {"player_1": 0, "player_2": 0}
        self.duel_round = 1
        self.player_cursors = {"player_1": 0, "player_2": 0}
        self.player_lives   = {"player_1": 3, "player_2": 3}
        self.player_stuns   = {"player_1": 0.0, "player_2": 0.0}
        self.round_winner: str | None = None
        self.duel_match_winner: str | None = None
        self.lobby_countdown_start: float | None = None

        self.ambient_clock    = time.monotonic()
        self.animation_clock  = time.monotonic()

        self.qr_surface = self._make_qr()
        self.floor_y    = int(HEIGHT * BG_FLOOR_PCT)   # _make_background günceller
        self.network    = NetworkBridge(SERVER_URL, self.room_id, self.events)
        self.background = self._make_background()

    # ── QR ───────────────────────────────────────────────────────────────────

    def _make_qr(self) -> pygame.Surface:
        import io
        img = qrcode.make(f"{PLAY_URL}/{self.room_id}").convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return pygame.image.load(buf).convert()

    # ── Ana döngü ────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.network.start()
        running = True
        try:
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            if self.state == GameState.CREDITS_VIEW:
                                self._close_credits_view()
                            elif self.state == GameState.MODE_SELECT:
                                running = False
                            elif self.state in (GameState.WAITING_FOR_PLAYER, GameState.PROLOGUE, GameState.DUEL_LOBBY):
                                self._return_to_mode_select()
                            elif self.state in (GameState.GAME_OVER, GameState.DUEL_MATCH_OVER):
                                self._return_to_mode_select()
                            else:
                                running = False
                        elif event.key == pygame.K_1:
                            if self.state == GameState.MODE_SELECT:
                                self._select_mode(GameMode.SINGLE)
                        elif event.key == pygame.K_2:
                            if self.state == GameState.MODE_SELECT:
                                self._select_mode(GameMode.DUEL)
                        elif event.key in (pygame.K_c, pygame.K_k):
                            if self.state == GameState.CREDITS_VIEW:
                                self._close_credits_view()
                            else:
                                self._open_credits_view()
                        elif event.key in (pygame.K_SPACE, pygame.K_RETURN):
                            if self.state == GameState.CREDITS_VIEW:
                                self.credits_paused = not self.credits_paused
                            elif self.state == GameState.PROLOGUE:
                                self._start_game_from_prologue()
                            elif self.state == GameState.GAME_OVER:
                                self._reset_game()
                            elif self.state == GameState.DUEL_MATCH_OVER:
                                self._reset_duel()
                        elif event.key == pygame.K_r:
                            if self.state == GameState.CREDITS_VIEW:
                                self.credits_scroll_y = 0.0
                        elif event.key == pygame.K_UP:
                            if self.state == GameState.CREDITS_VIEW:
                                self.credits_scroll_y = max(0.0, self.credits_scroll_y - 45.0)
                        elif event.key == pygame.K_DOWN:
                            if self.state == GameState.CREDITS_VIEW:
                                self.credits_scroll_y += 45.0
                        elif event.key == pygame.K_m:
                            if self.state in (GameState.GAME_OVER, GameState.DUEL_MATCH_OVER, GameState.WAITING_FOR_PLAYER):
                                self._return_to_mode_select()
                    elif event.type == pygame.MOUSEWHEEL:
                        if self.state == GameState.CREDITS_VIEW:
                            self.credits_scroll_y = max(0.0, self.credits_scroll_y - event.y * 35.0)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self._handle_mouse_click(event.pos)

                self._consume_network_events()
                self._update()
                self._draw()
                pygame.display.flip()
                self.clock.tick(60)
        finally:
            self.network.stop()
            pygame.quit()

    # ── Fare Tıklama Yönetimi ────────────────────────────────────────────────

    def _handle_mouse_click(self, pos: tuple[int, int]) -> None:
        x, y = pos
        if self.state == GameState.MODE_SELECT:
            # Kart 1: Tek Kişilik Macera (90, 160, 430, 390)
            if 90 <= x <= 520 and 160 <= y <= 550:
                self._select_mode(GameMode.SINGLE)
            # Kart 2: 1v1 Çırak Düellosu (580, 160, 430, 390)
            elif 580 <= x <= 1010 and 160 <= y <= 550:
                self._select_mode(GameMode.DUEL)
            # Alt bar: Kulüp & Künye Butonu
            elif (WIDTH // 2 - 200) <= x <= (WIDTH // 2 + 200) and 580 <= y <= 626:
                self._open_credits_view()

        elif self.state == GameState.WAITING_FOR_PLAYER:
            # Geri Dön Butonu
            if 452 <= x <= 712 and 530 <= y <= 572:
                self._return_to_mode_select()
            # Kulüp & Künye Butonu
            elif 730 <= x <= 990 and 530 <= y <= 572:
                self._open_credits_view()

        elif self.state == GameState.PROLOGUE:
            # Başla Butonu (Kullanıcı şartı: Sadece "BAŞLA")
            btn_rect = pygame.Rect(WIDTH // 2 - 160, 580, 320, 54)
            if btn_rect.collidepoint(pos):
                self._start_game_from_prologue()
            # Sol üst Geri butonu
            elif 30 <= x <= 180 and 20 <= y <= 56:
                self._return_to_mode_select()

        elif self.state == GameState.GAME_OVER:
            cx = WIDTH // 2
            # Tekrar Oyna: (cx - 240, 316, 220, 42)
            if (cx - 240) <= x <= (cx - 20) and 316 <= y <= 358:
                self._reset_game()
            # Mod Seçimi: (cx + 20, 316, 220, 42)
            elif (cx + 20) <= x <= (cx + 240) and 316 <= y <= 358:
                self._return_to_mode_select()
            # Kulüp & Künye: (cx - 200, 374, 400, 42)
            elif (cx - 200) <= x <= (cx + 200) and 374 <= y <= 416:
                self._open_credits_view()

        elif self.state == GameState.DUEL_MATCH_OVER:
            cx = WIDTH // 2
            # Yeni Karşılaşma: (cx - 240, 325, 220, 42)
            if (cx - 240) <= x <= (cx - 20) and 325 <= y <= 367:
                self._reset_duel()
            # Mod Seçimi: (cx + 20, 325, 220, 42)
            elif (cx + 20) <= x <= (cx + 240) and 325 <= y <= 367:
                self._return_to_mode_select()
            # Kulüp & Künye: (cx - 200, 383, 400, 42)
            elif (cx - 200) <= x <= (cx + 200) and 383 <= y <= 425:
                self._open_credits_view()

        elif self.state == GameState.CREDITS_VIEW:
            # ERÜ Topluluk Kayıt Butonu (110 <= x <= 380, 615 <= y <= 670)
            if 110 <= x <= 380 and 615 <= y <= 670:
                try:
                    webbrowser.open("https://kulup.erciyes.edu.tr/uyelik/uyeol")
                except Exception:
                    pass
            # Tabîb Ekmeleddin PDF İndir Butonu (410 <= x <= 710, 615 <= y <= 670)
            elif 410 <= x <= 710 and 615 <= y <= 670:
                try:
                    webbrowser.open(f"http://localhost:{PORT}/download/tabib_ekmeleddin_kimdir.pdf")
                except Exception:
                    pass
            # Kapat / Geri Dön Butonu (740 <= x <= 990, 615 <= y <= 670)
            elif 740 <= x <= 990 and 615 <= y <= 670:
                self._close_credits_view()
            # Kayan jenerik alanına tıklandığında duraklat / devam et
            elif 80 <= x <= 1020 and 80 <= y <= 570:
                self.credits_paused = not self.credits_paused

    # ── Mod & Ekran Geçiş Yardımcıları ───────────────────────────────────────

    def _select_mode(self, mode: GameMode) -> None:
        self.mode = mode
        self.state = GameState.WAITING_FOR_PLAYER
        self.wait_started = time.monotonic()
        self.player_connected = False
        for p in self.players.values():
            p["connected"] = False
            p["ready"] = False
        self.network.send({"type": "mode_set", "mode": mode.value})
        if mode == GameMode.DUEL:
            self.speak_bubble("1v1 Çırak Düellosu! Her iki çırak da QR kodu okutsun.", duration=4.0)
        else:
            self.speak_bubble("Tek kişilik macera! Telefonunla QR kodu okut.", duration=4.0)

    def _return_to_mode_select(self) -> None:
        self.state = GameState.MODE_SELECT
        self.mode = GameMode.SINGLE
        self.player_connected = False
        for p in self.players.values():
            p["connected"] = False
            p["ready"] = False
        self.network.send({"type": "mode_set", "mode": "single"})
        self.speak_bubble("Oyun modunu seçin: 1 (Tek Kişilik) veya 2 (Düello).", duration=4.0)

    def _start_prologue(self) -> None:
        self.state = GameState.PROLOGUE
        self.prologue_started = time.monotonic()
        self.prologue_readies = {"player_1": False, "player_2": False}
        if self.mode == GameMode.DUEL:
            self.prologue_text = BEYHEKIM_PROLOGUE_DUEL
            self.network.send({
                "type": "prologue",
                "mode": "duel",
                "title": "Tabîb Ekmeleddin'in Talimatları",
                "text": BEYHEKIM_PROLOGUE_DUEL,
                "button_text": "BAŞLA",
            })
            self.speak_bubble("Huzuruma hoş geldiniz çıraklar! Dikkatle dinleyin.", duration=5.0)
        else:
            self.prologue_text = BEYHEKIM_PROLOGUE_SINGLE
            self.network.send({
                "type": "prologue",
                "mode": "single",
                "title": "Tabîb Ekmeleddin'in Talimatları",
                "text": BEYHEKIM_PROLOGUE_SINGLE,
                "button_text": "BAŞLA",
            })
            self.speak_bubble("Hoş geldin hekim namzedi! Konya Dârüşşifası'nın sırlarını öğrenmeye hazır mısın?", duration=5.0)

    def _start_game_from_prologue(self) -> None:
        if self.state != GameState.PROLOGUE:
            return
        if self.mode == GameMode.DUEL:
            self.duel_round = 1
            self.duel_scores = {"player_1": 0, "player_2": 0}
            self._start_rhazi_turn()
        else:
            self.level = 1
            self.lives = 3
            self.combo = 0
            self.max_combo = 0
            self._start_rhazi_turn()

    def _open_credits_view(self) -> None:
        if self.state != GameState.CREDITS_VIEW:
            self._prev_credits_state = self.state
            self.state = GameState.CREDITS_VIEW
            self.credits_scroll_y = 0.0
            self.credits_paused = False
            self.credits_last_time = time.monotonic()

    def _close_credits_view(self) -> None:
        if self.state == GameState.CREDITS_VIEW:
            self.state = self._prev_credits_state

    # ── Network Olayları ─────────────────────────────────────────────────────

    def _consume_network_events(self) -> None:
        while True:
            try:
                msg = self.events.get_nowait()
            except queue.Empty:
                return
            t = msg.get("type")
            if t == "player_connected":
                pid = msg.get("player_id", "player_1")
                if pid in self.players:
                    self.players[pid]["connected"] = True
                players_data = msg.get("players", {})
                for k, v in players_data.items():
                    if k in self.players:
                        self.players[k].update(v)
                    else:
                        self.players[k] = v
                p_count = msg.get("player_count", sum(1 for p in self.players.values() if p.get("connected")))

                if self.mode == GameMode.DUEL:
                    # Düello modunda 2 oyuncu da bağlanana kadar QR açık kalır
                    if p_count >= 2:
                        if self.state == GameState.WAITING_FOR_PLAYER:
                            self.state = GameState.DUEL_LOBBY
                            self.duel_scores = {"player_1": 0, "player_2": 0}
                            self.duel_round = 1
                            self.lobby_countdown_start = None
                            self.speak_bubble("İki Çırak da katıldı! 1v1 Çırak Düellosu Lobisi açıldı.", duration=4.0)
                    else:
                        self.speak_bubble("1. Çırak bağlandı! 2. Çırağın QR kodu okutması bekleniyor...", duration=4.0)
                else:
                    # Tek kişilik modda 1 oyuncu bağlandığı an anlatım ekranına (Prologue) geçer
                    if self.state == GameState.WAITING_FOR_PLAYER:
                        self.player_connected = True
                        self._start_prologue()

            elif t in ("player_updated", "player_ready"):
                pid = msg.get("player_id")
                if pid and pid in self.players:
                    if "name" in msg:
                        self.players[pid]["name"] = msg["name"]
                    if "emblem" in msg:
                        self.players[pid]["emblem"] = msg["emblem"]
                    if "ready" in msg:
                        self.players[pid]["ready"] = bool(msg["ready"])
                players_data = msg.get("players", {})
                for k, v in players_data.items():
                    if k in self.players:
                        self.players[k].update(v)

                # Lobi hazır durumu kontrolü -> Hazır olunca Prologue'a geçilir
                if self.state == GameState.DUEL_LOBBY:
                    p1_ready = self.players.get("player_1", {}).get("ready", False)
                    p2_ready = self.players.get("player_2", {}).get("ready", False)
                    if p1_ready and p2_ready:
                        if not self.lobby_countdown_start:
                            self.lobby_countdown_start = time.monotonic()
                            self.speak_bubble("Her iki çırak da hazır! Talimatlar geliyor...", duration=3.0)
                    else:
                        self.lobby_countdown_start = None

            elif t == "start_game":
                # Mobilde 'BAŞLA' butonuna basıldı
                if self.state == GameState.PROLOGUE:
                    self._start_game_from_prologue()

            elif t == "button":
                btn = msg.get("button", "")
                pid = msg.get("player_id", "player_1")
                if btn in ("reset", "rematch"):
                    if self.state in (GameState.GAME_OVER, GameState.DUEL_MATCH_OVER):
                        if self.mode == GameMode.DUEL:
                            self._reset_duel()
                        else:
                            self._reset_game()
                elif self.state == GameState.PLAYER_TURN:
                    if isinstance(btn, str) and (btn in self.material_pool or btn in MATERIALS):
                        self._handle_button(btn, pid)

            elif t == "player_disconnected":
                pid = msg.get("player_id")
                p_count = msg.get("player_count", 0)
                if pid in self.players:
                    self.players[pid]["connected"] = False
                    self.players[pid]["ready"] = False
                if self.mode == GameMode.DUEL:
                    if self.state in (GameState.DUEL_LOBBY, GameState.PROLOGUE, GameState.RHAZI_TURN, GameState.PLAYER_TURN):
                        self.state = GameState.WAITING_FOR_PLAYER
                        self.wait_started = time.monotonic()
                        self.speak_bubble("Bir çırak ayrıldı. Yeni çırak bekleniyor...", duration=4.0)
                else:
                    self.player_connected = False

    # ── Oyun geçişleri ───────────────────────────────────────────────────────

    def speak_bubble(self, text: str, duration: float = 4.0) -> None:
        """Râzî'nin başı üzerindeki konuşma balonunda metin gösterir."""
        self.bubble_text     = text
        self.bubble_started  = time.monotonic()
        self.bubble_duration = duration

    def _start_rhazi_turn(self) -> None:
        self.round_success = False

        if self.mode == GameMode.DUEL:
            # Düelloda tempolu ve çekişmeli dizi uzunluğu: Raunt 1: 3, Raunt 2: 4, Raunt 3+: 5 element
            seq_len = min(6, 3 + (self.duel_round - 1) // 2)
            pool = tuple(MATERIALS[:min(len(MATERIALS), 6 + self.duel_round * 2)])
            self.sequence = [random.choice(pool) for _ in range(seq_len)]
            self.recipe_name = ""
            self.player_cursors = {"player_1": 0, "player_2": 0}
            self.player_lives   = {"player_1": 3, "player_2": 3}
            self.player_stuns   = {"player_1": 0.0, "player_2": 0.0}
            self.round_winner   = None
            self.phase_cursor   = 0
            self.phase_started  = time.monotonic()
            self.state          = GameState.RHAZI_TURN
            p1_name = self.players.get("player_1", {}).get("name", "Çırak 1")
            p2_name = self.players.get("player_2", {}).get("name", "Çırak 2")
            self.last_message   = f"DÜELLO RAUNDU {self.duel_round}: {p1_name} VS {p2_name}"
            self._spawn_particles(550, 385, GOLD, 30)

            self.network.send({
                "type": "round_started",
                "mode": "duel",
                "unlocked": list(pool),
                "round_num": self.duel_round,
                "duel_scores": self.duel_scores,
                "lives": 3,
                "combo": 0,
            })
            self.speak_bubble(f"⚔️ Düello Raundu {self.duel_round}! Malzemeleri dikkatle izleyin!", duration=3.5)
            return

        unlocked = list(self.material_pool)

        # Tarihi reçete dönüm noktası kontrolü
        if self.level in HISTORICAL_RECIPES:
            rec_title, rec_elements = HISTORICAL_RECIPES[self.level]
            if all(m in unlocked for m in rec_elements):
                self.recipe_name = rec_title
                self.sequence = list(rec_elements)
            else:
                self.recipe_name = ""
                self.sequence = [random.choice(self.material_pool) for _ in range(self.sequence_length)]
        else:
            self.recipe_name = ""
            self.sequence = [random.choice(self.material_pool) for _ in range(self.sequence_length)]

        self.phase_cursor  = 0
        self.phase_started = time.monotonic()
        self.state         = GameState.RHAZI_TURN
        self.last_message  = f"Tarihi Ayin: {self.recipe_name}" if self.recipe_name else "Tabîb Ekmeleddin malzemeleri hazırlıyor..."
        self._spawn_particles(530, 385, GOLD, 22)

        # Kilidi açık malzemeleri, canı ve kombo sayısını telefona bildir
        self.network.send({
            "type": "round_started",
            "unlocked": unlocked,
            "lives": self.lives,
            "combo": self.combo,
            "recipe": self.recipe_name,
        })

        # Yeni element açıldı mı veya bilgi kartı gösterimi
        if self.recipe_name:
            self.speak_bubble(f"Tarihi Formül: '{self.recipe_name}'! Malzemeleri dikkatle izle.", duration=4.0)
        elif len(unlocked) > getattr(self, "last_unlocked_count", 0):
            new_mat = unlocked[-1]
            self.info_card_mat = new_mat
            self.info_card_until = time.monotonic() + 6.0
            self.last_unlocked_count = len(unlocked)
            self.speak_bubble(f"Seviye {self.level}! Yeni malzeme: {MATERIAL_NAMES.get(new_mat, new_mat)}", duration=3.5)
        elif self.combo >= 2:
            self.speak_bubble(f"Harika seri! 🔥 x{self.combo} Kombo! Odaklan.", duration=3.0)
        elif random.random() < 0.4:
            self.info_card_mat = random.choice(unlocked)
            self.info_card_until = time.monotonic() + 5.0
            self.speak_bubble(f"Seviye {self.level}. Sırayı dikkatle takip et!", duration=3.0)
        else:
            self.speak_bubble(f"Seviye {self.level}. Malzemeleri dikkatle izle!", duration=3.0)

    def _reset_game(self) -> None:
        self.level        = 1
        self.lives        = 3
        self.combo        = 0
        self.max_combo    = 0
        self.recipe_name  = ""
        self.sequence     = []
        self.player_index = 0
        self.phase_cursor = 0
        self.state        = GameState.WAITING_FOR_PLAYER
        self.phase_started = time.monotonic()
        self.last_message  = "Yeni oyun hazırlanıyor..."
        self._start_rhazi_turn()

    def _return_to_qr_screen(self) -> None:
        self.network.stop()
        self.room_id         = make_room_id()
        self.network         = NetworkBridge(SERVER_URL, self.room_id, self.events)
        self.qr_surface      = self._make_qr()
        self.lives           = 3
        self.sequence        = []
        self.player_index    = 0
        self.phase_cursor    = 0
        self.state           = GameState.WAITING_FOR_PLAYER
        self.phase_started   = time.monotonic()
        self.wait_started    = time.monotonic()
        self.player_connected = False
        self.last_message    = "Telefon bağlanması bekleniyor"
        self.network.start()

    # ── Özellikler ───────────────────────────────────────────────────────────

    @property
    def sequence_length(self) -> int:
        # 100 seviyeye yayılan hafıza dengesi: Seviye 1'de 3, Seviye 100'de 13 element
        return min(13, 3 + int((self.level - 1) ** 0.51))

    @property
    def material_pool(self) -> tuple[str, ...]:
        # 30 simya elementi 100 seviyeye dengeli dağıtılır:
        # Seviye 1: 4 element, Seviye 100: 30 elementin tamamı açılır.
        count = min(len(MATERIALS), 4 + (self.level - 1) * (len(MATERIALS) - 4) // 99)
        return tuple(MATERIALS[:count])

    @property
    def reveal_duration(self) -> float:
        # Râzî'nin fırlatma animasyon ritmine uygun yumuşak geçişli süre (1.4s -> 0.7s)
        return max(0.70, 1.40 - (self.level - 1) * 0.007)

    @property
    def player_duration(self) -> float:
        # Her 3 elementte bir orantılı süre artışı (hem dizi uzunluğu hem buton arama desteği):
        seq_len = len(self.sequence) if self.sequence else self.sequence_length
        pool_size = len(self.material_pool)
        base_time = 5.0
        seq_time = seq_len * 2.0
        seq_bonus = (seq_len // 3) * 3.0
        pool_bonus = max(0, (pool_size - 4) // 3) * 1.0
        return max(12.0, base_time + seq_time + seq_bonus + pool_bonus)

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def _update(self) -> None:
        now = time.monotonic()

        # Kamera sallanma offset hesapla
        elapsed_shake = now - self.shake_started
        if self.shake_started and elapsed_shake < 0.45:
            strength = int(10 * (1 - elapsed_shake / 0.45))
            self.shake_offset = (
                random.randint(-strength, strength),
                random.randint(-strength, strength),
            )
        else:
            self.shake_offset = (0, 0)

        # Ortam parçacıkları
        if now - self.ambient_clock > 0.18:
            self.ambient_clock = now
            self._spawn_particles(random.randint(170, 930), random.randint(185, 520), (160, 128, 80), 1)
        self._update_particles()

        # Timer tick → telefona saniyede bir gönder
        if self.state == GameState.PLAYER_TURN:
            remaining = max(0.0, self.player_duration - (now - self.phase_started))
            if not hasattr(self, "_last_tick") or now - self._last_tick >= 1.0:
                self._last_tick = now
                self.network.send({
                    "type": "timer_tick",
                    "remaining": round(remaining, 1),
                    "total": round(self.player_duration, 1),
                })

        # Gemini ipucu — arka plandan gelen sonucu konuşma balonunda göster
        hint_result = self.hint_engine.poll()
        if hint_result:
            self.hint_text, self.hint_material = hint_result
            self.hint_started = time.monotonic()
            self.speak_bubble(self.hint_text, duration=6.5)

        if self.state == GameState.DUEL_LOBBY:
            if self.lobby_countdown_start:
                if now - self.lobby_countdown_start >= 3.0:
                    self.lobby_countdown_start = None
                    self._start_prologue()
            return

        if self.state == GameState.RHAZI_TURN:
            if now - self.phase_started >= self.reveal_duration:
                self.phase_started = now
                self.phase_cursor += 1
                if self.phase_cursor >= len(self.sequence):
                    self.state        = GameState.PLAYER_TURN
                    self.player_index = 0
                    if self.mode == GameMode.DUEL:
                        self.player_cursors = {"player_1": 0, "player_2": 0}
                        self.player_lives   = {"player_1": 3, "player_2": 3}
                        self.player_stuns   = {"player_1": 0.0, "player_2": 0.0}
                    self.phase_started = now
                    self._last_tick   = 0.0
                    self.last_message  = "Sıra sizde! Hızlı olan kazanır!" if self.mode == GameMode.DUEL else "Sıra sende!"
                    self._spawn_particles(550 if self.mode == GameMode.DUEL else 530, 385, GREEN, 36)
                    self.network.send({
                        "type": "player_turn",
                        "mode": self.mode.value,
                        "total": round(self.player_duration, 1),
                        "seq_len": len(self.sequence),
                    })
                    self.sounds.play("tick")
                    if self.mode == GameMode.DUEL:
                        self.speak_bubble("Yarış başladı! Sırayı ilk tamamlayan raundu kazanır!", duration=3.0)
                    else:
                        self.speak_bubble("Sıra sende çırak! Malzemeleri sırayla seç.", duration=3.5)
                else:
                    mat = self.sequence[self.phase_cursor]
                    self._spawn_particles(550 if self.mode == GameMode.DUEL else 530, 385, COLORS.get(mat, GOLD), 14)
                    self.speak_bubble(f"{MATERIAL_NAMES.get(mat, mat)} ekliyorum...", duration=max(1.0, self.reveal_duration * 0.9))
        elif self.state == GameState.PLAYER_TURN and now - self.phase_started >= self.player_duration:
            if self.mode == GameMode.DUEL:
                self._duel_time_out()
            else:
                self._time_out()
        elif self.state == GameState.RESOLUTION and now - self.phase_started >= 2.4:
            if self.mode == GameMode.DUEL:
                self.duel_round += 1
                self._start_rhazi_turn()
            else:
                if self.round_success:
                    self._start_rhazi_turn()
                else:
                    self._go_game_over()
        elif self.state == GameState.GAME_OVER and now - self.game_over_time >= 120:
            self._return_to_mode_select()
        elif self.state == GameState.DUEL_MATCH_OVER and now - self.game_over_time >= 180:
            self._return_to_mode_select()
        elif self.state == GameState.WAITING_FOR_PLAYER:
            # Hiç kimse bağlanmazsa timeout — yeni oda oluştur
            if now - self.wait_started > self.PLAYER_TIMEOUT:
                self._return_to_qr_screen()

    def _time_out(self) -> None:
        self.lives -= 1
        self.combo = 0
        self.flash_color   = RED_LT
        self.flash_started = time.monotonic()
        self.shake_started = time.monotonic()
        self.fire_surge_until = time.monotonic() + 2.5
        self._spawn_particles(840, int(self.floor_y - 140), (255, 60, 20), 50)
        self.sounds.play("wrong")

        if self.lives > 0:
            self.last_message = f"Zaman doldu! ({self.lives} Can kaldı)"
            self.player_index = 0
            self.phase_started = time.monotonic()
            self._last_tick = 0.0
            self.speak_bubble(f"Vakit tükendi! Bir iksir şişen kırıldı ({self.lives} can kaldı). Sırayı tekrar dene!", duration=4.0)
            self.network.send({
                "type": "life_lost",
                "lives": self.lives,
                "combo": 0,
                "message": f"Süre tükendi! {self.lives} canın kaldı.",
                "total": round(self.player_duration, 1),
            })
            return

        self.state        = GameState.RESOLUTION
        self.round_success = False
        self.last_message  = "Süre doldu — Kazan taştı!"
        self.phase_started = time.monotonic()
        self.speak_bubble("Vakit tükendi ve tüm şişeler kırıldı! Ateş kontrolden çıktı!", duration=3.5)
        self.network.send({
            "type": "game_over",
            "message": self.last_message,
            "level": self.level,
            "max_combo": self.max_combo,
        })

    def _go_game_over(self) -> None:
        save_score(self.level, self.room_id, max_combo=self.max_combo)
        self.best         = max(self.best, self.level)
        self.final_level  = self.level
        self.state        = GameState.GAME_OVER
        self.game_over_time = time.monotonic()
        self.sounds.play("gameover")
        self.speak_bubble(f"Oyun bitti! Seviye {self.final_level}'e kadar gelebildin.", duration=4.0)

    def _duel_time_out(self) -> None:
        p1_cur = self.player_cursors.get("player_1", 0)
        p2_cur = self.player_cursors.get("player_2", 0)
        now = time.monotonic()
        self.sounds.play("wrong")
        self.fire_surge_until = now + 2.0

        if p1_cur > p2_cur:
            winner_id = "player_1"
        elif p2_cur > p1_cur:
            winner_id = "player_2"
        else:
            winner_id = None

        if winner_id:
            winner_name = self.players.get(winner_id, {}).get("name", winner_id)
            self.round_winner = winner_id
            self.duel_scores[winner_id] = self.duel_scores.get(winner_id, 0) + 1
            if self.duel_scores[winner_id] >= 3:
                self.duel_match_winner = winner_id
                self.state = GameState.DUEL_MATCH_OVER
                self.game_over_time = now
                self.speak_bubble(f"🏆 DÜELLO ŞAMPİYONU: {winner_name}! Kitâbü'l-Esrâr'ın Yeni Vârisi!", duration=7.0)
                self.network.send({
                    "type": "duel_match_over",
                    "winner_id": winner_id,
                    "winner_name": winner_name,
                    "scores": self.duel_scores,
                })
            else:
                self.state = GameState.RESOLUTION
                self.phase_started = now
                self.speak_bubble(f"Süre doldu! Daha önde olan {winner_name} raundu kazandı! ⭐", duration=3.5)
                self.network.send({
                    "type": "duel_round_won",
                    "winner_id": winner_id,
                    "winner_name": winner_name,
                    "scores": self.duel_scores,
                    "first_to": 3,
                })
        else:
            self.round_winner = None
            self.state = GameState.RESOLUTION
            self.phase_started = now
            self.speak_bubble("Süre doldu ve iki çırak da eşit kaldı! Berabere!", duration=3.5)
            self.network.send({
                "type": "duel_round_draw",
                "scores": self.duel_scores,
            })

    def _reset_duel(self) -> None:
        self.duel_scores = {"player_1": 0, "player_2": 0}
        self.duel_round = 1
        self.player_cursors = {"player_1": 0, "player_2": 0}
        self.player_lives = {"player_1": 3, "player_2": 3}
        self.player_stuns = {"player_1": 0.0, "player_2": 0.0}
        self.round_winner = None
        self.duel_match_winner = None
        self.lobby_countdown_start = None
        for p in self.players.values():
            p["ready"] = False
        self.state = GameState.DUEL_LOBBY
        self.network.send({
            "type": "duel_lobby_reset",
            "scores": self.duel_scores,
        })
        self.speak_bubble("Yeni düello için ambleminizi seçip 'Hazırım' butonuna basın.", duration=4.0)

    def _handle_duel_button(self, button: str, player_id: str) -> None:
        if player_id not in ("player_1", "player_2"):
            return
        now = time.monotonic()

        # Sersemleme kontrolü
        if now < self.player_stuns.get(player_id, 0.0):
            return

        # Can kontrolü
        if self.player_lives.get(player_id, 3) <= 0:
            return

        cur_idx = self.player_cursors.get(player_id, 0)
        if cur_idx >= len(self.sequence):
            return

        correct = self.sequence[cur_idx]
        p_info = self.players.get(player_id, {})
        p_name = p_info.get("name", player_id)
        other_id = "player_2" if player_id == "player_1" else "player_1"
        other_name = self.players.get(other_id, {}).get("name", other_id)

        px = 140 if player_id == "player_1" else 960

        if button == correct:
            self.player_cursors[player_id] = cur_idx + 1
            new_idx = self.player_cursors[player_id]
            self.sounds.play("correct")
            self._spawn_particles(px, 340, GREEN_LT, 14)

            # İlerlemeyi mobil oyunculara bildir
            self.network.send({
                "type": "duel_progress",
                "player_id": player_id,
                "cursor": new_idx,
                "total": len(self.sequence),
            })

            # Bu oyuncu diziyi ilk tamamladı mı?
            if new_idx == len(self.sequence):
                self.round_winner = player_id
                self.duel_scores[player_id] = self.duel_scores.get(player_id, 0) + 1
                self.sounds.play("level_up")
                self.flash_color = GREEN_LT
                self.flash_started = now
                self.shake_started = now
                self._spawn_particles(px, 260, GOLD, 50)

                # Maç zaferi mi (İlk 3 yıldıza ulaşan)?
                if self.duel_scores[player_id] >= 3:
                    self.duel_match_winner = player_id
                    self.state = GameState.DUEL_MATCH_OVER
                    self.game_over_time = now
                    self.speak_bubble(f"🏆 DÜELLO ŞAMPİYONU: {p_name}! Kitâbü'l-Esrâr'ın Yeni Vârisi!", duration=7.0)
                    self.network.send({
                        "type": "duel_match_over",
                        "winner_id": player_id,
                        "winner_name": p_name,
                        "scores": self.duel_scores,
                    })
                else:
                    self.state = GameState.RESOLUTION
                    self.phase_started = now
                    self.speak_bubble(f"⭐ Raunt {p_name}'in! (+1 Yıldız)", duration=3.5)
                    self.network.send({
                        "type": "duel_round_won",
                        "winner_id": player_id,
                        "winner_name": p_name,
                        "scores": self.duel_scores,
                        "first_to": 3,
                    })

        else:
            # Yanlış basım: Can kaybı + Sersemleme + Sıfırlanma
            self.player_lives[player_id] -= 1
            self.player_cursors[player_id] = 0
            self.player_stuns[player_id] = now + 1.2
            self.sounds.play("wrong")
            self._spawn_particles(px, 340, RED_LT, 30)

            # Bu oyuncuya sersemletme mesajı
            self.network.send({
                "type": "stunned",
                "target": player_id,
                "player_id": player_id,
                "duration": 1.2,
                "lives": self.player_lives[player_id],
                "message": "⚡ Yanlış malzeme! 1.2s Sersemledin ve sıra başa döndü!",
            })
            # Rakibe haber ver
            self.network.send({
                "type": "opponent_mistake",
                "target": other_id,
                "player_id": player_id,
                "message": f"Rakip {p_name} hata yaptı ve sersemledi!",
            })

            # Tüm canlar bitti mi?
            if self.player_lives[player_id] <= 0:
                self.round_winner = other_id
                self.duel_scores[other_id] = self.duel_scores.get(other_id, 0) + 1
                self.sounds.play("level_up")
                self._spawn_particles(px, 300, RED, 45)

                if self.duel_scores[other_id] >= 3:
                    self.duel_match_winner = other_id
                    self.state = GameState.DUEL_MATCH_OVER
                    self.game_over_time = now
                    self.speak_bubble(f"🏆 {p_name} elendi! Şampiyon: {other_name}!", duration=6.0)
                    self.network.send({
                        "type": "duel_match_over",
                        "winner_id": other_id,
                        "winner_name": other_name,
                        "scores": self.duel_scores,
                    })
                else:
                    self.state = GameState.RESOLUTION
                    self.phase_started = now
                    self.speak_bubble(f"{p_name} elendi! Raundu {other_name} kazandı! ⭐", duration=3.5)
                    self.network.send({
                        "type": "duel_round_won",
                        "winner_id": other_id,
                        "winner_name": other_name,
                        "scores": self.duel_scores,
                        "first_to": 3,
                    })

    # ── Buton işleme ─────────────────────────────────────────────────────────

    def _handle_button(self, button: str, player_id: str = "player_1") -> None:
        if self.mode == GameMode.DUEL:
            self._handle_duel_button(button, player_id)
            return

        if button != self.sequence[self.player_index]:
            correct = self.sequence[self.player_index]
            self.lives -= 1
            self.combo = 0
            name = MATERIAL_NAMES.get(button, button or "bilinmiyor")
            correct_tr = MATERIAL_NAMES.get(correct, correct)
            self.flash_color   = RED_LT
            self.flash_started = time.monotonic()
            self.shake_started = time.monotonic()
            self.fire_surge_until = time.monotonic() + 2.5
            self._spawn_particles(840, int(self.floor_y - 140), (255, 60, 20), 55)
            self.sounds.play("wrong")

            # Gemini'den Râzî karakteriyle ipucu iste
            self.hint_engine.request(correct, button, self.level)

            if self.lives > 0:
                self.last_message  = f"Yanlış! '{name}' seçildi ({self.lives} Can kaldı)"
                self.player_index = 0
                self.phase_started = time.monotonic()
                self._last_tick = 0.0
                self.speak_bubble(f"Dikkat et çırak! Doğrusu {correct_tr} idi. ({self.lives} can kaldı, sırayı baştan dene!)", duration=4.0)
                self.network.send({
                    "type": "life_lost",
                    "lives": self.lives,
                    "combo": 0,
                    "message": f"Yanlış seçim! {self.lives} canın kaldı.",
                    "total": round(self.player_duration, 1),
                })
                return

            self.state        = GameState.RESOLUTION
            self.round_success = False
            self.last_message  = f"Yanlış! '{name}' seçildi."
            self.phase_started = time.monotonic()
            self.network.send({
                "type": "game_over",
                "message": self.last_message,
                "level": self.level,
                "max_combo": self.max_combo,
            })
            self.speak_bubble(f"Eyvah! Tüm şişeler kırıldı, ayin bozuldu! Doğrusu {correct_tr} idi!", duration=3.5)
            return

        self.player_index += 1
        self._spawn_particles(560, 350, GREEN, 12)
        self.sounds.play("correct")

        # Malzeme notu göster
        self.note_material = self.sequence[self.player_index - 1]
        self.note_started  = time.monotonic()

        if self.player_index == len(self.sequence):
            self.state        = GameState.RESOLUTION
            self.round_success = True
            self.combo        += 1
            self.max_combo    = max(self.max_combo, self.combo)
            self.last_message  = "Doğru! Tabîb Ekmeleddin onaylıyor."
            self.phase_started = time.monotonic()
            self.level        += 1
            self.flash_color   = GREEN_LT
            self.flash_started = self.phase_started
            self._spawn_particles(560, 350, GOLD, 60)
            self.sounds.play("level_up")

            self.network.send({
                "type": "round_success",
                "level": self.level,
                "combo": self.combo,
                "lives": self.lives,
                "recipe_completed": bool(self.recipe_name),
            })

            life_msg = ""
            if self.level in (26, 51, 76) and self.lives < 3:
                self.lives += 1
                life_msg = " · 🧪 +1 Can Yenilendi!"
                self.network.send({"type": "life_gained", "lives": self.lives})

            combo_msg = f" · 🔥 x{self.combo} Kombo!" if self.combo >= 2 else ""
            self.last_message = f"Doğru! Tabîb Ekmeleddin onaylıyor.{combo_msg}"
            self.speak_bubble(f"Mükemmel! Seviye {self.level}'e geçtik.{life_msg}{combo_msg}", duration=3.5)
        else:
            remaining_count = len(self.sequence) - self.player_index
            self.last_message = f"Doğru  ·  {remaining_count} malzeme kaldı"

    # ── Çizim ────────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self.pixel_surface.fill(BG)
        self.pixel_surface.blit(self.background, (0, 0))

        if self.state == GameState.MODE_SELECT:
            self._draw_mode_select_screen()
        elif self.state == GameState.PROLOGUE:
            self._draw_prologue_screen()
        elif self.state == GameState.CREDITS_VIEW:
            self._draw_credits_screen()
        elif self.state == GameState.WAITING_FOR_PLAYER:
            self._draw_waiting_screen()
        elif self.state == GameState.DUEL_LOBBY:
            self._draw_duel_lobby()
        elif self.mode == GameMode.DUEL:
            self._draw_duel_sprites()
            self._draw_duel_hud()
            if self.state == GameState.RHAZI_TURN:
                material = self.sequence[self.phase_cursor]
                self._draw_duel_material_animation(material)
                self._draw_material_label(material, 360, 560)
            elif self.state == GameState.PLAYER_TURN:
                remaining = max(0.0, self.player_duration - (time.monotonic() - self.phase_started))
                self._draw_duel_timer_bar(remaining)
            elif self.state == GameState.RESOLUTION:
                w_name = self.players.get(self.round_winner, {}).get("name", "Berabere") if self.round_winner else "BERABERE"
                badge_text = f"RAUNT: {w_name.upper()} KAZANDI!" if self.round_winner else "RAUNT BERABERE"
                self._draw_status_badge(badge_text, GOLD if self.round_winner else TEXT_DIM)
            elif self.state == GameState.DUEL_MATCH_OVER:
                self._draw_duel_match_over()
        else:
            self._draw_sprites()
            self._draw_game_header()
            if self.state == GameState.RHAZI_TURN:
                material = self.sequence[self.phase_cursor]
                self._draw_material_animation(material)
                self._draw_material_label(material, 50, 560)
            elif self.state == GameState.PLAYER_TURN:
                remaining = max(0.0, self.player_duration - (time.monotonic() - self.phase_started))
                self._draw_timer_bar(remaining)
                self._draw_player_prompt()
            elif self.state == GameState.RESOLUTION:
                ok = "Doğru" in self.last_message
                self._draw_status_badge("KAYIT ALINDI" if ok else "HATA", GREEN if ok else RED)
            elif self.state == GameState.GAME_OVER:
                self._draw_game_over_screen()

        # Malzeme notu kartı (üst katman)
        if self.note_material and time.monotonic() - self.note_started < self.NOTE_DURATION:
            self._draw_note_card(self.note_material)
        else:
            self.note_material = None

        # Gemini ipucu banner (üst katman — malzeme kartının altında)
        if self.hint_text and time.monotonic() - self.hint_started < self.hint_engine.HINT_DURATION:
            self._draw_hint_banner(self.hint_text, self.hint_material)
        else:
            self.hint_text = None

        # Râzî konuşma balonu & Tarihi bilgi kartı
        self._draw_speech_bubble()
        self._draw_info_card()

        self._draw_particles()
        self._draw_flash()

        # Kamera sallanması — final blit
        ox, oy = self.shake_offset
        self.screen.fill(SHADOW)
        self.screen.blit(self.pixel_surface, (ox, oy))

    def _draw_sprites(self) -> None:
        now = time.monotonic()
        frame_idx = 0
        if self.state == GameState.RHAZI_TURN:
            progress = (now - self.phase_started) / self.reveal_duration
            # Her elini kaldırdığında 1 malzeme atacak biçimde:
            # 0.10 <= progress < 0.70 aralığında elini kaldırır (Frame 5)
            if 0.10 <= progress < 0.70:
                frame_idx = 5  # Elini havaya kaldırdığı kare
            elif progress < 0.10:
                frame_idx = 1  # Masaya uzanıp malzemeyi alma karesi
            else:
                frame_idx = 0  # Malzemeyi fırlattıktan sonra bekleme / izleme karesi
        else:
            # Boşta (Idle) çalışma animasyonu: masadaki malzemeleri inceler
            idle_loop = [0, 0, 1, 1, 2, 2, 1, 0, 3, 3, 4, 4, 3, 0]
            frame_idx = idle_loop[int(now * 3) % len(idle_loop)]

        master_frame = self.anim_master.get_frame_at(frame_idx)
        if master_frame:
            # Râzî ve simya masası (genişlik 280px)
            self._blit_on_floor(master_frame, 220)

        # Kazan ve Ateş Çizimi
        is_surge = now < self.fire_surge_until
        anim_obj = self.anim_forge_surge if is_surge else self.anim_forge
        fps = 10 if is_surge else 6
        forge_frame = anim_obj.get_frame(now, fps)
        if forge_frame:
            # Sade döküm kazan, canlı ateş ve duman
            self._blit_on_floor(forge_frame, 840)

        # Aşırı alevlenme anında etrafa sıçrayan köz ve kıvılcım parçacıkları
        if is_surge and random.random() < 0.7:
            self._spawn_particles(
                random.randint(805, 875),
                int(self.floor_y - random.randint(80, 190)),
                random.choice([(255, 140, 20), (255, 50, 10), (255, 230, 60), (220, 30, 10)]),
                3
            )

    def _blit_on_floor(self, frame: pygame.Surface, center_x: int) -> None:
        bounds = frame.get_bounding_rect()
        rect   = frame.get_rect(midtop=(center_x, self.floor_y - bounds.bottom))
        self.pixel_surface.blit(frame, rect)

    # ── Bekleme ekranı ───────────────────────────────────────────────────────

    def _draw_waiting_screen(self) -> None:
        # Başlık
        mode_title = "1v1 ÇIRAK DÜELLOSU" if self.mode == GameMode.DUEL else "TEK KİŞİLİK MACERA"
        self._text_shadow(f"TABÎB EKMELEDDİN'İN KAZANI · {mode_title}", self.font_title, GOLD, (50, 44))
        self._text("Mobil cihazınızdan QR kodu okutarak ayine bağlanın", self.font_body, TEXT_DIM, (52, 88))

        # QR kutusu (Sol Panel)
        panel_rect = pygame.Rect(48, 140, 368, 470)
        self._draw_panel(panel_rect, radius=14)
        qr_scaled = pygame.transform.scale(self.qr_surface, (296, 296))
        pygame.draw.rect(self.pixel_surface, (245, 245, 245), (72, 160, 296, 296), border_radius=6)
        self.pixel_surface.blit(qr_scaled, (72, 160))
        self._text("ODA KODU", self.font_small, TEXT_DIM, (100, 474))
        self._text(self.room_id, self.font_large, GOLD_LT, (80, 496))
        self._text(f"Aynı Wi-Fi ağında: {PLAY_URL}/{self.room_id}"[:50], self.font_tiny, TEXT_DIM, (60, 534))
        self._text("Kamera ile QR kodu okutun", self.font_body, GOLD, (108, 564))

        # Sağ panel
        rx = 452
        if self.mode == GameMode.DUEL:
            self._text_shadow("DÜELLO BAĞLANTI DURUMU", self.font_medium, GOLD, (rx, 150))
            self._draw_separator(rx, 178, 1020)

            p1_info = self.players.get("player_1", {})
            p2_info = self.players.get("player_2", {})
            p1_conn = p1_info.get("connected", False)
            p2_conn = p2_info.get("connected", False)
            conn_count = (1 if p1_conn else 0) + (1 if p2_conn else 0)

            # Çırak 1 Kutusu
            b1 = pygame.Rect(rx, 196, 568, 64)
            self._draw_panel(b1, radius=10)
            pygame.draw.rect(self.pixel_surface, GREEN if p1_conn else BORDER, b1, 2, border_radius=10)
            self._text("☿", self.font_symbol_large, GOLD_LT if p1_conn else TEXT_DIM, (rx + 16, 210))
            self._text(f"1. ÇIRAK: {p1_info.get('name', 'Çırak 1')}", self.font_body_bold, TEXT, (rx + 56, 206))
            status_p1 = "✅ Bağlandı — Hazır" if p1_conn else "⏳ QR Kodu Okutması Bekleniyor..."
            self._text(status_p1, self.font_body, GREEN if p1_conn else GOLD, (rx + 56, 230))

            # Çırak 2 Kutusu
            b2 = pygame.Rect(rx, 274, 568, 64)
            self._draw_panel(b2, radius=10)
            pygame.draw.rect(self.pixel_surface, GREEN if p2_conn else BORDER, b2, 2, border_radius=10)
            self._text("🜍", self.font_symbol_large, GOLD_LT if p2_conn else TEXT_DIM, (rx + 16, 288))
            self._text(f"2. ÇIRAK: {p2_info.get('name', 'Çırak 2')}", self.font_body_bold, TEXT, (rx + 56, 284))
            status_p2 = "✅ Bağlandı — Hazır" if p2_conn else "⏳ 2. Telefon Bekleniyor (Aynı QR'ı okutun)..."
            self._text(status_p2, self.font_body, GREEN if p2_conn else GOLD, (rx + 56, 308))

            # Bilgilendirme
            self._draw_separator(rx, 356, 1020)
            self._text(f"Durum: {conn_count} / 2 Çırak Bağlandı", self.font_body_bold, GOLD_LT, (rx, 370))
            self._text("• İki oyuncu da bağlandığında 1v1 Düello Lobisi açılacaktır.", self.font_body, TEXT_DIM, (rx, 398))
            self._text("• Her iki oyuncu da kendi telefonundan amblem seçip yarışır.", self.font_body, TEXT_DIM, (rx, 424))
            self._text("• İlk 3 raundu (yıldızı) kazanan şampiyon olur!", self.font_body, TEXT_DIM, (rx, 450))

        else:
            self._text_shadow("NASIL OYNANIR", self.font_medium, GOLD, (rx, 150))
            self._draw_separator(rx, 178, 1020)

            steps = [
                ("1", "QR kodu telefonunun kamerasıyla tara ve bağlan."),
                ("2", "Tabîb Ekmeleddin'in talimatlarını dinle ve 'BAŞLA'ya bas."),
                ("3", "Bey Hekim kazana şifalı cevherleri atarken sırayı aklında tut."),
                ("4", "Sıra sana geldiğinde telefondan aynı sırayla ekle."),
                ("5", "3 can hakkın var. Her 3 elementte bir süren uzar!"),
            ]
            sy = 196
            for num, text in steps:
                pygame.draw.circle(self.pixel_surface, GOLD, (rx + 12, sy + 10), 10)
                pygame.draw.circle(self.pixel_surface, PANEL, (rx + 12, sy + 10), 8)
                self._text(num, self.font_tiny, GOLD, (rx + 8, sy + 4))
                self._text(text, self.font_body, TEXT, (rx + 32, sy))
                sy += 36

            # En iyi skor
            self._draw_separator(rx, sy + 10, 1020)
            best_str = f"SEVİYE  {self.best:02d}" if self.best > 0 else "—"
            self._text(f"EN İYİ REKOR: {best_str}", self.font_body_bold, GOLD_LT, (rx, sy + 24))

        # Alt Butonlar
        # Buton 1: Mod Seçimine Dön
        btn_back = pygame.Rect(rx, 530, 260, 42)
        self._draw_panel(btn_back, radius=8)
        pygame.draw.rect(self.pixel_surface, BORDER, btn_back, 2, border_radius=8)
        self._text_center("◀ Mod Seçimi (ESC)", self.font_body_bold, TEXT, btn_back.centerx, btn_back.y + 11)

        # Buton 2: Kulüp & Künye
        btn_cred = pygame.Rect(rx + 280, 530, 260, 42)
        self._draw_panel(btn_cred, radius=8)
        pygame.draw.rect(self.pixel_surface, GOLD, btn_cred, 2, border_radius=8)
        self._text_center("🏛️ Kulüp & Künye (C)", self.font_body_bold, GOLD_LT, btn_cred.centerx, btn_cred.y + 11)

    # ── Oyun başlık çubuğu ───────────────────────────────────────────────────

    def _draw_game_header(self) -> None:
        # Üst bar
        pygame.draw.rect(self.pixel_surface, PANEL, (0, 0, WIDTH, 78))
        pygame.draw.line(self.pixel_surface, BORDER, (0, 78), (WIDTH, 78), 2)

        # Seviye
        self._text("SEVİYE", self.font_tiny, TEXT_DIM, (24, 14))
        self._text_shadow(f"{self.level:02d}", self.font_title, GOLD_LT, (24, 30))

        # Canlar (3 İksir Şişesi)
        self._text("CAN", self.font_tiny, TEXT_DIM, (110, 14))
        for i in range(3):
            fx = 110 + i * 22
            fy = 32
            active = i < self.lives
            flask_col = GREEN if active else (65, 42, 34)
            # Gövde
            pygame.draw.circle(self.pixel_surface, flask_col, (fx + 8, fy + 12), 7)
            # Boyun
            pygame.draw.rect(self.pixel_surface, flask_col, (fx + 6, fy + 2, 4, 6))
            # Tıpa
            pygame.draw.rect(self.pixel_surface, GOLD if active else (50, 32, 24), (fx + 5, fy, 6, 3), border_radius=1)
            # Parlama
            if active:
                pygame.draw.circle(self.pixel_surface, (255, 255, 255), (fx + 6, fy + 10), 2)

        # Kombo Rozeti (2 veya daha fazla doğru tur)
        if self.combo >= 2:
            cx = 184
            cw = 84
            pygame.draw.rect(self.pixel_surface, (60, 26, 14), (cx, 22, cw, 28), border_radius=4)
            pygame.draw.rect(self.pixel_surface, GOLD, (cx, 22, cw, 28), 1, border_radius=4)
            self._text(f"{self.combo}x SERİ", self.font_small, GOLD_LT, (cx + 10, 31))

        # Mesaj ve Tarihi Reçete — ortada
        if self.recipe_name:
            self._text_center(f"KADİM REÇETE: {self.recipe_name.upper()}", self.font_small, GOLD_LT, WIDTH // 2, 16)
            self._text_center(self.last_message, self.font_medium, TEXT, WIDTH // 2, 40)
        else:
            msg_surface = self.font_large.render(self.last_message, True, TEXT)
            msg_x = (WIDTH - msg_surface.get_width()) // 2
            self.pixel_surface.blit(msg_surface, (msg_x, 26))

        # En iyi skor — sağda
        self._text("EN İYİ", self.font_tiny, TEXT_DIM, (WIDTH - 120, 14))
        self._text(f"{self.best:02d}", self.font_large, GOLD, (WIDTH - 120, 30))

    # ── Malzeme animasyonu ───────────────────────────────────────────────────

    def _draw_material_animation(self, material: str) -> None:
        now = time.monotonic()
        progress = min(1.0, (now - self.phase_started) / self.reveal_duration)

        # Râzî'nin kalkan eli (center_x = 220, frame_w = 280, hand offset = 104, 58)
        # Top-left = (80, self.floor_y - 230) -> Hand = (184, self.floor_y - 172)
        hand_x = 184
        hand_y = int(self.floor_y - 172)
        # Yeni simya kazanı ağzı (center_x = 840, mouth_y = floor_y - 188)
        target_x = 840
        target_y = int(self.floor_y - 188)

        color    = COLORS[material]
        color_lt = COLORS_LT[material]

        if progress < 0.10:
            # Henüz elinde tutuyor / masadan kaldırıyor
            x = hand_x - 8
            y = hand_y + 16
        elif progress < 0.70:
            # Uçuş safhası: el kalkıkken kazana doğru parabolik yay çizer
            t = (progress - 0.10) / 0.60
            x = int(hand_x + (target_x - hand_x) * t)
            arc = 160 * 4 * t * (1 - t)
            y = int(hand_y + (target_y - hand_y) * t - arc)
            # Uçuş izi parçacıkları
            if random.random() < 0.45:
                self._spawn_particles(x, y, color_lt, 1)
        else:
            # Kazana düştü
            x = target_x
            y = target_y
            if 0.70 <= progress < 0.78:
                # Malzeme-spesifik simyasal alev ve kıvılcım reaksiyonu
                if material in ("bakir", "tenkar", "zumrut"):
                    spark_color = (50, 240, 140)
                elif material in ("kukurt", "zirnik", "altin", "bal"):
                    spark_color = (255, 235, 50)
                elif material in ("civa", "gumus", "kursun", "inci"):
                    spark_color = (195, 235, 255)
                elif material in ("tuz", "kirec", "kafur", "saf_tuz"):
                    spark_color = (255, 255, 255)
                elif material in ("zac", "sirke", "buyuk_iksir", "afyon"):
                    spark_color = (230, 80, 245)
                else:
                    spark_color = color_lt
                self._spawn_particles(target_x, target_y - 12, spark_color, 8)
                self._spawn_particles(target_x, target_y, color, 6)

        pw, ph   = 26, 36
        bottle   = pygame.Surface((pw, ph), pygame.SRCALPHA)
        # Gövde
        pygame.draw.rect(bottle, color, (4, 16, 18, 18), border_radius=5)
        pygame.draw.rect(bottle, color_lt, (4, 14, 18, 8), border_radius=3)
        # Cam parlaması
        pygame.draw.rect(bottle, (220, 235, 245, 120), (4, 10, 18, 24), 2, border_radius=5)
        pygame.draw.rect(bottle, (255, 255, 255, 80), (7, 12, 5, 10), border_radius=2)
        # Boyun
        pygame.draw.rect(bottle, (190, 210, 225, 200), (9, 4, 8, 9), 2)
        pygame.draw.rect(bottle, color, (11, 7, 4, 5))
        # Tıpa
        pygame.draw.rect(bottle, (130, 90, 50), (10, 0, 7, 5), border_radius=2)

        if progress < 0.72:
            self.pixel_surface.blit(bottle, (x - pw // 2, y - ph // 2))

        # Kazan içine düşüş anında parıltı ve alevlenme efekti
        if 0.70 <= progress < 0.95:
            glow_alpha = int((1.0 - (progress - 0.70) / 0.25) * 160)
            glow_surf = pygame.Surface((140, 70), pygame.SRCALPHA)
            pygame.draw.ellipse(glow_surf, (*color, glow_alpha), (10, 10, 120, 50))
            pygame.draw.ellipse(glow_surf, (*color_lt, min(255, glow_alpha + 60)), (30, 18, 80, 34))
            self.pixel_surface.blit(glow_surf, (target_x - 70, target_y - 35), special_flags=pygame.BLEND_ADD)

        if 0.70 <= progress <= 0.76 and not getattr(self, "_drop_sound_played", False):
            self._drop_sound_played = True
            self.sounds.play("drop")
        elif progress < 0.70:
            self._drop_sound_played = False

    def _draw_material_label(self, material: str, x: int, y: int) -> None:
        """Alt bölgede malzeme adını sembolle birlikte göster."""
        bar_h = 52
        pygame.draw.rect(self.pixel_surface, PANEL, (0, y - 4, WIDTH, bar_h), border_radius=0)
        pygame.draw.line(self.pixel_surface, BORDER, (0, y - 4), (WIDTH, y - 4), 1)
        color = COLORS[material]
        # Renk nokta
        pygame.draw.circle(self.pixel_surface, color, (x + 12, y + 22), 10)
        pygame.draw.circle(self.pixel_surface, COLORS_LT[material], (x + 9, y + 19), 4)
        self._text("MALZEME", self.font_tiny, TEXT_DIM, (x + 30, y + 6))
        self._text_shadow(MATERIAL_NAMES[material].upper(), self.font_large, color, (x + 30, y + 22))

    # ── Oyuncu istemi ────────────────────────────────────────────────────────

    def _draw_player_prompt(self) -> None:
        bar_h = 52
        y     = 556
        pygame.draw.rect(self.pixel_surface, PANEL, (0, y - 4, WIDTH, bar_h))
        pygame.draw.line(self.pixel_surface, BORDER, (0, y - 4), (WIDTH, y - 4), 1)
        self._text("SIRA SENDE", self.font_tiny, TEXT_DIM, (24, y + 6))
        self._text_shadow(
            f"{self.player_index + 1}. MALZEME SEÇİLİYOR",
            self.font_large, GREEN, (24, y + 22)
        )
        # Sağda sıra indikatörü
        dots_x = WIDTH - 24 - len(self.sequence) * 14
        for i, mat in enumerate(self.sequence):
            c = COLORS[mat] if i < self.player_index else (PANEL_LT if i == self.player_index else PANEL)
            pygame.draw.rect(self.pixel_surface, c, (dots_x + i * 14, y + 18, 10, 10), border_radius=3)
            if i == self.player_index:
                pygame.draw.rect(self.pixel_surface, GREEN, (dots_x + i * 14, y + 18, 10, 10), 2, border_radius=3)

    # ── Timer bar ────────────────────────────────────────────────────────────

    def _draw_timer_bar(self, remaining: float) -> None:
        ratio = max(0.0, remaining / self.player_duration)
        bar_rect = pygame.Rect(24, 620, WIDTH - 48, 10)
        pygame.draw.rect(self.pixel_surface, PANEL_LT, bar_rect, border_radius=5)
        fill_w = int(bar_rect.width * ratio)
        if fill_w > 0:
            color = GREEN if ratio > 0.4 else (GOLD if ratio > 0.2 else RED)
            pygame.draw.rect(self.pixel_surface, color,
                             pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.height),
                             border_radius=5)
        # Süre metni
        secs = int(remaining) + 1
        self._text(f"{secs}s", self.font_tiny, TEXT_DIM, (bar_rect.right + 6, bar_rect.y - 1))

    # ── Durum rozeti ─────────────────────────────────────────────────────────

    def _draw_status_badge(self, label: str, color: tuple) -> None:
        s = self.font_large.render(label, True, color)
        x = (WIDTH - s.get_width()) // 2
        pygame.draw.rect(self.pixel_surface, PANEL,
                         (x - 18, 560, s.get_width() + 36, 36), border_radius=8)
        pygame.draw.rect(self.pixel_surface, color,
                         (x - 18, 560, s.get_width() + 36, 36), 2, border_radius=8)
        self.pixel_surface.blit(s, (x, 567))

    # ── Oyun bitti ekranı ────────────────────────────────────────────────────

    def _draw_game_over_screen(self) -> None:
        # Karartma
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 185))
        self.pixel_surface.blit(overlay, (0, 0))

        cx = WIDTH // 2
        # Kutu
        box = pygame.Rect(cx - 280, 95, 560, 480)
        self._draw_panel(box, radius=16)
        pygame.draw.rect(self.pixel_surface, RED, box, 2, border_radius=16)

        # Başlık
        self._text_center("AYİN SONA ERDİ", self.font_title, RED_LT, cx, 120)
        self._draw_separator(box.x + 20, 155, box.right - 20)

        # Ulaşılan seviye
        self._text_center("ULAŞILAN SEVİYE", self.font_tiny, TEXT_DIM, cx, 170)
        self._text_center(f"{self.final_level:02d}", self.font_title, GOLD_LT, cx, 192)

        # Simyacı Unvanı
        title = get_alchemical_title(self.final_level)
        self._text_center(title, self.font_medium, GOLD, cx, 228)

        # Kombo & En iyi skor
        self._draw_separator(box.x + 20, 258, box.right - 20)
        combo_str = f"EN ÇOK SERİ: {self.max_combo}x" if self.max_combo >= 2 else "SERİ: —"
        self._text_center(f"{combo_str}   ·   EN İYİ: {self.best:02d}", self.font_small, TEXT, cx, 276)

        # Buton 1 & 2: Tekrar Oyna & Mod Seçimi
        b_retry = pygame.Rect(cx - 240, 316, 220, 42)
        self._draw_panel(b_retry, radius=8)
        pygame.draw.rect(self.pixel_surface, GREEN, b_retry, 2, border_radius=8)
        self._text_center("🔄 Tekrar Oyna (SPACE)", self.font_body_bold, GREEN_LT, b_retry.centerx, b_retry.y + 11)

        b_mode = pygame.Rect(cx + 20, 316, 220, 42)
        self._draw_panel(b_mode, radius=8)
        pygame.draw.rect(self.pixel_surface, BORDER, b_mode, 2, border_radius=8)
        self._text_center("◀ Mod Seçimi (M)", self.font_body_bold, TEXT, b_mode.centerx, b_mode.y + 11)

        # Buton 3: Kulüp & Künye & Risale
        b_cred = pygame.Rect(cx - 200, 374, 400, 42)
        self._draw_panel(b_cred, radius=8)
        pygame.draw.rect(self.pixel_surface, GOLD, b_cred, 2, border_radius=8)
        self._text_center("🏛️ Kulüp, Künye & Risale (C)", self.font_body_bold, GOLD_LT, b_cred.centerx, b_cred.y + 11)

        # İpuçları
        self._draw_separator(box.x + 20, 436, box.right - 20)
        self._text_center("Telefondan 'Yeni Oyun' butonuna basabilir veya ekrandan seçebilirsiniz", self.font_tiny, TEXT_DIM, cx, 452)
        secs_left = max(0, 120 - int(time.monotonic() - self.game_over_time))
        self._text_center(f"veya {secs_left}s sonra otomatik ana menüye döner", self.font_tiny, TEXT_DIM, cx, 474)

    # ── Düello Çizim Metotları ───────────────────────────────────────────────

    def _draw_duel_sprites(self) -> None:
        now = time.monotonic()
        frame_idx = 0
        if self.state == GameState.RHAZI_TURN:
            progress = (now - self.phase_started) / self.reveal_duration
            if 0.10 <= progress < 0.70:
                frame_idx = 5
            elif progress < 0.10:
                frame_idx = 1
            else:
                frame_idx = 0
        else:
            idle_loop = [0, 0, 1, 1, 2, 2, 1, 0, 3, 3, 4, 4, 3, 0]
            frame_idx = idle_loop[int(now * 3) % len(idle_loop)]

        master_frame = self.anim_master.get_frame_at(frame_idx)
        if master_frame:
            self._blit_on_floor(master_frame, 420)

        is_surge = now < self.fire_surge_until
        anim_obj = self.anim_forge_surge if is_surge else self.anim_forge
        fps = 10 if is_surge else 6
        forge_frame = anim_obj.get_frame(now, fps)
        if forge_frame:
            self._blit_on_floor(forge_frame, 680)

        if is_surge and random.random() < 0.7:
            self._spawn_particles(
                random.randint(645, 715),
                int(self.floor_y - random.randint(80, 190)),
                random.choice([(255, 140, 20), (255, 50, 10), (255, 230, 60), (220, 30, 10)]),
                3
            )

    def _draw_duel_material_animation(self, material: str) -> None:
        now = time.monotonic()
        progress = min(1.0, (now - self.phase_started) / self.reveal_duration)

        hand_x = 384
        hand_y = int(self.floor_y - 172)
        target_x = 680
        target_y = int(self.floor_y - 188)

        color    = COLORS[material]
        color_lt = COLORS_LT[material]

        if progress < 0.10:
            x = hand_x - 8
            y = hand_y + 16
        elif progress < 0.70:
            t = (progress - 0.10) / 0.60
            x = int(hand_x + (target_x - hand_x) * t)
            arc = 120 * 4 * t * (1 - t)
            y = int(hand_y + (target_y - hand_y) * t - arc)
            if random.random() < 0.45:
                self._spawn_particles(x, y, color_lt, 1)
        else:
            x = target_x
            y = target_y
            if 0.70 <= progress < 0.78:
                self._spawn_particles(target_x, target_y - 12, color_lt, 8)
                self._spawn_particles(target_x, target_y, color, 6)

        pw, ph = 26, 36
        bottle = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pygame.draw.rect(bottle, color, (4, 16, 18, 18), border_radius=5)
        pygame.draw.rect(bottle, color_lt, (4, 14, 18, 8), border_radius=3)
        pygame.draw.rect(bottle, (220, 235, 245, 120), (4, 10, 18, 24), 2, border_radius=5)
        pygame.draw.rect(bottle, (255, 255, 255, 80), (7, 12, 5, 10), border_radius=2)
        pygame.draw.rect(bottle, (190, 210, 225, 200), (9, 4, 8, 9), 2)
        pygame.draw.rect(bottle, color, (11, 7, 4, 5))
        pygame.draw.rect(bottle, (130, 90, 50), (10, 0, 7, 5), border_radius=2)

        if progress < 0.72:
            self.pixel_surface.blit(bottle, (x - pw // 2, y - ph // 2))

        if 0.70 <= progress < 0.95:
            glow_alpha = int((1.0 - (progress - 0.70) / 0.25) * 160)
            glow_surf = pygame.Surface((140, 70), pygame.SRCALPHA)
            pygame.draw.ellipse(glow_surf, (*color, glow_alpha), (10, 10, 120, 50))
            pygame.draw.ellipse(glow_surf, (*color_lt, min(255, glow_alpha + 60)), (30, 18, 80, 34))
            self.pixel_surface.blit(glow_surf, (target_x - 70, target_y - 35), special_flags=pygame.BLEND_ADD)

        if 0.70 <= progress <= 0.76 and not getattr(self, "_drop_sound_played", False):
            self._drop_sound_played = True
            self.sounds.play("drop")
        elif progress < 0.70:
            self._drop_sound_played = False

    def _draw_duel_hud(self) -> None:
        pygame.draw.rect(self.pixel_surface, PANEL, (0, 0, WIDTH, 78))
        pygame.draw.line(self.pixel_surface, BORDER, (0, 78), (WIDTH, 78), 2)

        self._text_center(f"⚔️ 1v1 ÇIRAK DÜELLOSU  ·  RAUNT {self.duel_round}", self.font_small, GOLD_LT, WIDTH // 2, 14)
        s1 = self.duel_scores.get("player_1", 0)
        s2 = self.duel_scores.get("player_2", 0)
        score_str = f"{s1}  —  {s2}"
        self._text_center(score_str, self.font_large, TEXT, WIDTH // 2, 34)
        self._text_center(self.last_message, self.font_tiny, TEXT_DIM, WIDTH // 2, 56)

        now = time.monotonic()

        # ── 1. Çırak Paneli (Sol) ──
        p1 = self.players.get("player_1", {"name": "Çırak 1", "emblem": "☿"})
        p1_box = pygame.Rect(16, 88, 256, 460)
        self._draw_panel(p1_box, radius=12)
        pygame.draw.rect(self.pixel_surface, BORDER, p1_box, 1, border_radius=12)

        self._text("1. ÇIRAK", self.font_tiny, TEXT_DIM, (p1_box.x + 16, p1_box.y + 14))
        p1_name = p1.get("name", "Çırak 1")[:14]
        self._text_shadow(p1_name, self.font_large, GOLD_LT, (p1_box.x + 16, p1_box.y + 30))
        emb_surf = self.font_symbol_large.render(p1.get("emblem", "☿"), True, GOLD)
        self.pixel_surface.blit(emb_surf, (p1_box.right - 44, p1_box.y + 16))

        self._draw_separator(p1_box.x + 14, p1_box.y + 60, p1_box.right - 14)

        self._text("RAUNT YILDIZLARI", self.font_tiny, TEXT_DIM, (p1_box.x + 16, p1_box.y + 72))
        for i in range(3):
            sx = p1_box.x + 20 + i * 40
            sy = p1_box.y + 88
            has_star = i < s1
            star_col = GOLD_LT if has_star else (60, 44, 34)
            star_txt = "★" if has_star else "☆"
            star_s = self.font_symbol_large.render(star_txt, True, star_col)
            self.pixel_surface.blit(star_s, (sx, sy))

        self._draw_separator(p1_box.x + 14, p1_box.y + 130, p1_box.right - 14)

        p1_lives = self.player_lives.get("player_1", 3)
        self._text("RAUNT HAKKI", self.font_tiny, TEXT_DIM, (p1_box.x + 16, p1_box.y + 142))
        for i in range(3):
            fx = p1_box.x + 20 + i * 32
            fy = p1_box.y + 160
            active = i < p1_lives
            flask_col = GREEN if active else (65, 42, 34)
            pygame.draw.circle(self.pixel_surface, flask_col, (fx + 8, fy + 12), 7)
            pygame.draw.rect(self.pixel_surface, flask_col, (fx + 6, fy + 2, 4, 6))
            pygame.draw.rect(self.pixel_surface, GOLD if active else (50, 32, 24), (fx + 5, fy, 6, 3), border_radius=1)

        self._draw_separator(p1_box.x + 14, p1_box.y + 195, p1_box.right - 14)

        p1_cur = self.player_cursors.get("player_1", 0)
        tot_seq = len(self.sequence) if self.sequence else 1
        self._text(f"İLERLEME: {p1_cur}/{tot_seq}", self.font_tiny, TEXT, (p1_box.x + 16, p1_box.y + 210))
        if self.sequence:
            for i, mat in enumerate(self.sequence):
                dot_x = p1_box.x + 18 + (i % 6) * 38
                dot_y = p1_box.y + 232 + (i // 6) * 30
                c = COLORS.get(mat, GOLD) if i < p1_cur else (50, 36, 28)
                pygame.draw.rect(self.pixel_surface, c, (dot_x, dot_y, 28, 20), border_radius=4)
                if i < p1_cur:
                    pygame.draw.rect(self.pixel_surface, GREEN_LT, (dot_x, dot_y, 28, 20), 1, border_radius=4)
                elif i == p1_cur:
                    pygame.draw.rect(self.pixel_surface, GOLD_LT, (dot_x, dot_y, 28, 20), 2, border_radius=4)

        is_stunned = now < self.player_stuns.get("player_1", 0.0)
        status_y = p1_box.bottom - 44
        if is_stunned:
            rem_stun = self.player_stuns.get("player_1", 0.0) - now
            pygame.draw.rect(self.pixel_surface, (70, 20, 15), (p1_box.x + 14, status_y, p1_box.width - 28, 30), border_radius=6)
            self._text_center(f"⚡ SERSEMLENDİ ({rem_stun:.1f}s)", self.font_tiny, RED_LT, p1_box.centerx, status_y + 9)
        elif p1_cur == len(self.sequence) and self.sequence:
            pygame.draw.rect(self.pixel_surface, (20, 60, 30), (p1_box.x + 14, status_y, p1_box.width - 28, 30), border_radius=6)
            self._text_center("TAMAMLADI!", self.font_tiny, GREEN_LT, p1_box.centerx, status_y + 9)
        else:
            pygame.draw.rect(self.pixel_surface, (40, 30, 20), (p1_box.x + 14, status_y, p1_box.width - 28, 30), border_radius=6)
            self._text_center("YARIŞIYOR...", self.font_tiny, GOLD, p1_box.centerx, status_y + 9)

        # ── 2. Çırak Paneli (Sağ) ──
        p2 = self.players.get("player_2", {"name": "Çırak 2", "emblem": "🜍"})
        p2_box = pygame.Rect(WIDTH - 256 - 16, 88, 256, 460)
        self._draw_panel(p2_box, radius=12)
        pygame.draw.rect(self.pixel_surface, BORDER, p2_box, 1, border_radius=12)

        self._text("2. ÇIRAK", self.font_tiny, TEXT_DIM, (p2_box.x + 16, p2_box.y + 14))
        p2_name = p2.get("name", "Çırak 2")[:14]
        self._text_shadow(p2_name, self.font_large, GOLD_LT, (p2_box.x + 16, p2_box.y + 30))
        emb_surf2 = self.font_symbol_large.render(p2.get("emblem", "🜍"), True, GOLD)
        self.pixel_surface.blit(emb_surf2, (p2_box.right - 44, p2_box.y + 16))

        self._draw_separator(p2_box.x + 14, p2_box.y + 60, p2_box.right - 14)

        self._text("RAUNT YILDIZLARI", self.font_tiny, TEXT_DIM, (p2_box.x + 16, p2_box.y + 72))
        for i in range(3):
            sx = p2_box.x + 20 + i * 40
            sy = p2_box.y + 88
            has_star = i < s2
            star_col = GOLD_LT if has_star else (60, 44, 34)
            star_txt = "★" if has_star else "☆"
            star_s = self.font_symbol_large.render(star_txt, True, star_col)
            self.pixel_surface.blit(star_s, (sx, sy))

        self._draw_separator(p2_box.x + 14, p2_box.y + 130, p2_box.right - 14)

        p2_lives = self.player_lives.get("player_2", 3)
        self._text("RAUNT HAKKI", self.font_tiny, TEXT_DIM, (p2_box.x + 16, p2_box.y + 142))
        for i in range(3):
            fx = p2_box.x + 20 + i * 32
            fy = p2_box.y + 160
            active = i < p2_lives
            flask_col = GREEN if active else (65, 42, 34)
            pygame.draw.circle(self.pixel_surface, flask_col, (fx + 8, fy + 12), 7)
            pygame.draw.rect(self.pixel_surface, flask_col, (fx + 6, fy + 2, 4, 6))
            pygame.draw.rect(self.pixel_surface, GOLD if active else (50, 32, 24), (fx + 5, fy, 6, 3), border_radius=1)

        self._draw_separator(p2_box.x + 14, p2_box.y + 195, p2_box.right - 14)

        p2_cur = self.player_cursors.get("player_2", 0)
        self._text(f"İLERLEME: {p2_cur}/{tot_seq}", self.font_tiny, TEXT, (p2_box.x + 16, p2_box.y + 210))
        if self.sequence:
            for i, mat in enumerate(self.sequence):
                dot_x = p2_box.x + 18 + (i % 6) * 38
                dot_y = p2_box.y + 232 + (i // 6) * 30
                c = COLORS.get(mat, GOLD) if i < p2_cur else (50, 36, 28)
                pygame.draw.rect(self.pixel_surface, c, (dot_x, dot_y, 28, 20), border_radius=4)
                if i < p2_cur:
                    pygame.draw.rect(self.pixel_surface, GREEN_LT, (dot_x, dot_y, 28, 20), 1, border_radius=4)
                elif i == p2_cur:
                    pygame.draw.rect(self.pixel_surface, GOLD_LT, (dot_x, dot_y, 28, 20), 2, border_radius=4)

        is_stunned2 = now < self.player_stuns.get("player_2", 0.0)
        status_y = p2_box.bottom - 44
        if is_stunned2:
            rem_stun2 = self.player_stuns.get("player_2", 0.0) - now
            pygame.draw.rect(self.pixel_surface, (70, 20, 15), (p2_box.x + 14, status_y, p2_box.width - 28, 30), border_radius=6)
            self._text_center(f"⚡ SERSEMLENDİ ({rem_stun2:.1f}s)", self.font_tiny, RED_LT, p2_box.centerx, status_y + 9)
        elif p2_cur == len(self.sequence) and self.sequence:
            pygame.draw.rect(self.pixel_surface, (20, 60, 30), (p2_box.x + 14, status_y, p2_box.width - 28, 30), border_radius=6)
            self._text_center("TAMAMLADI!", self.font_tiny, GREEN_LT, p2_box.centerx, status_y + 9)
        else:
            pygame.draw.rect(self.pixel_surface, (40, 30, 20), (p2_box.x + 14, status_y, p2_box.width - 28, 30), border_radius=6)
            self._text_center("YARIŞIYOR...", self.font_tiny, GOLD, p2_box.centerx, status_y + 9)

    def _draw_duel_timer_bar(self, remaining: float) -> None:
        ratio = max(0.0, remaining / self.player_duration)
        cx = WIDTH // 2
        bw = 480
        bar_rect = pygame.Rect(cx - bw // 2, 630, bw, 12)
        pygame.draw.rect(self.pixel_surface, PANEL_LT, bar_rect, border_radius=6)
        fill_w = int(bar_rect.width * ratio)
        if fill_w > 0:
            color = GREEN if ratio > 0.4 else (GOLD if ratio > 0.2 else RED)
            pygame.draw.rect(self.pixel_surface, color,
                             pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.height),
                             border_radius=6)
        secs = int(remaining) + 1
        self._text_center(f"Kalan Süre: {secs}s", self.font_tiny, TEXT, cx, 648)

    def _draw_duel_lobby(self) -> None:
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((10, 8, 7, 210))
        self.pixel_surface.blit(overlay, (0, 0))

        cx = WIDTH // 2
        self._text_center("⚔️  1v1 ÇIRAK DÜELLOSU  ⚔️", self.font_title, GOLD_LT, cx, 64)
        self._text_center("Tabîb Ekmeleddin'in huzurunda 3 raunt kazanan şampiyon olur!", self.font_small, TEXT_DIM, cx, 98)

        p1 = self.players.get("player_1", {"name": "Çırak 1", "emblem": "☿", "ready": False})
        c1 = pygame.Rect(cx - 380, 140, 310, 380)
        self._draw_panel(c1, radius=16)
        border_col = GREEN if p1.get("ready") else BORDER
        pygame.draw.rect(self.pixel_surface, border_col, c1, 2, border_radius=16)

        self._text_center("1. ÇIRAK", self.font_small, TEXT_DIM, c1.centerx, c1.y + 24)
        emb_s1 = self.font_symbol_large.render(p1.get("emblem", "☿"), True, GOLD)
        self.pixel_surface.blit(emb_s1, (c1.centerx - emb_s1.get_width() // 2, c1.y + 60))
        self._text_center(p1.get("name", "Çırak 1"), self.font_medium, TEXT, c1.centerx, c1.y + 110)

        self._draw_separator(c1.x + 20, c1.y + 145, c1.right - 20)
        status1 = "HAZIR  ✓" if p1.get("ready") else "BEKLENİYOR... ⏳"
        col1 = GREEN_LT if p1.get("ready") else TEXT_DIM
        self._text_center(status1, self.font_small, col1, c1.centerx, c1.y + 175)

        vs_rect = pygame.Rect(cx - 36, 300, 72, 72)
        pygame.draw.circle(self.pixel_surface, PANEL_LT, vs_rect.center, 36)
        pygame.draw.circle(self.pixel_surface, GOLD, vs_rect.center, 36, 2)
        self._text_center("VS", self.font_large, GOLD_LT, cx, vs_rect.centery - 8)

        p2 = self.players.get("player_2", {"name": "Çırak 2", "emblem": "🜍", "ready": False})
        c2 = pygame.Rect(cx + 70, 140, 310, 380)
        self._draw_panel(c2, radius=16)
        border_col2 = GREEN if p2.get("ready") else BORDER
        pygame.draw.rect(self.pixel_surface, border_col2, c2, 2, border_radius=16)

        self._text_center("2. ÇIRAK", self.font_small, TEXT_DIM, c2.centerx, c2.y + 24)
        emb_s2 = self.font_symbol_large.render(p2.get("emblem", "🜍"), True, GOLD)
        self.pixel_surface.blit(emb_s2, (c2.centerx - emb_s2.get_width() // 2, c2.y + 60))
        self._text_center(p2.get("name", "Çırak 2"), self.font_medium, TEXT, c2.centerx, c2.y + 110)

        self._draw_separator(c2.x + 20, c2.y + 145, c2.right - 20)
        status2 = "HAZIR  ✓" if p2.get("ready") else "BEKLENİYOR... ⏳"
        col2 = GREEN_LT if p2.get("ready") else TEXT_DIM
        self._text_center(status2, self.font_small, col2, c2.centerx, c2.y + 175)

        if self.lobby_countdown_start:
            rem = max(0, 3 - int(time.monotonic() - self.lobby_countdown_start))
            count_str = f"BAŞLIYOR: {rem}" if rem > 0 else "BAŞLA!"
            self._text_center(count_str, self.font_title, GOLD_LT, cx, 550)
        else:
            self._text_center("Telefondan ambleminizi seçip 'Hazırım' butonuna basın", self.font_small, TEXT_DIM, cx, 555)

    def _draw_duel_match_over(self) -> None:
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        self.pixel_surface.blit(overlay, (0, 0))

        cx = WIDTH // 2
        box = pygame.Rect(cx - 320, 75, 640, 520)
        self._draw_panel(box, radius=18)
        pygame.draw.rect(self.pixel_surface, GOLD, box, 3, border_radius=18)

        self._text_center("👑  DÜELLO ŞAMPİYONU  👑", self.font_title, GOLD_LT, cx, box.y + 24)
        self._draw_separator(box.x + 30, box.y + 58, box.right - 30)

        winner_id = self.duel_match_winner or "player_1"
        w_info = self.players.get(winner_id, {})
        w_name = w_info.get("name", "Şampiyon Simyacı")
        w_emblem = w_info.get("emblem", "👑")

        emb_s = self.font_symbol_large.render(w_emblem, True, GOLD_LT)
        self.pixel_surface.blit(emb_s, (cx - emb_s.get_width() // 2, box.y + 74))

        self._text_center(w_name.upper(), self.font_large, TEXT, cx, box.y + 118)
        self._text_center("Konya Dârüşşifası'nın Yeni Baş Hekimi!", self.font_small, GOLD, cx, box.y + 148)

        self._draw_separator(box.x + 30, box.y + 178, box.right - 30)

        s1 = self.duel_scores.get("player_1", 0)
        s2 = self.duel_scores.get("player_2", 0)
        p1_n = self.players.get("player_1", {}).get("name", "Çırak 1")
        p2_n = self.players.get("player_2", {}).get("name", "Çırak 2")
        score_text = f"{p1_n}: {s1} ⭐  —  ⭐ {s2} :{p2_n}"
        self._text_center(score_text, self.font_medium, TEXT_DIM, cx, box.y + 200)

        # Buton 1 & 2: Yeni Karşılaşma & Mod Seçimi
        b_rematch = pygame.Rect(cx - 240, box.y + 250, 220, 42)
        self._draw_panel(b_rematch, radius=8)
        pygame.draw.rect(self.pixel_surface, GREEN, b_rematch, 2, border_radius=8)
        self._text_center("🔄 Yeni Düello (SPACE)", self.font_body_bold, GREEN_LT, b_rematch.centerx, b_rematch.y + 11)

        b_mode = pygame.Rect(cx + 20, box.y + 250, 220, 42)
        self._draw_panel(b_mode, radius=8)
        pygame.draw.rect(self.pixel_surface, BORDER, b_mode, 2, border_radius=8)
        self._text_center("◀ Mod Seçimi (M)", self.font_body_bold, TEXT, b_mode.centerx, b_mode.y + 11)

        # Buton 3: Kulüp & Künye & Risale
        b_cred = pygame.Rect(cx - 200, box.y + 308, 400, 42)
        self._draw_panel(b_cred, radius=8)
        pygame.draw.rect(self.pixel_surface, GOLD, b_cred, 2, border_radius=8)
        self._text_center("🏛️ Kulüp, Künye & Risale (C)", self.font_body_bold, GOLD_LT, b_cred.centerx, b_cred.y + 11)

        # İpuçları
        self._draw_separator(box.x + 30, box.y + 372, box.right - 30)
        self._text_center("Telefondan 'Yeniden Düello' butonuna basabilir veya ekrandan seçebilirsiniz", self.font_tiny, TEXT_DIM, cx, box.y + 390)
        secs_left = max(0, 180 - int(time.monotonic() - self.game_over_time))
        self._text_center(f"veya {secs_left}s sonra otomatik ana menüye döner", self.font_tiny, TEXT_DIM, cx, box.y + 412)

    # ── Malzeme not kartı ────────────────────────────────────────────────────

    def _draw_note_card(self, material: str) -> None:
        elapsed  = time.monotonic() - self.note_started
        alpha    = int(255 * min(1.0, (self.NOTE_DURATION - elapsed) / 0.6))
        color    = COLORS[material]
        note     = MATERIAL_NOTES[material]
        symbol   = MATERIAL_SYMBOLS[material]

        card_w, card_h = 480, 108
        card_x = WIDTH - card_w - 20
        card_y = 90

        card = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
        # Arka plan
        pygame.draw.rect(card, (*PANEL, alpha), (0, 0, card_w, card_h), border_radius=10)
        pygame.draw.rect(card, (*color, alpha), (0, 0, card_w, card_h), 2, border_radius=10)
        # Sol renk şeridi
        pygame.draw.rect(card, (*color, alpha), (0, 0, 5, card_h), border_radius=10)

        # Sembol + isim
        sym_surf = self.font_title.render(symbol, True, (*color, alpha))
        card.blit(sym_surf, (14, 10))
        name_surf = self.font_medium.render(MATERIAL_NAMES[material].upper(), True, (*GOLD, alpha))
        card.blit(name_surf, (14, 46))

        # Not metni — word wrap
        words = note.split()
        lines, line = [], []
        max_w = card_w - 180
        for word in words:
            test = " ".join(line + [word])
            if self.font_tiny.size(test)[0] > max_w:
                lines.append(" ".join(line))
                line = [word]
            else:
                line.append(word)
        if line:
            lines.append(" ".join(line))

        ty = 10
        for ln in lines[:4]:
            ts = self.font_tiny.render(ln, True, (*TEXT_DIM, alpha))
            card.blit(ts, (170, ty))
            ty += 18

        self.pixel_surface.blit(card, (card_x, card_y))

    # ── Râzî Konuşma Balonu ──────────────────────────────────────────────────

    def _draw_speech_bubble(self) -> None:
        """Râzî'nin başı üzerinde antika parşömen konuşma balonu çizer."""
        now = time.monotonic()
        if not self.bubble_text or now - self.bubble_started >= self.bubble_duration:
            return

        bx = 220
        words = self.bubble_text.split()
        lines = []
        cur_line = []
        max_line_w = 260
        for w in words:
            test = " ".join(cur_line + [w])
            if self.font_small.size(test)[0] > max_line_w:
                if cur_line:
                    lines.append(" ".join(cur_line))
                cur_line = [w]
            else:
                cur_line.append(w)
        if cur_line:
            lines.append(" ".join(cur_line))
        if not lines:
            return

        line_h = 16
        pad_x, pad_y = 14, 10
        bw = min(320, max(self.font_small.size(l)[0] for l in lines) + pad_x * 2)
        bh = len(lines) * line_h + pad_y * 2

        # Balon gövdesi
        box_x = int(bx - bw // 2)
        box_x = max(10, min(WIDTH - bw - 10, box_x))
        box_y = int(self.floor_y - 238 - bh)

        surf = pygame.Surface((bw, bh + 14), pygame.SRCALPHA)
        # Parşömen gövde
        rect = pygame.Rect(0, 0, bw, bh)
        pygame.draw.rect(surf, (252, 248, 236), rect, border_radius=10)
        pygame.draw.rect(surf, (95, 65, 38), rect, 2, border_radius=10)

        # Kuyruk (Râzî'nin ağzına doğru)
        tail_x = bw // 2
        tail_points = [(tail_x - 8, bh - 1), (tail_x + 8, bh - 1), (tail_x - 3, bh + 12)]
        pygame.draw.polygon(surf, (252, 248, 236), tail_points)
        pygame.draw.line(surf, (95, 65, 38), (tail_x - 8, bh - 1), (tail_x - 3, bh + 12), 2)
        pygame.draw.line(surf, (95, 65, 38), (tail_x + 8, bh - 1), (tail_x - 3, bh + 12), 2)

        # Metin çizimi
        ty = pad_y
        for ln in lines:
            t_surf = self.font_small.render(ln, True, (45, 28, 18))
            surf.blit(t_surf, (pad_x, ty))
            ty += line_h

        self.pixel_surface.blit(surf, (box_x, box_y))

    # ── Tarihi Bilgi Kartı ────────────────────────────────────────────────────

    def _draw_info_card(self) -> None:
        """Tarihi Ebû Bekir er-Râzî simya/tıp bilgi kartını çizer."""
        now = time.monotonic()
        if not self.info_card_mat or now >= self.info_card_until:
            return

        mat = self.info_card_mat
        fact = MATERIAL_NOTES.get(mat, "")
        if not fact:
            return

        cw, ch = 380, 110
        cx = WIDTH - cw - 20
        cy = 75

        elapsed = self.info_card_until - now
        alpha = int(min(255, elapsed * 180)) if elapsed < 1.0 else 245

        card_surf = pygame.Surface((cw, ch), pygame.SRCALPHA)
        pygame.draw.rect(card_surf, (28, 20, 16, alpha), (0, 0, cw, ch), border_radius=12)
        pygame.draw.rect(card_surf, (*GOLD, alpha), (0, 0, cw, ch), 2, border_radius=12)
        pygame.draw.rect(card_surf, (80, 55, 35, int(alpha * 0.7)), (3, 3, cw - 6, ch - 6), 1, border_radius=10)

        # Başlık
        head = self.font_tiny.render("🏺 TABÎB EKMELEDDİN'İN NOTU", True, (*GOLD_LT, alpha))
        card_surf.blit(head, (16, 12))

        # Sembol ve İsim
        sym = MATERIAL_SYMBOLS.get(mat, "🜔")
        color = COLORS.get(mat, GOLD)
        name_str = MATERIAL_NAMES.get(mat, mat)
        sym_surf = self.font_large.render(f"{sym} {name_str}", True, (*color, alpha))
        card_surf.blit(sym_surf, (16, 28))

        # Bilgi metni
        words = fact.split()
        lines = []
        cur_l = []
        for w in words:
            test = " ".join(cur_l + [w])
            if self.font_tiny.size(test)[0] > cw - 32:
                lines.append(" ".join(cur_l))
                cur_l = [w]
            else:
                cur_l.append(w)
        if cur_l:
            lines.append(" ".join(cur_l))

        fy = 54
        for ln in lines[:3]:
            f_surf = self.font_tiny.render(ln, True, (*TEXT, alpha))
            card_surf.blit(f_surf, (16, fy))
            fy += 15

        self.pixel_surface.blit(card_surf, (cx, cy))


    def _draw_hint_banner(self, text: str, material: str | None) -> None:
        """Ekranın alt kısmında Gemini'den gelen Bey Hekim ipucunu göster."""
        elapsed   = time.monotonic() - self.hint_started
        duration  = self.hint_engine.HINT_DURATION
        fade_time = 0.8
        alpha     = int(255 * min(1.0, (duration - elapsed) / fade_time))

        accent = COLORS.get(material, GOLD) if material else GOLD

        banner_h = 70
        banner_y = HEIGHT - banner_h - 10
        banner   = pygame.Surface((WIDTH - 40, banner_h), pygame.SRCALPHA)

        # Arka plan
        pygame.draw.rect(banner, (*PANEL, alpha),  (0, 0, WIDTH - 40, banner_h), border_radius=10)
        pygame.draw.rect(banner, (*accent, alpha),  (0, 0, WIDTH - 40, banner_h), 2, border_radius=10)
        # Sol şerit
        pygame.draw.rect(banner, (*accent, alpha),  (0, 0, 4, banner_h), border_radius=8)

        # Gemini ikonu
        gem_label = self.font_tiny.render("✦ BEY HEKİM'İN İPUCU", True, (*GOLD, alpha))
        banner.blit(gem_label, (14, 8))

        # İpucu metni — word wrap
        words   = text.split()
        lines, line = [], []
        max_w   = WIDTH - 80
        for word in words:
            test = " ".join(line + [word])
            if self.font_small.size(test)[0] > max_w:
                lines.append(" ".join(line))
                line = [word]
            else:
                line.append(word)
        if line:
            lines.append(" ".join(line))

        ty = 28
        for ln in lines[:2]:
            ts = self.font_small.render(ln, True, (*TEXT, alpha))
            banner.blit(ts, (14, ty))
            ty += 18

        self.pixel_surface.blit(banner, (20, banner_y))

    # ── AI durum göstergesi ───────────────────────────────────────────────────

    def _draw_ai_status(self) -> None:
        """Sağ alt köşede Gemini ve VoiceEngine durumunu göster."""
        icons = []
        if self.hint_engine.enabled:
            icons.append(("✦ Gemini", GREEN))
        else:
            icons.append(("✦ Gemini", (80, 80, 80)))

        if self.voice.enabled:
            icons.append(("♪ Ses", GREEN))
        else:
            icons.append(("♪ Ses", (80, 80, 80)))

        x = WIDTH - 10
        y = HEIGHT - 18
        for label, color in reversed(icons):
            s = self.font_tiny.render(label, True, color)
            x -= s.get_width() + 14
            self.pixel_surface.blit(s, (x, y))

    # ── Yardımcı çizim metodları ─────────────────────────────────────────────

    def _draw_panel(self, rect: pygame.Rect, radius: int = 10) -> None:
        pygame.draw.rect(self.pixel_surface, PANEL, rect, border_radius=radius)
        pygame.draw.rect(self.pixel_surface, BORDER, rect, 2, border_radius=radius)

    def _draw_separator(self, x1: int, y: int, x2: int) -> None:
        pygame.draw.line(self.pixel_surface, BORDER, (x1, y), (x2, y), 1)

    def _text(self, text: str, font: pygame.font.Font,
              color: tuple, pos: tuple) -> None:
        self.pixel_surface.blit(font.render(text, True, color), pos)

    def _text_shadow(self, text: str, font: pygame.font.Font,
                     color: tuple, pos: tuple, offset: int = 2) -> None:
        shadow = font.render(text, True, (0, 0, 0))
        self.pixel_surface.blit(shadow, (pos[0] + offset, pos[1] + offset))
        self.pixel_surface.blit(font.render(text, True, color), pos)

    def _text_center(self, text: str, font: pygame.font.Font,
                     color: tuple, cx: int, y: int) -> None:
        s = font.render(text, True, color)
        self.pixel_surface.blit(s, (cx - s.get_width() // 2, y))

    def _draw_multiline_text(self, text: str, font: pygame.font.Font,
                             color: tuple, x: int, y: int, max_width: int,
                             line_spacing: int = 6) -> int:
        words = text.split()
        lines = []
        current_line: list[str] = []
        for word in words:
            test_line = " ".join(current_line + [word])
            if font.size(test_line)[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))

        cur_y = y
        for line in lines:
            s = font.render(line, True, color)
            self.pixel_surface.blit(s, (x, cur_y))
            cur_y += font.get_height() + line_spacing
        return cur_y

    def _draw_mode_select_screen(self) -> None:
        mouse_pos = pygame.mouse.get_pos()

        # Başlık ve Üst Panel
        self._text_shadow("TABÎB EKMELEDDİN'İN KAZANI", self.font_title, GOLD_LT, (WIDTH // 2 - 270, 48))
        self._text_center("Kadim Tıp ve Simya Mirası · Bir Hafıza ve Dikkat Ayini", self.font_body, TEXT_DIM, WIDTH // 2, 86)
        self._draw_separator(80, 118, WIDTH - 80)

        # Kart 1: Tek Kişilik Macera (x=90, y=145, w=430, h=410)
        c1 = pygame.Rect(90, 145, 430, 410)
        c1_hover = c1.collidepoint(mouse_pos)
        self._draw_panel(c1, radius=16)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if c1_hover else BORDER, c1, 3 if c1_hover else 2, border_radius=16)

        # Kart 1 İçerik
        self._text_center("🧪", self.font_symbol_large, GOLD if c1_hover else TEXT_DIM, c1.centerx, c1.y + 24)
        self._text_center("TEK KİŞİLİK MACERA", self.font_medium, GOLD_LT, c1.centerx, c1.y + 68)
        self._text_center("100 Seviyeli Kadim İksir Yolculuğu", self.font_body_bold, TEXT, c1.centerx, c1.y + 98)
        self._draw_separator(c1.x + 30, c1.y + 128, c1.right - 30)

        c1_bullets = [
            "• 1 Oyuncu (Mobil cihazla kumanda)",
            "• 3 İksir şişesi (can) kırılma hakkı",
            "• 30 Simya cevheri & Tarihi reçeteler",
            "• Her 3 elementte bir artan süre ve kombo",
            "• Bey Hekim'in talimatlarını dinle ve başla!",
        ]
        by = c1.y + 144
        for b in c1_bullets:
            self._text(b, self.font_body, TEXT if c1_hover else TEXT_DIM, (c1.x + 32, by))
            by += 32

        # Kart 1 Buton
        btn1 = pygame.Rect(c1.x + 30, c1.bottom - 64, c1.width - 60, 44)
        self._draw_panel(btn1, radius=10)
        pygame.draw.rect(self.pixel_surface, GOLD if c1_hover else GREEN, btn1, 2, border_radius=10)
        self._text_center("▶ 1 Tuşu veya TIKLA: BAŞLAT", self.font_body_bold, GOLD_LT if c1_hover else GREEN_LT, btn1.centerx, btn1.y + 12)

        # Kart 2: 1v1 Çırak Düellosu (x=580, y=145, w=430, h=410)
        c2 = pygame.Rect(580, 145, 430, 410)
        c2_hover = c2.collidepoint(mouse_pos)
        self._draw_panel(c2, radius=16)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if c2_hover else BORDER, c2, 3 if c2_hover else 2, border_radius=16)

        # Kart 2 İçerik
        self._text_center("⚔️", self.font_symbol_large, GOLD if c2_hover else TEXT_DIM, c2.centerx, c2.y + 24)
        self._text_center("1v1 ÇIRAK DÜELLOSU", self.font_medium, GOLD_LT, c2.centerx, c2.y + 68)
        self._text_center("İki Simyacının Canlı Hız & Hafıza Yarışı", self.font_body_bold, TEXT, c2.centerx, c2.y + 98)
        self._draw_separator(c2.x + 30, c2.y + 128, c2.right - 30)

        c2_bullets = [
            "• 2 Oyuncu (2 Ayrı mobil cihaz kumandası)",
            "• Canlı & eşzamanlı hafıza düellosu",
            "• İlk 3 raundu (yıldızı) kazanan şampiyon",
            "• Yanlış malzeme seçiminde 1.2s sersemleme",
            "• Konya Dârüşşifası'nın yeni baş hekimi belirlensin!",
        ]
        by2 = c2.y + 144
        for b in c2_bullets:
            self._text(b, self.font_body, TEXT if c2_hover else TEXT_DIM, (c2.x + 32, by2))
            by2 += 32

        # Kart 2 Buton
        btn2 = pygame.Rect(c2.x + 30, c2.bottom - 64, c2.width - 60, 44)
        self._draw_panel(btn2, radius=10)
        pygame.draw.rect(self.pixel_surface, GOLD if c2_hover else GREEN, btn2, 2, border_radius=10)
        self._text_center("⚔️ 2 Tuşu veya TIKLA: BAŞLAT", self.font_body_bold, GOLD_LT if c2_hover else GREEN_LT, btn2.centerx, btn2.y + 12)

        # Alt Bar: Kulüp & Künye Butonu & Çıkış
        self._draw_separator(80, 574, WIDTH - 80)
        btn_credits = pygame.Rect(WIDTH // 2 - 200, 588, 400, 44)
        cred_hover = btn_credits.collidepoint(mouse_pos)
        self._draw_panel(btn_credits, radius=10)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if cred_hover else GOLD, btn_credits, 2, border_radius=10)
        self._text_center("🏛️ ERÜ Anadolu Tıp Tarihi Topluluğu & Künye (C)", self.font_body_bold, GOLD_LT, btn_credits.centerx, btn_credits.y + 12)

    def _draw_prologue_screen(self) -> None:
        mouse_pos = pygame.mouse.get_pos()
        now = time.monotonic()
        elapsed = now - self.prologue_started

        # Üst Başlık
        self._text_shadow("TABÎB EKMELEDDİN (BEY HEKİM) DİYOR Kİ:", self.font_title, GOLD_LT, (WIDTH // 2 - 320, 36))
        mode_label = "⚔️ 1v1 Çırak Düellosu Talimatları" if self.mode == GameMode.DUEL else "🧪 Tek Kişilik Macera Talimatları"
        self._text_center(mode_label, self.font_body_bold, TEXT_DIM, WIDTH // 2, 72)

        # Sol taraf: Râzî'nin animasyonu ve kazanı
        # Râzî konuşma hareketi: 1. frame ile 4. frame arasında döngü
        speech_frame_idx = int((elapsed * 3.5) % 4) + 1
        frame = self.anim_master.get_frame_at(speech_frame_idx)
        if frame:
            self.pixel_surface.blit(frame, (60, 240))
        # Kazanın normal ateşi
        forge_frame = self.anim_forge.get_frame(now, fps=8.0)
        if forge_frame:
            self.pixel_surface.blit(forge_frame, (230, 310))

        # Sağ taraf: Parşömen Diyalog Kutusu
        box = pygame.Rect(440, 100, 610, 460)
        self._draw_panel(box, radius=16)
        pygame.draw.rect(self.pixel_surface, GOLD, box, 2, border_radius=16)

        # Parşömen Başlığı
        self._text("📜 KONYA DÂRÜŞŞİFASI VE AYİNİN SIRRI", self.font_medium, GOLD_LT, (box.x + 30, box.y + 22))
        self._draw_separator(box.x + 20, box.y + 52, box.right - 20)

        # Açıklama Metni (Multiline)
        body_text = self.prologue_text or (RHAZI_PROLOGUE_DUEL if self.mode == GameMode.DUEL else RHAZI_PROLOGUE_SINGLE)
        self._draw_multiline_text(body_text, self.font_body, TEXT, box.x + 30, box.y + 68, box.width - 60, line_spacing=8)

        # Alt Hatırlatma
        self._draw_separator(box.x + 20, box.bottom - 54, box.right - 20)
        self._text("💡 Hem ekrandan hem de telefonundaki butona basarak başlayabilirsin!", self.font_body, GOLD, (box.x + 24, box.bottom - 40))

        # Ana Başlat Butonu (Kullanıcı kesin kuralı: "ayine başla yazmasın direkt başla yazsın")
        btn_start = pygame.Rect(WIDTH // 2 - 160, 580, 320, 54)
        btn_hover = btn_start.collidepoint(mouse_pos)
        self._draw_panel(btn_start, radius=12)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if btn_hover else GOLD, btn_start, 3 if btn_hover else 2, border_radius=12)
        self._text_center("▶  BAŞLA", self.font_body_large, GOLD_LT if btn_hover else (255, 235, 170), btn_start.centerx, btn_start.y + 12)
        self._text_center("(Boşluk, Enter veya Telefondan 'BAŞLA')", self.font_tiny, TEXT_DIM, btn_start.centerx, btn_start.bottom + 6)

        # Sol üst Geri Dön butonu
        btn_back = pygame.Rect(30, 20, 150, 36)
        self._draw_panel(btn_back, radius=6)
        pygame.draw.rect(self.pixel_surface, BORDER, btn_back, 1, border_radius=6)
        self._text_center("◀ Menü (ESC)", self.font_tiny, TEXT_DIM, btn_back.centerx, btn_back.y + 12)

    def _draw_credits_screen(self) -> None:
        mouse_pos = pygame.mouse.get_pos()

        # Karartma Arka Plan (Sinematik Koyu Tiyatro / Parşömen Tonu)
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((5, 3, 2, 230))
        self.pixel_surface.blit(overlay, (0, 0))

        # Ana Modal Kutusu
        box = pygame.Rect(50, 20, 1000, 660)
        self._draw_panel(box, radius=16)
        pygame.draw.rect(self.pixel_surface, GOLD, box, 2, border_radius=16)

        # Modal Başlık (Sabit Üst Başlık)
        self._text_center("🏛️  ERÜ ANADOLU TIP TARİHİ TOPLULUĞU  🏛️", self.font_title, GOLD_LT, box.centerx, box.y + 16)
        self._text_center("Tabîb Ekmeleddin (Bey Hekim) Kazanı — Sinematik Kayan Jenerik", self.font_body, (225, 205, 170), box.centerx, box.y + 44)
        self._text_center("[Boşluk / Tıkla]: Duraklat/Devam  •  [↑/↓ veya Fare]: Kaydır  •  [R]: Başa Sar", self.font_tiny, (170, 150, 115), box.centerx, box.y + 66)
        self._draw_separator(box.x + 30, box.y + 84, box.right - 30)

        # Zaman & Akış Hesaplaması (Sinematik Kayan Jenerik)
        now = time.monotonic()
        if self.credits_last_time <= 0.0:
            dt = 1.0 / 60.0
        else:
            dt = min(0.1, now - self.credits_last_time)
        self.credits_last_time = now

        if not self.credits_paused:
            self.credits_scroll_y += dt * 36.0  # saniyede 36 piksel akıcı okuma hızı

        # Kayan Jenerik Alanı (Viewport: y: 90 -> 590, yükseklik 500)
        viewport = pygame.Rect(box.x + 20, box.y + 90, box.width - 40, 505)

        # Görünüm alanını sınırla (Viewport Clip)
        self.pixel_surface.set_clip(viewport)

        # Kayan içerik dikey başlangıç konumu
        cur_y = viewport.y + 30 - int(self.credits_scroll_y)

        # Başlangıç Tepe Amblemi
        self._text_center("✦ ✦ ✦   B E Y   H E K İ M   ✦ ✦ ✦", self.font_body_bold, GOLD_LT, viewport.centerx, cur_y)
        cur_y += 24
        self._text_center("ERCİYES ÜNİVERSİTESİ TIP FAKÜLTESİ", self.font_body_bold, (245, 235, 215), viewport.centerx, cur_y)
        cur_y += 20
        self._text_center("Kadim Tıp Kültürü, Deontoloji ve Bilim Yolculuğu", self.font_tiny, TEXT_DIM, viewport.centerx, cur_y)
        cur_y += 42

        # Kategori ve İsimlerin Sırayla Akışı
        for block in CREDITS_ROLL_DATA:
            # Kategori Rozeti / Başlığı
            self._text_center(block.get("badge", ""), self.font_body_bold, GOLD, viewport.centerx, cur_y)
            cur_y += 22

            # Alt Başlık
            self._text_center(block.get("title", ""), self.font_body_large, GOLD_LT, viewport.centerx, cur_y)
            cur_y += 22

            # Açıklama
            desc = block.get("desc", "")
            if desc:
                self._text_center(desc, self.font_tiny, (180, 165, 140), viewport.centerx, cur_y)
                cur_y += 20

            cur_y += 8

            # İsimler
            for name in block.get("names", []):
                self._text_center(name, self.font_body, (250, 242, 228), viewport.centerx, cur_y)
                cur_y += 24

            cur_y += 8
            # Narin ara çizgi
            self._draw_separator(viewport.centerx - 140, cur_y, viewport.centerx + 140)
            cur_y += 30

        # Kapanış Sözü
        cur_y += 10
        self._text_center("« İlim ile amel birleşince şifa kemâle erer. »", self.font_body_bold, GOLD_LT, viewport.centerx, cur_y)
        cur_y += 26
        self._text_center("ERÜ ANADOLU TIP TARİHİ TOPLULUĞU", self.font_body, (220, 205, 175), viewport.centerx, cur_y)
        cur_y += 22
        self._text_center("Kayseri · 2026", self.font_tiny, TEXT_DIM, viewport.centerx, cur_y)
        cur_y += 80

        # Sonsuz akış döngüsü (Başa dönme)
        total_content_height = cur_y - (viewport.y + 30 - int(self.credits_scroll_y))
        if self.credits_scroll_y > total_content_height + viewport.height:
            self.credits_scroll_y = 0.0

        # Kırpma alanını kaldır
        self.pixel_surface.set_clip(None)

        # Üst ve Alt Sinematik Karartma Maskeleri (Soft Alpha Fade)
        top_fade = pygame.Surface((viewport.width, 45), pygame.SRCALPHA)
        for i in range(45):
            alpha = int(255 * (1.0 - (i / 45.0)))
            pygame.draw.line(top_fade, (16, 10, 7, alpha), (0, i), (viewport.width, i))
        self.pixel_surface.blit(top_fade, (viewport.x, viewport.y))

        bot_fade = pygame.Surface((viewport.width, 45), pygame.SRCALPHA)
        for i in range(45):
            alpha = int(255 * (i / 45.0))
            pygame.draw.line(bot_fade, (16, 10, 7, alpha), (0, i), (viewport.width, i))
        self.pixel_surface.blit(bot_fade, (viewport.x, viewport.bottom - 45))

        # Duraklatıldı Rozeti
        if self.credits_paused:
            pause_rect = pygame.Rect(viewport.centerx - 140, viewport.y + 12, 280, 28)
            p_surf = pygame.Surface((pause_rect.width, pause_rect.height), pygame.SRCALPHA)
            p_surf.fill((40, 25, 15, 230))
            self.pixel_surface.blit(p_surf, (pause_rect.x, pause_rect.y))
            pygame.draw.rect(self.pixel_surface, GOLD, pause_rect, 1, border_radius=6)
            self._text_center("⏸ DURAKLATILDI (Tıkla / Boşluk)", self.font_tiny, GOLD_LT, pause_rect.centerx, pause_rect.y + 8)

        # Alt Eylem Butonları Alanı Ayırıcı
        self._draw_separator(box.x + 30, 604, box.right - 30)

        # Buton 1: ERÜ Topluluk Kayıt Butonu (110 <= x <= 380, 615 <= y <= 670)
        btn_reg = pygame.Rect(110, 615, 270, 48)
        b_reg_hover = btn_reg.collidepoint(mouse_pos)
        self._draw_panel(btn_reg, radius=10)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if b_reg_hover else GOLD, btn_reg, 2, border_radius=10)
        self._text_center("🌐 ERÜ Topluluk Kayıt", self.font_body_bold, GOLD_LT if b_reg_hover else (255, 235, 175), btn_reg.centerx, btn_reg.y + 10)
        self._text_center("kulup.erciyes.edu.tr/uyelik/uyeol", self.font_tiny, TEXT_DIM, btn_reg.centerx, btn_reg.y + 30)

        # Buton 2: Tabîb Ekmeleddin PDF İndir Butonu (410 <= x <= 710, 615 <= y <= 670)
        btn_pdf = pygame.Rect(410, 615, 300, 48)
        b_pdf_hover = btn_pdf.collidepoint(mouse_pos)
        self._draw_panel(btn_pdf, radius=10)
        pygame.draw.rect(self.pixel_surface, GOLD_LT if b_pdf_hover else GREEN, btn_pdf, 2, border_radius=10)
        self._text_center("📥 Tabîb Ekmeleddin PDF İndir", self.font_body_bold, GOLD_LT if b_pdf_hover else GREEN_LT, btn_pdf.centerx, btn_pdf.y + 10)
        self._text_center("Tezhipli Biyografi & Tıp Risalesi", self.font_tiny, TEXT_DIM, btn_pdf.centerx, btn_pdf.y + 30)

        # Buton 3: Kapat / Geri Dön (740 <= x <= 990, 615 <= y <= 670)
        btn_close = pygame.Rect(740, 615, 250, 48)
        b_close_hover = btn_close.collidepoint(mouse_pos)
        self._draw_panel(btn_close, radius=10)
        pygame.draw.rect(self.pixel_surface, RED_LT if b_close_hover else BORDER, btn_close, 2, border_radius=10)
        self._text_center("◀ Kapat / Geri (ESC)", self.font_body_bold, (255, 230, 220) if b_close_hover else TEXT, btn_close.centerx, btn_close.y + 15)

    def _spawn_particles(self, x: float, y: float,
                         color: tuple, count: int) -> None:
        for _ in range(count):
            angle = random.random() * 6.283185
            speed = random.uniform(30, 140)
            v = pygame.math.Vector2(1, 0).rotate_rad(angle) * speed
            self.particles.append({
                "x": x, "y": y,
                "vx": v.x, "vy": v.y,
                "life": random.uniform(0.4, 1.1),
                "size": random.uniform(2, 5),
                "color": color,
            })

    def _update_particles(self) -> None:
        dt = 1 / 60
        for p in self.particles:
            p["x"]   += p["vx"] * dt
            p["y"]   += p["vy"] * dt
            p["vy"]  += 90 * dt
            p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]

    def _draw_particles(self) -> None:
        for p in self.particles:
            pygame.draw.circle(
                self.pixel_surface, p["color"],
                (int(p["x"]), int(p["y"])), max(1, int(p["size"]))
            )

    # ── Flash efekti ─────────────────────────────────────────────────────────

    def _draw_flash(self) -> None:
        if not self.flash_started:
            return
        elapsed = time.monotonic() - self.flash_started
        if elapsed >= 0.5:
            return
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        alpha   = int(100 * ((1 - elapsed / 0.5) ** 1.5))
        overlay.fill((*self.flash_color, alpha))
        self.pixel_surface.blit(overlay, (0, 0))

    # ── Arka plan ────────────────────────────────────────────────────────────

    def _make_background(self) -> pygame.Surface:
        assets_dir = os.path.dirname(__file__)

        # Aktif pixel-art arka plan görseli
        background_path = os.path.join(assets_dir, "assets", "arkaplan_yeni.jpg")
        try:
            bg = pygame.image.load(background_path).convert()
            img_w, img_h = bg.get_size()

            # "Contain" — resmin tamamı görünür, oran korunur, kenar boşlukları BG rengi
            scale = min(WIDTH / img_w, HEIGHT / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            scaled  = pygame.transform.smoothscale(bg, (new_w, new_h))
            surface = pygame.Surface((WIDTH, HEIGHT))
            surface.fill(BG)
            x_off = (WIDTH  - new_w) // 2
            y_off = (HEIGHT - new_h) // 2
            surface.blit(scaled, (x_off, y_off))

            # Zemin çizgisi: resmin alt kenarından BG_FLOOR_PCT oranında yukarı
            self.floor_y = y_off + int(new_h * BG_FLOOR_PCT)
            return surface
        except (OSError, pygame.error) as e:
            print(f"Arka plan yüklenemedi ({e}), fallback çizim kullanılıyor.")

        # Fallback — çizimsel arka plan
        surface = pygame.Surface((WIDTH, HEIGHT))
        surface.fill((32, 20, 18))
        pygame.draw.rect(surface, (55, 35, 28), (0, 0, WIDTH, 510))
        for x in range(0, WIDTH, 22):
            pygame.draw.line(surface, (68, 42, 30), (x, 0), (x, 510), 2)
        for y in range(24, 510, 42):
            pygame.draw.line(surface, (42, 26, 22), (0, y), (WIDTH, y), 2)
        for row in range(10):
            by = 510 + row * 20
            for col in range(-1, WIDTH // 160 + 2):
                bx = col * 160 - (80 if row % 2 else 0)
                pygame.draw.rect(surface, (80, 46, 26), (bx, by, 158, 18))
                pygame.draw.rect(surface, (45, 26, 18), (bx, by, 158, 18), 2)
                pygame.draw.line(surface, (110, 64, 32), (bx + 10, by + 4), (bx + 140, by + 4), 1)

        window = pygame.Rect(405, 58, 355, 350)
        pygame.draw.rect(surface, (28, 18, 18), window.inflate(18, 18))
        pygame.draw.rect(surface, (110, 155, 170), window)
        pygame.draw.rect(surface, (170, 195, 185), (window.x, window.y, window.w, 115))
        pygame.draw.rect(surface, (225, 165, 90), (window.x, window.y + 115, window.w, 235))
        pygame.draw.rect(surface, (60, 80, 88), (window.x, window.y + 180, window.w, 170))

        mosque = (45, 38, 46)
        pygame.draw.rect(surface, mosque, (500, 274, 164, 76))
        pygame.draw.rect(surface, mosque, (485, 294, 194, 56))
        pygame.draw.ellipse(surface, mosque, (538, 224, 88, 86))
        pygame.draw.polygon(surface, mosque, ((538, 265), (582, 211), (626, 265)))
        pygame.draw.rect(surface, mosque, (516, 245, 10, 105))
        pygame.draw.polygon(surface, mosque, ((511, 246), (521, 226), (531, 246)))
        pygame.draw.rect(surface, mosque, (646, 235, 10, 115))
        pygame.draw.polygon(surface, mosque, ((641, 236), (651, 211), (661, 236)))
        pygame.draw.rect(surface, (215, 170, 75), (557, 291, 15, 35))
        pygame.draw.rect(surface, (215, 170, 75), (617, 291, 15, 35))
        pygame.draw.line(surface, (35, 24, 26), (window.centerx, window.top), (window.centerx, window.bottom), 8)
        pygame.draw.line(surface, (35, 24, 26), (window.left, 225), (window.right, 225), 8)

        def draw_bookshelf(sx: int, sy: int, w: int, rows: int) -> None:
            pygame.draw.rect(surface, (36, 20, 18), (sx, sy, w, rows * 66 + 18))
            pygame.draw.rect(surface, (100, 56, 28), (sx, sy, w, 8))
            for row in range(rows):
                shelf_y = sy + 58 + row * 66
                pygame.draw.rect(surface, (104, 58, 28), (sx, shelf_y, w, 8))
                for book in range(w // 22):
                    bx   = sx + 8 + book * 22
                    bh   = 32 + ((book + row) % 3) * 9
                    bcol = (118, 55, 40)
                    pygame.draw.rect(surface, bcol, (bx, shelf_y - bh, 15, bh))
                    pygame.draw.line(surface, (200, 138, 64), (bx + 3, shelf_y - bh + 4), (bx + 3, shelf_y - 5), 1)

        draw_bookshelf(18, 54, 230, 6)
        draw_bookshelf(852, 54, 230, 6)

        shadow = pygame.Surface((WIDTH, 30), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 130))
        surface.blit(shadow, (0, 500))
        for gx, gy in ((150, 320), (980, 320)):
            glow = pygame.Surface((300, 300), pygame.SRCALPHA)
            pygame.draw.circle(glow, (130, 80, 35, 40), (150, 150), 150)
            pygame.draw.circle(glow, (170, 110, 45, 28), (150, 150), 80)
            surface.blit(glow, (gx - 150, gy - 150))
        return surface


if __name__ == "__main__":
    Game().run()
