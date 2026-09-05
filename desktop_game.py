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



class GameState(Enum):
    WAITING_FOR_PLAYER = auto()
    RHAZI_TURN         = auto()
    PLAYER_TURN        = auto()
    RESOLUTION         = auto()
    GAME_OVER          = auto()


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


def save_score(level: int, room_id: str = "") -> None:
    """Skoru yerel JSON'a ve sunucu liderlik tablosuna kaydeder."""
    # Yerel kayıt
    scores = load_scores()
    scores.append({"level": level, "ts": int(time.time())})
    scores = sorted(scores, key=lambda s: s["level"], reverse=True)[:10]
    SCORES_FILE.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")

    # Sunucu liderlik tablosuna gönder (arka planda, hata sessizce yutulur)
    def _post() -> None:
        try:
            data = json.dumps({"level": level, "room_id": room_id}).encode()
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
            f"Sen Ebû Bekir er-Râzî'nin ruhusun. 9. yüzyıl İslam simyacısı ve tabip.\n"
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
        pygame.display.set_caption("Ebû Bekir er-Râzî'nin Kazanı")
        self.clock = pygame.time.Clock()
        self.pixel_surface = pygame.Surface((WIDTH, HEIGHT))

        # ── Fontlar ──────────────────────────────────────────────────────────
        _fp = os.path.join(os.path.dirname(__file__), "assets", "PressStart2P.ttf")
        self.font_title  = pygame.font.Font(_fp, 18)   # Ekran başlığı
        self.font_large  = pygame.font.Font(_fp, 13)   # Seviye / durum
        self.font_medium = pygame.font.Font(_fp, 10)   # Malzeme adı, notlar
        self.font_small  = pygame.font.Font(_fp, 8)    # Yardımcı bilgi
        self.font_tiny   = pygame.font.Font(_fp, 7)    # Timer, ipucu

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
        self.state     = GameState.WAITING_FOR_PLAYER
        self.level     = 1
        self.best      = max((s["level"] for s in load_scores()), default=0)
        self.sequence: list[str] = []
        self.player_index  = 0
        self.phase_started = time.monotonic()
        self.phase_cursor  = 0
        self.last_message  = "Telefon bağlanması bekleniyor"
        self.round_success = False

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
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False
                self._consume_network_events()
                self._update()
                self._draw()
                pygame.display.flip()
                self.clock.tick(60)
        finally:
            self.network.stop()
            pygame.quit()

    # ── Network olayları ─────────────────────────────────────────────────────

    def _consume_network_events(self) -> None:
        while True:
            try:
                msg = self.events.get_nowait()
            except queue.Empty:
                return
            t = msg.get("type")
            if t == "player_connected" and self.state == GameState.WAITING_FOR_PLAYER:
                self.player_connected = True
                self._start_rhazi_turn()
            elif t == "button" and self.state == GameState.PLAYER_TURN:
                self._handle_button(msg.get("button", ""))
            elif t == "button" and msg.get("button") == "reset" and self.state == GameState.GAME_OVER:
                self._reset_game()
            elif t == "player_disconnected":
                self.player_connected = False

    # ── Oyun geçişleri ───────────────────────────────────────────────────────

    def speak_bubble(self, text: str, duration: float = 4.0) -> None:
        """Râzî'nin başı üzerindeki konuşma balonunda metin gösterir."""
        self.bubble_text     = text
        self.bubble_started  = time.monotonic()
        self.bubble_duration = duration

    def _start_rhazi_turn(self) -> None:
        self.round_success = False
        self.sequence      = [random.choice(self.material_pool) for _ in range(self.sequence_length)]
        self.phase_cursor  = 0
        self.phase_started = time.monotonic()
        self.state         = GameState.RHAZI_TURN
        self.last_message  = "Ebû Bekir er-Râzî malzemeleri hazırlıyor..."
        self._spawn_particles(530, 385, GOLD, 18)

        # Kilidi açık malzemeleri telefona bildir (30 elemente kadar)
        unlocked = list(self.material_pool)
        self.network.send({"type": "round_started", "unlocked": unlocked})

        # Yeni element açıldı mı veya bilgi kartı gösterimi
        if len(unlocked) > getattr(self, "last_unlocked_count", 0):
            new_mat = unlocked[-1]
            self.info_card_mat = new_mat
            self.info_card_until = time.monotonic() + 6.0
            self.last_unlocked_count = len(unlocked)
            self.speak_bubble(f"Seviye {self.level}! Yeni malzeme: {MATERIAL_NAMES.get(new_mat, new_mat)}", duration=3.5)
        elif random.random() < 0.4:
            self.info_card_mat = random.choice(unlocked)
            self.info_card_until = time.monotonic() + 5.0
            self.speak_bubble(f"Seviye {self.level}. Sırayı dikkatle takip et!", duration=3.0)
        else:
            self.speak_bubble(f"Seviye {self.level}. Malzemeleri dikkatle izle!", duration=3.0)

    def _reset_game(self) -> None:
        self.level        = 1
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

        if self.state == GameState.RHAZI_TURN:
            if now - self.phase_started >= self.reveal_duration:
                self.phase_started = now
                self.phase_cursor += 1
                if self.phase_cursor >= len(self.sequence):
                    self.state        = GameState.PLAYER_TURN
                    self.player_index = 0
                    self.phase_started = now
                    self._last_tick   = 0.0
                    self.last_message  = "Sıra sende!"
                    self._spawn_particles(530, 385, GREEN, 36)
                    self.network.send({
                        "type": "player_turn",
                        "total": round(self.player_duration, 1),
                    })
                    self.sounds.play("tick")
                    self.speak_bubble("Sıra sende çırak! Malzemeleri sırayla seç.", duration=3.5)
                else:
                    mat = self.sequence[self.phase_cursor]
                    self._spawn_particles(530, 385, COLORS.get(mat, GOLD), 14)
                    self.speak_bubble(f"{MATERIAL_NAMES.get(mat, mat)} ekliyorum...", duration=max(1.0, self.reveal_duration * 0.9))
        elif self.state == GameState.PLAYER_TURN and now - self.phase_started >= self.player_duration:
            self._time_out()
        elif self.state == GameState.RESOLUTION and now - self.phase_started >= 2:
            if self.round_success:
                self._start_rhazi_turn()
            else:
                self._go_game_over()
        elif self.state == GameState.GAME_OVER and now - self.game_over_time >= 4:
            self._return_to_qr_screen()
        elif self.state == GameState.WAITING_FOR_PLAYER:
            # Hiç kimse bağlanmazsa timeout — yeni oda oluştur
            if now - self.wait_started > self.PLAYER_TIMEOUT:
                self._return_to_qr_screen()

    def _time_out(self) -> None:
        self.state        = GameState.RESOLUTION
        self.round_success = False
        self.last_message  = "Süre doldu — Kazan taştı!"
        self.phase_started = time.monotonic()
        self.flash_color   = RED_LT
        self.flash_started = self.phase_started
        self.shake_started = self.phase_started
        # Hata anında aşırı alevlenme (flare surge)
        self.fire_surge_until = time.monotonic() + 3.0
        self._spawn_particles(840, int(self.floor_y - 140), (255, 60, 20), 50)
        self.sounds.play("wrong")
        self.speak_bubble("Vakit doldu çırak! Ateş kontrolden çıktı!", duration=3.5)
        self.network.send({"type": "game_over", "message": self.last_message})

    def _go_game_over(self) -> None:
        save_score(self.level, self.room_id)
        self.best         = max(self.best, self.level)
        self.final_level  = self.level
        self.state        = GameState.GAME_OVER
        self.game_over_time = time.monotonic()
        self.sounds.play("gameover")
        self.speak_bubble(f"Oyun bitti! Seviye {self.final_level}'e kadar gelebildin.", duration=4.0)

    # ── Buton işleme ─────────────────────────────────────────────────────────

    def _handle_button(self, button: str) -> None:
        if button != self.sequence[self.player_index]:
            correct = self.sequence[self.player_index]
            self.state        = GameState.RESOLUTION
            self.round_success = False
            name = MATERIAL_NAMES.get(button, button or "bilinmiyor")
            self.last_message  = f"Yanlış! '{name}' seçildi."
            self.phase_started = time.monotonic()
            self.flash_color   = RED_LT
            self.flash_started = self.phase_started
            self.shake_started = self.phase_started
            # Hata anında aşırı alevlenme (flare surge)
            self.fire_surge_until = time.monotonic() + 3.0
            self._spawn_particles(840, int(self.floor_y - 140), (255, 60, 20), 55)
            self.sounds.play("wrong")
            self.network.send({"type": "game_over", "message": self.last_message})
            
            # Gemini'den Râzî karakteriyle ipucu iste
            self.hint_engine.request(correct, button, self.level)
            
            correct_tr = MATERIAL_NAMES.get(correct, correct)
            self.speak_bubble(f"Eyvah! Yanlış malzeme, doğrusu {correct_tr} idi!", duration=3.5)
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
            self.last_message  = "Doğru! Ebû Bekir er-Râzî onaylıyor."
            self.phase_started = time.monotonic()
            self.level        += 1
            self.flash_color   = GREEN_LT
            self.flash_started = self.phase_started
            self._spawn_particles(560, 350, GOLD, 60)
            self.sounds.play("level_up")
            self.speak_bubble(f"Mükemmel! Seviye {self.level}'e geçtik.", duration=3.0)
        else:
            remaining_count = len(self.sequence) - self.player_index
            self.last_message = f"Doğru  ·  {remaining_count} malzeme kaldı"

    # ── Çizim ────────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self.pixel_surface.fill(BG)
        self.pixel_surface.blit(self.background, (0, 0))

        if self.state == GameState.WAITING_FOR_PLAYER:
            self._draw_waiting_screen()
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
        self._text_shadow("EBÛ BEKİR ER-RÂZÎ'NİN KAZANI", self.font_title, GOLD, (50, 44))
        self._text("Bir hafıza ve dikkat ayini", self.font_medium, TEXT_DIM, (60, 88))

        # QR kutusu
        panel_rect = pygame.Rect(48, 164, 368, 450)
        self._draw_panel(panel_rect, radius=14)
        qr_scaled = pygame.transform.scale(self.qr_surface, (296, 296))
        # Beyaz arka plan QR arka planı için
        pygame.draw.rect(self.pixel_surface, (245, 245, 245), (72, 184, 296, 296), border_radius=6)
        self.pixel_surface.blit(qr_scaled, (72, 184))
        self._text("ODA KODU", self.font_small, TEXT_DIM, (100, 494))
        self._text(self.room_id, self.font_large, GOLD_LT, (80, 516))
        self._text("Aynı Wi-Fi ağında tarayın", self.font_tiny, TEXT_DIM, (68, 554))

        # Sağ panel — talimatlar
        rx = 452
        self._text_shadow("NASIL OYNANIR", self.font_medium, GOLD, (rx, 170))
        self._draw_separator(rx, 200, 610)

        steps = [
            ("1", "QR kodu telefonunla tara"),
            ("2", "Ebû Bekir er-Râzî malzemeleri gösterir"),
            ("3", "Sırayı ezberle"),
            ("4", "Telefondaki butonlarla"),
            ("",  "aynı sırayla bas"),
            ("5", "Her tur daha uzun ve hızlı"),
        ]
        sy = 220
        for num, text in steps:
            if num:
                pygame.draw.circle(self.pixel_surface, GOLD, (rx + 10, sy + 6), 8)
                pygame.draw.circle(self.pixel_surface, PANEL, (rx + 10, sy + 6), 6)
                self._text(num, self.font_tiny, GOLD, (rx + 7, sy + 1))
            self._text(text, self.font_tiny, TEXT if num else TEXT_DIM, (rx + 26, sy))
            sy += 28

        # En iyi skor
        self._draw_separator(rx, sy + 8, 610)
        self._text("EN İYİ SKOR", self.font_small, TEXT_DIM, (rx, sy + 20))
        best_str = f"SEVİYE  {self.best:02d}" if self.best > 0 else "—"
        self._text(best_str, self.font_large, GOLD_LT, (rx, sy + 40))

        # URL
        url_text = f"{PLAY_URL}/{self.room_id}"
        self._text(url_text[:40], self.font_tiny, TEXT_DIM, (rx, HEIGHT - 60))

    # ── Oyun başlık çubuğu ───────────────────────────────────────────────────

    def _draw_game_header(self) -> None:
        # Üst bar
        pygame.draw.rect(self.pixel_surface, PANEL, (0, 0, WIDTH, 78))
        pygame.draw.line(self.pixel_surface, BORDER, (0, 78), (WIDTH, 78), 2)

        # Seviye
        self._text("SEVİYE", self.font_tiny, TEXT_DIM, (24, 14))
        self._text_shadow(f"{self.level:02d}", self.font_title, GOLD_LT, (24, 30))

        # Mesaj — ortada
        msg_surface = self.font_large.render(self.last_message, True, TEXT)
        msg_x = (WIDTH - msg_surface.get_width()) // 2
        self.pixel_surface.blit(msg_surface, (msg_x, 22))

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
                self._spawn_particles(target_x, target_y, color, 5)

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
        overlay.fill((0, 0, 0, 160))
        self.pixel_surface.blit(overlay, (0, 0))

        cx = WIDTH // 2
        # Kutu
        box = pygame.Rect(cx - 260, 180, 520, 320)
        self._draw_panel(box, radius=16)
        pygame.draw.rect(self.pixel_surface, RED, box, 2, border_radius=16)

        # Başlık
        self._text_center("OYUN BİTTİ", self.font_title, RED_LT, cx, 210)
        self._draw_separator(box.x + 20, 248, box.right - 20)

        # Ulaşılan seviye
        self._text_center("ULAŞILAN SEVİYE", self.font_small, TEXT_DIM, cx, 268)
        self._text_center(f"{self.final_level:02d}", self.font_title, GOLD_LT, cx, 294)

        # En iyi skor
        self._draw_separator(box.x + 20, 336, box.right - 20)
        self._text_center(f"EN İYİ  {self.best:02d}", self.font_medium, GOLD, cx, 352)

        # Yeniden başlat ipucu
        self._text_center("Telefondan 'Yeni Oyun' butonuna bas", self.font_tiny, TEXT_DIM, cx, 398)
        secs_left = max(0, 4 - int(time.monotonic() - self.game_over_time))
        self._text_center(f"veya {secs_left}s sonra otomatik", self.font_tiny, TEXT_DIM, cx, 422)

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
        head = self.font_tiny.render("🏺 EBÛ BEKİR ER-RÂZÎ'NİN NOTU", True, (*GOLD_LT, alpha))
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
        """Ekranın alt kısmında Gemini'den gelen Râzî ipucunu göster."""
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
        gem_label = self.font_tiny.render("✦ RÂZİ'NİN İPUCU", True, (*GOLD, alpha))
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

    # ── Parçacıklar ──────────────────────────────────────────────────────────

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
