from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import socket
import threading
import time
from enum import Enum, auto

import pygame
import qrcode
import websockets

PORT = 8000


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
MATERIALS = ("civa", "kukurt", "antimon", "tuz", "demir", "bakir", "fosfor", "arsenik")
MATERIAL_NAMES = {
    "civa": "Cıva",
    "kukurt": "Kükürt",
    "antimon": "Antimon",
    "tuz": "Tuz",
    "demir": "Demir",
    "bakir": "Bakır",
    "fosfor": "Fosfor",
    "arsenik": "Arsenik",
}
COLORS = {
    "civa": (124, 164, 185),
    "kukurt": (215, 164, 50),
    "antimon": (147, 104, 91),
    "tuz": (168, 183, 155),
    "demir": (151, 117, 101),
    "bakir": (191, 103, 62),
    "fosfor": (111, 208, 133),
    "arsenik": (156, 125, 190),
}
BG = (23, 18, 19)
PANEL = (43, 31, 27)
TEXT = (245, 236, 215)
GOLD = (224, 185, 94)


class GameState(Enum):
    WAITING_FOR_PLAYER = auto()
    RHAZI_TURN = auto()
    PLAYER_TURN = auto()
    RESOLUTION = auto()
    GAME_OVER = auto()


def make_room_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(random.choice(alphabet) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"


class NetworkBridge:
    def __init__(self, server_url: str, room_id: str, events: queue.Queue[dict[str, str]]) -> None:
        self.server_url = server_url.rstrip("/")
        self.room_id = room_id
        self.events = events
        self.outgoing: queue.Queue[dict[str, str]] = queue.Queue()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        self.thread.join(timeout=2)

    def send(self, message: dict[str, str]) -> None:
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
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                        
            except (OSError, websockets.WebSocketException):
                if not self.stop_requested.is_set():
                    self.events.put({"type": "server_disconnected"})
                    await asyncio.sleep(1)


def decorate_master(frame: pygame.Surface):
    # Master sprite: head visible at y=24-35, x=36-50 (behind table)
    # The character already has white hair/beard - just add a green turban wrap band
    # Draw green turban stripe across the head - y=24 is the top of the head area
    # Turban sits on TOP of the white hair at y=24-28, x=35-51
    # We only add a colored wrap, the white part underneath stays as the turban dome
    pygame.draw.rect(frame, (70, 130, 90), (35, 27, 16, 3))   # Teal-green turban band
    pygame.draw.rect(frame, (80, 150, 100), (33, 28, 2, 3))   # Side tuck left
    pygame.draw.rect(frame, (80, 150, 100), (51, 28, 2, 3))   # Side tuck right

def decorate_forge(frame: pygame.Surface):
    # Forge chimney runs from y=14 to y=46 (width ~13px at x=24-36)
    # Erase the chimney (top portion) completely
    frame.fill((0, 0, 0, 0), (0, 0, 64, 47))
    
    # Now draw a round iron cauldron above the forge dome (the dome starts ~y=47)
    # Cauldron body: centered at x=32, sits at y=28-47 overlapping the dome edge
    c_x, c_y, c_w, c_h = 8, 30, 48, 28
    # Outer dark iron body
    pygame.draw.ellipse(frame, (28, 30, 33), (c_x, c_y, c_w, c_h))
    # Inner shading (lighter)
    pygame.draw.ellipse(frame, (45, 48, 52), (c_x+5, c_y+5, c_w-10, c_h-10))
    # Specular highlight top-left
    pygame.draw.rect(frame, (65, 68, 72), (c_x+8, c_y+6, 8, 4))
    # Rim (wide flat ellipse on top)
    pygame.draw.ellipse(frame, (40, 42, 46), (c_x-4, c_y-5, c_w+8, 14))
    pygame.draw.ellipse(frame, (18, 20, 22), (c_x, c_y-3, c_w, 10))
    # Glowing green liquid in cauldron
    pygame.draw.ellipse(frame, (30, 185, 80), (c_x+2, c_y-2, c_w-4, 8))
    pygame.draw.ellipse(frame, (80, 230, 130), (c_x+12, c_y-3, 12, 4))
    # Bubble highlights
    pygame.draw.circle(frame, (150, 255, 180), (20, c_y-1), 2)
    pygame.draw.circle(frame, (120, 240, 160), (38, c_y-2), 1)
    pygame.draw.circle(frame, (180, 255, 200), (50, c_y), 1)

class SpriteAnimation:
    def __init__(self, filepath: str, frame_width: int, frame_height: int, scale: int = 4, decorator=None):
        self.frames = []
        try:
            sheet = pygame.image.load(filepath).convert_alpha()
            sheet_width = sheet.get_width()
            for x in range(0, sheet_width, frame_width):
                frame = sheet.subsurface(pygame.Rect(x, 0, frame_width, frame_height)).copy()
                if decorator:
                    decorator(frame)
                frame = pygame.transform.scale(frame, (frame_width * scale, frame_height * scale))
                self.frames.append(frame)
        except Exception as e:
            print(f"Failed to load sprite {filepath}: {e}")
            
    def get_frame(self, time_sec: float, fps: float = 8.0) -> pygame.Surface:
        if not self.frames:
            return None
        frame_idx = int(time_sec * fps) % len(self.frames)
        return self.frames[frame_idx]

class Game:
    def __init__(self, server_url: str = "ws://localhost:8000"):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Râzî'nin Kazanı")
        self.clock = pygame.time.Clock()
        self.pixel_surface = pygame.Surface((WIDTH, HEIGHT))
        
        # Pixel art font — PressStart2P
        _font_path = os.path.join(os.path.dirname(__file__), "assets", "PressStart2P.ttf")
        self.title_font = pygame.font.Font(_font_path, 22)
        self.font       = pygame.font.Font(_font_path, 14)
        self.small_font = pygame.font.Font(_font_path, 10)
        
        # Sprite Animations
        self.anim_master = SpriteAnimation("assets/PNG/Master_Idle.png", 64, 80, scale=6, decorator=decorate_master)
        self.anim_forge = SpriteAnimation("assets/PNG/Forge.png", 64, 96, scale=5, decorator=decorate_forge)
        
        self.events: queue.Queue[dict[str, str]] = queue.Queue()
        self.room_id = make_room_id()
        self.state = GameState.WAITING_FOR_PLAYER
        self.level = 1
        self.sequence: list[str] = []
        self.player_index = 0
        self.phase_started = time.monotonic()
        self.phase_cursor = 0
        self.last_message = "Telefonun bağlanması bekleniyor"
        self.particles: list[dict[str, float | tuple[int, int, int]]] = []
        self.flash_color = (0, 0, 0)
        self.flash_started = 0.0
        self.shake_started = 0.0
        self.ambient_clock = time.monotonic()
        self.animation_clock = time.monotonic()
        self.round_success = False
        self.qr_surface = self._make_qr()
        self.network = NetworkBridge(SERVER_URL, self.room_id, self.events)
        self.background = self._make_background()

    def _make_qr(self) -> pygame.Surface:
        import io
        image = qrcode.make(f"{PLAY_URL}/{self.room_id}").convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return pygame.image.load(buffer).convert()

    def run(self) -> None:
        self.network.start()
        running = True
        try:
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                self._consume_network_events()
                self._update()
                self._draw()
                pygame.display.flip()
                self.clock.tick(60)
        finally:
            self.network.stop()
            pygame.quit()

    def _consume_network_events(self) -> None:
        while True:
            try:
                message = self.events.get_nowait()
            except queue.Empty:
                return
            message_type = message.get("type")
            if message_type == "player_connected" and self.state == GameState.WAITING_FOR_PLAYER:
                self._start_rhazi_turn()
            elif message_type == "button" and self.state == GameState.PLAYER_TURN:
                self._handle_button(message.get("button", ""))
            elif message_type == "button" and message.get("button") == "reset" and self.state == GameState.GAME_OVER:
                self._reset_game()

    def _start_rhazi_turn(self) -> None:
        self.round_success = False
        self.sequence = [random.choice(self.material_pool) for _ in range(self.sequence_length)]
        self.phase_cursor = 0
        self.phase_started = time.monotonic()
        self.state = GameState.RHAZI_TURN
        self.last_message = "Râzî malzemeleri hazırlıyor..."
        self._spawn_particles(530, 385, (224, 185, 94), 18)
        self.network.send({"type": "round_started"})

    def _reset_game(self) -> None:
        self.level = 1
        self.sequence = []
        self.player_index = 0
        self.phase_cursor = 0
        self.state = GameState.WAITING_FOR_PLAYER
        self.phase_started = time.monotonic()
        self.last_message = "Yeni oyun hazırlanıyor..."
        self._start_rhazi_turn()

    @property
    def sequence_length(self) -> int:
        return min(3 + (self.level - 1) // 2, 12)

    @property
    def material_pool(self) -> tuple[str, ...]:
        unlocked = min(4 + (self.level - 1) // 2, len(MATERIALS))
        return MATERIALS[:unlocked]

    @property
    def reveal_duration(self) -> float:
        return max(0.38, 1.5 - (self.level - 1) * 0.1)

    @property
    def player_duration(self) -> float:
        return max(5.0, len(self.sequence) * 2.5 - (self.level - 1) * 0.2)

    def _update(self) -> None:
        now = time.monotonic()
        if now - self.ambient_clock > 0.18:
            self.ambient_clock = now
            self._spawn_particles(random.randint(170, 930), random.randint(185, 520), (180, 145, 94), 1)
        self._update_particles()
        if self.state == GameState.RHAZI_TURN:
            if now - self.phase_started >= self.reveal_duration:
                self.phase_started = now
                self.phase_cursor += 1
                if self.phase_cursor >= len(self.sequence):
                    self.state = GameState.PLAYER_TURN
                    self.player_index = 0
                    self.phase_started = now
                    self.last_message = "Sıra sende!"
                    self._spawn_particles(530, 385, (99, 201, 151), 36)
                    self.network.send({"type": "player_turn"})
                else:
                    self._spawn_particles(530, 385, COLORS[self.sequence[self.phase_cursor]], 14)
        elif self.state == GameState.PLAYER_TURN and now - self.phase_started >= self.player_duration:
            self.state = GameState.RESOLUTION
            self.round_success = False
            self.last_message = "Süre doldu. Kazan patladı."
            self.phase_started = now
            self.flash_color = (215, 95, 75)
            self.flash_started = now
            self.shake_started = now
            self._spawn_particles(560, 350, (215, 95, 75), 42)
        elif self.state == GameState.RESOLUTION and now - self.phase_started >= 2:
            if self.round_success:
                self._start_rhazi_turn()
            else:
                self.state = GameState.GAME_OVER
                self.game_over_time = time.monotonic()
                self.network.send({"type": "game_over", "message": self.last_message})

    def _handle_button(self, button: str) -> None:
        if button != self.sequence[self.player_index]:
            self.state = GameState.RESOLUTION
            self.round_success = False
            self.last_message = f"Yanlış malzeme: {button or 'bilinmiyor'}"
            self.phase_started = time.monotonic()
            self.flash_color = (215, 95, 75)
            self.flash_started = self.phase_started
            self.shake_started = self.phase_started
            self._spawn_particles(560, 350, (215, 95, 75), 42)
            return
        self.player_index += 1
        self._spawn_particles(560, 350, (99, 201, 151), 12)
        if self.player_index == len(self.sequence):
            self.state = GameState.RESOLUTION
            self.round_success = True
            self.last_message = "Doğru! Râzî onaylıyor."
            self.phase_started = time.monotonic()
            self.level += 1
            self.flash_color = (99, 201, 151)
            self.flash_started = self.phase_started
            self._spawn_particles(560, 350, (224, 185, 94), 54)
        else:
            self.last_message = f"Doğru. {len(self.sequence) - self.player_index} kaldı."

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
                self._text(f"GÖZLEM  /  {material.upper()}", self.font, COLORS[material], (50, 570))
            elif self.state == GameState.PLAYER_TURN:
                remaining = max(0, self.player_duration - (time.monotonic() - self.phase_started))
                self._draw_timer(remaining)
                self._text(f"SIRA SENDE  /  {self.player_index + 1}. MALZEME", self.font, (99, 201, 151), (50, 570))
            elif self.state == GameState.RESOLUTION:
                resolution_color = (99, 201, 151) if "Doğru" in self.last_message else (215, 95, 75)
                self._text("KAYIT ALINDI", self.font, resolution_color, (50, 570))
            elif self.state == GameState.GAME_OVER:
                self._text("OYUN BİTTİ", self.font, (215, 95, 75), (50, 570))
        self._draw_particles()
        self._draw_flash()
        self.screen.blit(self.pixel_surface, (0, 0))

    def _draw_sprites(self) -> None:
        now = time.monotonic()
        
        # Draw Master
        master_frame = self.anim_master.get_frame(now, 10)
        if master_frame:
            rect = master_frame.get_rect(midbottom=(400, 580))
            self.pixel_surface.blit(master_frame, rect)
            
        # Draw Forge (Cauldron replacement)
        forge_frame = self.anim_forge.get_frame(now, 8)
        if forge_frame:
            rect = forge_frame.get_rect(midbottom=(750, 580))
            self.pixel_surface.blit(forge_frame, rect)

    def _make_background(self) -> pygame.Surface:
        surface = pygame.Surface((WIDTH, HEIGHT))
        
        # Stone wall — darker, more earthy alchemist dungeon feel
        wall_dark  = (42, 44, 50)
        wall_mid   = (55, 57, 65)
        wall_light = (68, 70, 80)
        brick_w, brick_h = 80, 36
        for row in range(0, 520 // brick_h + 1):
            for col in range(-1, WIDTH // brick_w + 2):
                offset = (brick_w // 2) if row % 2 == 1 else 0
                bx = col * brick_w - offset
                by = row * brick_h
                pygame.draw.rect(surface, wall_dark,  (bx, by, brick_w, brick_h))
                pygame.draw.rect(surface, wall_mid,   (bx+2, by+2, brick_w-4, brick_h-4))
                pygame.draw.rect(surface, wall_light, (bx+3, by+3, 6, 4))  # corner highlight
        
        # Wooden floor planks
        for row in range(0, HEIGHT - 510):
            by = 510 + row * 20
            for col in range(-1, WIDTH // 160 + 2):
                offset = 80 if row % 2 == 1 else 0
                bx = col * 160 - offset
                pygame.draw.rect(surface, (72, 47, 28), (bx, by, 158, 18))
                pygame.draw.rect(surface, (55, 35, 20), (bx, by, 158, 18), 2)
                pygame.draw.line(surface, (85, 55, 30), (bx+10, by+4), (bx+140, by+4), 1)

        # Floor/wall shadow transition
        shadow_surf = pygame.Surface((WIDTH, 30), pygame.SRCALPHA)
        shadow_surf.fill((0, 0, 0, 120))
        surface.blit(shadow_surf, (0, 500))
        
        # ── Potion shelves (left side: x=30, right side: x=820) ──
        shelf_color  = (80, 52, 30)
        shelf_edge   = (55, 35, 20)
        shelf_height = 14
        
        # Helper: draw one shelf with potions
        def draw_shelf(sx, sy, mat_list):
            # shelf board
            pygame.draw.rect(surface, shelf_color, (sx, sy, 200, shelf_height))
            pygame.draw.rect(surface, shelf_edge,  (sx, sy + shelf_height, 200, 4))
            # bracket shadows
            pygame.draw.line(surface, shelf_edge, (sx+10, sy+shelf_height+4), (sx+10, sy+shelf_height+16), 3)
            pygame.draw.line(surface, shelf_edge, (sx+190, sy+shelf_height+4), (sx+190, sy+shelf_height+16), 3)
            # potions on the shelf
            bottle_w = 18
            for i, mat in enumerate(mat_list):
                px = sx + 8 + i * (bottle_w + 5)
                if px + bottle_w > sx + 200:
                    break
                col = COLORS[mat]
                # bottle body
                pygame.draw.rect(surface, col, (px, sy - 24, bottle_w, 22), border_radius=4)
                # glass shine
                pygame.draw.rect(surface, (220, 235, 245), (px+2, sy-22, 4, 6))
                # neck
                pygame.draw.rect(surface, (180, 200, 210), (px+5, sy-30, 8, 7))
                pygame.draw.rect(surface, col, (px+6, sy-28, 6, 4))
                # cork
                pygame.draw.rect(surface, (130, 85, 45), (px+4, sy-33, 10, 4))
        
        mat_keys = list(COLORS.keys())
        
        # Left shelves
        draw_shelf(30, 160, mat_keys)
        draw_shelf(30, 280, mat_keys[3:] + mat_keys[:3])
        draw_shelf(30, 400, mat_keys[1:] + mat_keys[:1])
        
        # Right shelves
        draw_shelf(820, 160, mat_keys[2:] + mat_keys[:2])
        draw_shelf(820, 280, mat_keys)
        draw_shelf(820, 400, mat_keys[4:] + mat_keys[:4])

        # Warm candle glow (atmospheric light)
        for gx, gy in ((150, 320), (980, 320)):
            glow = pygame.Surface((300, 300), pygame.SRCALPHA)
            pygame.draw.circle(glow, (140, 90, 40, 35), (150, 150), 150)
            pygame.draw.circle(glow, (180, 120, 50, 25), (150, 150), 80)
            surface.blit(glow, (gx - 150, gy - 150))
            
        return surface



    def _draw_waiting_screen(self) -> None:
        self._text("RÂZÎ'NİN KAZANI", self.title_font, GOLD, (55, 46))
        self._text("Bir hafıza ve dikkat ayini", self.font, (190, 178, 155), (60, 120))
        pygame.draw.rect(self.pixel_surface, PANEL, (55, 190, 365, 430), border_radius=12)
        pygame.draw.rect(self.pixel_surface, (111, 79, 49), (55, 190, 365, 430), 2, border_radius=12)
        self.pixel_surface.blit(pygame.transform.scale(self.qr_surface, (300, 300)), (88, 220))
        self._text("TELEFONUNU BAĞLA", self.font, TEXT, (470, 250))
        self._text(f"ODA  {self.room_id}", self.font, GOLD, (470, 305))
        self._text("Aynı Wi-Fi ağında QR kodu okut.", self.small_font, (190, 178, 155), (470, 360))
        self._text("Râzî hazır olduğunda deney başlayacak.", self.small_font, (190, 178, 155), (470, 392))

    def _draw_game_header(self) -> None:
        self._text(f"SEVİYE {self.level:02d}", self.font, GOLD, (50, 35))
        self._text(self.last_message, self.title_font, TEXT, (50, 95))
        pygame.draw.line(self.pixel_surface, (111, 79, 49), (50, 185), (1050, 185), 2)



    def _draw_material_animation(self, material: str) -> None:
        progress = min(1.0, (time.monotonic() - self.phase_started) / self.reveal_duration)
        
        # Yeni Sprite animasyonuna göre koordinatlar (Master x=400, Forge x=750)
        hand_x, hand_y = 400, 420
        target_x, target_y = 750, 480
        
        x = int(hand_x + (target_x - hand_x) * progress)
        y = int(hand_y + (target_y - hand_y) * progress - 150 * (4 * progress * (1 - progress)))
        color = COLORS[material]
        
        # Draw pixel art potion bottle
        pw, ph = 24, 32
        bottle = pygame.Surface((pw, ph), pygame.SRCALPHA)
        # Liquid
        pygame.draw.rect(bottle, color, (4, 16, 16, 16), border_radius=4)
        # Glass outline/highlight
        pygame.draw.rect(bottle, (200, 220, 230, 150), (4, 12, 16, 20), 2, border_radius=4)
        # Neck
        pygame.draw.rect(bottle, (200, 220, 230, 200), (8, 4, 8, 8), 2)
        pygame.draw.rect(bottle, color, (10, 8, 4, 4)) # small liquid in neck
        # Cork
        pygame.draw.rect(bottle, (120, 80, 40), (9, 0, 6, 4))
        
        self.pixel_surface.blit(bottle, (x - pw//2, y - ph//2))
        if 0.82 < progress < 1:
            self._spawn_particles(x, y + 12, color, 1)
        self._text(MATERIAL_NAMES[material].upper(), self.small_font, TEXT, (x - 38, y - 48))

    def _draw_timer(self, remaining: float) -> None:
        ratio = max(0.0, remaining / self.player_duration)
        pygame.draw.rect(self.pixel_surface, (56, 44, 37), (50, 610, 1000, 12), border_radius=6)
        pygame.draw.rect(self.pixel_surface, (99, 201, 151) if ratio > 0.3 else (215, 95, 75), (50, 610, int(1000 * ratio), 12), border_radius=6)

    def _spawn_particles(self, x: float, y: float, color: tuple[int, int, int], count: int) -> None:
        for _ in range(count):
            angle = random.random() * 6.283
            speed = random.uniform(35, 150)
            self.particles.append({"x": x, "y": y, "vx": pygame.math.Vector2(1, 0).rotate_rad(angle).x * speed, "vy": pygame.math.Vector2(1, 0).rotate_rad(angle).y * speed, "life": random.uniform(0.45, 1.2), "size": random.uniform(2, 6), "color": color})

    def _update_particles(self) -> None:
        delta = 1 / 60
        for particle in self.particles:
            particle["x"] = float(particle["x"]) + float(particle["vx"]) * delta
            particle["y"] = float(particle["y"]) + float(particle["vy"]) * delta
            particle["vy"] = float(particle["vy"]) + 80 * delta
            particle["life"] = float(particle["life"]) - delta
        self.particles = [particle for particle in self.particles if float(particle["life"]) > 0]

    def _draw_particles(self) -> None:
        for particle in self.particles:
            color = particle["color"]
            size = max(2, int(float(particle["size"])))
            pygame.draw.circle(self.pixel_surface, color, (int(float(particle["x"])), int(float(particle["y"]))), size)

    def _draw_flash(self) -> None:
        if not self.flash_started:
            return
        elapsed = time.monotonic() - self.flash_started
        if elapsed >= 0.45:
            return
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        alpha = int(90 * ((1 - elapsed / 0.45) ** 1.5))
        overlay.fill((*self.flash_color, alpha))
        self.pixel_surface.blit(overlay, (0, 0))

    def _text(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], position: tuple[int, int]) -> None:
        self.pixel_surface.blit(font.render(text, True, color), position)


if __name__ == "__main__":
    Game().run()
