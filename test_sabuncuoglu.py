r"""
✦ Tabîb Ekmeleddin & Sabuncuoğlu Şerefeddin - Canlı Sahne Testi ✦

Çalıştırmak için terminalden:
    .\.venv\Scripts\python.exe test_sabuncuoglu.py
veya çift tıklayarak:
    test_sabuncuoglu.bat
"""

from __future__ import annotations

import os
import sys
import time

# UTF-8 stdout/stderr
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pygame

# Tam ekran yerine 1100x700 masaüstü pencere modu
_orig_set_mode = pygame.display.set_mode
def _windowed_set_mode(size, flags=0, *args, **kwargs):
    return _orig_set_mode((1100, 700), pygame.RESIZABLE)
pygame.display.set_mode = _windowed_set_mode

from desktop_game import (
    Game,
    GameMode,
    GameState,
    SabuncuogluActor,
    SabuncuogluState,
    WIDTH,
    HEIGHT,
)

def main():
    print("=" * 65)
    print("  ✦ SABUNCUOĞLU ŞEREFEDDİN CANLI SAHNE VE ANİMASYON TESTİ ✦")
    print("=" * 65)
    print("Kontroller:")
    print("  [1]     : Sabuncuoğlu Tersten Doldurma Sınavı (Tek Kişilik)")
    print("  [2]     : Sabuncuoğlu Misafir Ziyareti (Selam & El Sallama)")
    print("  [3]     : Sabuncuoğlu Düello Modu Ters Sınavı")
    print("  [SPACE] : Tur Başarısı (Aferin & Uğurlama)")
    print("  [X]     : Tur Başarısızlığı / Can Kaybı (Teselli & Uğurlama)")
    print("  [ESC]   : Pencereyi Kapat")
    print("=" * 65)

    pygame.display.set_caption("Tabîb Ekmeleddin & Sabuncuoğlu Şerefeddin - Canlı Sahne Testi")

    game = Game()
    game.display_scale = 1.0
    game.display_width = WIDTH
    game.display_height = HEIGHT
    game.display_offset = (0, 0)

    # Başlangıç durumu: Tek kişilik Seviye 13 Tersten Doldurma Sınavı
    game.mode = GameMode.SINGLE
    game.state = GameState.RHAZI_TURN
    game.level = 13
    game.lives = 3
    game.is_reverse_round = True
    game.sequence = ["demir", "kalay", "kursun"]
    game.sabuncuoglu.start_reverse_challenge(is_duel=False, level=13)

    running = True
    font_help = pygame.font.SysFont("segoeui,arial", 13, bold=True)
    font_badge = pygame.font.SysFont("segoeui,arial", 12)

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_1:
                    # 1: Tek Kişilik Tersten Doldurma Sınavı
                    game.mode = GameMode.SINGLE
                    game.state = GameState.RHAZI_TURN
                    game.level = 13
                    game.is_reverse_round = True
                    game.sequence = ["demir", "kalay", "kursun"]
                    game.sabuncuoglu.reset()
                    game.sabuncuoglu.start_reverse_challenge(is_duel=False, level=13)
                    game.speak_bubble("Sabuncuoğlu Şerefeddin sahneye geldi! Tersten sınavı başlattı.", duration=3.5)
                elif event.key == pygame.K_2:
                    # 2: Misafir Ziyareti (Selam & El Sallama)
                    game.mode = GameMode.SINGLE
                    game.state = GameState.RHAZI_TURN
                    game.is_reverse_round = False
                    game.sabuncuoglu.reset()
                    game.sabuncuoglu.start_guest_visit(is_duel=False, level=8)
                    game.speak_bubble("Amasya'dan Şerefeddin Sabuncuoğlu çırakları teftişe geldi!", duration=3.5)
                elif event.key == pygame.K_3:
                    # 3: Düello Modunda Tersten Sınav (x = 180, Bey Hekim x = 420)
                    game.mode = GameMode.DUEL
                    game.state = GameState.DUEL_ROUND
                    game.duel_round = 13
                    game.is_reverse_round = True
                    game.sequence = ["civa", "tuz", "demir", "kalay"]
                    game.sabuncuoglu.reset()
                    game.sabuncuoglu.start_reverse_challenge(is_duel=True, level=13)
                    game.speak_bubble("1v1 Düello Meydanı! Sabuncuoğlu her iki çırağı sınıyor.", duration=3.5)
                elif event.key == pygame.K_SPACE:
                    # SPACE: Tur Başarısı
                    game.sabuncuoglu.on_round_end(success=True)
                    game.sounds.play("level_up")
                elif event.key == pygame.K_x:
                    # X: Tur Başarısızlığı
                    game.sabuncuoglu.on_round_end(success=False)
                    game.sounds.play("wrong")

        # Oyun mantığını ve aktörleri güncelle
        game._update()

        # Ekranı çiz
        game._draw()

        # Üst şeride mini tuş rehberi paneli çiz
        overlay = pygame.Surface((490, 48), pygame.SRCALPHA)
        pygame.draw.rect(overlay, (15, 20, 18, 225), (0, 0, 490, 48), border_radius=8)
        pygame.draw.rect(overlay, (214, 168, 72), (0, 0, 490, 48), 1, border_radius=8)

        t1 = font_help.render("✦ TEST KONTROLLERİ: [1] Ters Sınav  [2] Misafir Ziyareti  [3] Düello", True, (245, 215, 110))
        t2 = font_badge.render("[SPACE] Başarılı Uğurla  |  [X] Can Kaybı  |  [ESC] Kapat", True, (190, 210, 195))
        overlay.blit(t1, (10, 6))
        overlay.blit(t2, (10, 26))
        game.pixel_surface.blit(overlay, (WIDTH - 505, 12))

        # Ekran ölçeklemesi ve pencereye yansıtma
        sw, sh = game.screen.get_size()
        if sw != WIDTH or sh != HEIGHT:
            scaled = pygame.transform.smoothscale(game.pixel_surface, (sw, sh))
            game.screen.blit(scaled, (0, 0))
        else:
            game.screen.blit(game.pixel_surface, (0, 0))

        pygame.display.flip()
        game.clock.tick(60)

    pygame.quit()
    print("Test penceresi kapatıldı.")

if __name__ == "__main__":
    main()
