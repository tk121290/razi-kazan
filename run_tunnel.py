"""
Tabîb Ekmeleddin'in Kazanı — Cloudflare Tunnel Başlatıcı
Bu betik:
1. 'cloudflared' uygulamasını kontrol eder (yoksa resmi GitHub adresinden tek tuşla indirir).
2. FastAPI sunucusunu (server.py) yerel olarak başlatır.
3. Ücretsiz, SSL korumalı bir Cloudflare Tüneli açar (https://xxx.trycloudflare.com).
4. Tünel URL'sini yakalayarak oyunun QR koduna enjekte eder.
5. Pygame oyun penceresini (desktop_game.py) açar.
6. Oyun penceresi kapandığında sunucuyu ve tüneli arka planda temiz bir şekilde kapatır.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON_BIN = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
LOCAL_PORT = 8000
CLOUDFLARED_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"


def find_or_download_cloudflared() -> str | None:
    # 1. Sistem PATH kontrolü
    system_path = shutil.which("cloudflared")
    if system_path:
        return system_path

    # 2. Proje klasöründe var mı?
    local_binary = BASE_DIR / "cloudflared.exe"
    if local_binary.exists():
        return str(local_binary)

    # 3. Bulunamadı — Kullanıcıya otomatik indirme yap
    print("=" * 65)
    print("  [BİLGİ] 'cloudflared' (Cloudflare Tünel Motoru) bulunamadı.")
    print("  Oyunun aynı WiFi olmadan internetten oynanabilmesi için")
    print("  resmi Cloudflare yürütücüsü indiriliyor...")
    print("=" * 65)
    print(f"Kaynak: {CLOUDFLARED_DOWNLOAD_URL}")

    try:
        def _report(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(100, int(downloaded * 100 / total_size))
                sys.stdout.write(f"\r  İlerleme: %{percent} ({downloaded // 1048576}MB / {total_size // 1048576}MB)")
                sys.stdout.flush()

        urllib.request.urlretrieve(CLOUDFLARED_DOWNLOAD_URL, str(local_binary), reporthook=_report)
        print("\n  ✓ 'cloudflared.exe' başarıyla indirildi ve hazırlandı!\n")
        return str(local_binary)
    except Exception as e:
        print(f"\n  [HATA] Otomatik indirme başarısız oldu: {e}")
        print("  Alternatif olarak PowerShell'de şu komutu çalıştırabilirsiniz:")
        print("  winget install --id Cloudflare.cloudflared -e")
        return None


def main() -> int:
    os.chdir(str(BASE_DIR))
    cf_bin = find_or_download_cloudflared()
    if not cf_bin:
        input("\nDevam etmek için Enter'a basın...")
        return 1

    print("\n" + "=" * 65)
    print("  ✦ TABÎB EKMELEDDİN'İN KAZANI — CLOUDFLARE İNTERNET YAYINI ✦")
    print("=" * 65)

    # 1. FastAPI sunucusunu başlat
    print("[1/3] Oyun sunucusu (server.py) başlatılıyor...")
    server_proc = subprocess.Popen(
        [PYTHON_BIN, "server.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(BASE_DIR),
    )
    time.sleep(1.5)

    # 2. Cloudflare Tünelini başlat ve URL'yi yakala
    print("[2/3] Cloudflare güvenli tüneli kuruluyor...")
    tunnel_cmd = [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{LOCAL_PORT}"]
    tunnel_proc = subprocess.Popen(
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(BASE_DIR),
    )

    public_url = None
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    start_time = time.time()

    while time.time() - start_time < 25.0:
        line = tunnel_proc.stdout.readline() if tunnel_proc.stdout else ""
        if not line and tunnel_proc.poll() is not None:
            break
        match = url_pattern.search(line)
        if match:
            public_url = match.group(0)
            break

    if not public_url:
        print("[HATA] Cloudflare tünel URL'i alınamadı. İnternet bağlantınızı kontrol edin.")
        server_proc.terminate()
        tunnel_proc.terminate()
        return 1

    play_url = f"{public_url}/play"

    print("\n" + "╔" + "═" * 63 + "╗")
    print("║   İNTERNET YAYINI BAŞARIYLA AKTİF EDİLDİ!                     ║")
    print("╠" + "═" * 63 + "╣")
    print(f"║  Telefon Linki : {play_url:<43} ║")
    print("║  Ağ Kapsamı    : Tüm Dünya (4.5G / 5G / Farklı WiFi'lar)      ║")
    print("║  Durum         : SSL Sertifikalı ve Güvenli (HTTPS/WSS)       ║")
    print("╚" + "═" * 63 + "╝\n")

    # 3. Oyun ortam değişkenini ayarla ve oyunu başlat
    print("[3/3] Pygame oyun penceresi açılıyor...")
    env = os.environ.copy()
    env["RAZI_PLAY_URL"] = play_url

    try:
        subprocess.run([PYTHON_BIN, "desktop_game.py"], env=env, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        pass
    finally:
        print("\nOyun kapatıldı. Tünel ve yerel sunucu temizleniyor...")
        try:
            tunnel_proc.terminate()
            tunnel_proc.wait(timeout=2)
        except Exception:
            tunnel_proc.kill()

        try:
            server_proc.terminate()
            server_proc.wait(timeout=2)
        except Exception:
            server_proc.kill()

        print("✓ Tüm süreçler güvenle sonlandırıldı.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
