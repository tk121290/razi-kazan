# Bey Hekim'in Kazanı (Tabîb Ekmeleddin)

FastAPI sunucusu ile telefon kontrolcüsünü Pygame masaüstü oyununa bağlayan, 13. yüzyıl Selçuklu başhekimi ve Hz. Mevlânâ'nın tabibi **Tabîb Ekmeleddin (Bey Hekim)** temalı kadim tıp ve simya hafıza oyunu.

## Özellikler

- **Tek Kişilik Macera & 1v1 Çırak Düellosu:** İster tek kişilik 100 seviyeli kadim iksir yolculuğu, ister iki ayrı telefonla gerçek zamanlı hafıza düellosu!
- **Tabîb Ekmeleddin (Bey Hekim) Başlangıç Anlatımı (Prolog):** Oyun öncesinde Bey Hekim'in sesli/animasyonlu talimatları ve kadim Konya Dârüşşifası hikâyesi.
- **Tezhipli Selçuklu Risalesi (PDF):** Selçuklu turkuazı ve altın tezhip motifleriyle hazırlanmış "Tabîb Ekmeleddin (Bey Hekim) Kimdir?" bilgilendirme risalesi.
- **Erciyes Üniversitesi Künyesi:** Kulüp kayıt, yönetim ekibi ve tıp-bilişim sentezi künye ekranı.
- **Mobil Web Kumandası:** Tarayıcı üzerinden sıfır kurulumla bağlanan antik parşömen temalı telefon kumandası ve dokunsal geri bildirimler (haptic feedback).

## Kurulum

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Çalıştırma

Bir terminalde (telefonun aynı Wi-Fi ağından erişebilmesi için `0.0.0.0` kullanılır):

```powershell
py -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Başka bir terminalde:

```powershell
py desktop_game.py
```

Pygame penceresindeki QR kodu telefonla okut. Telefon ve masaüstü aynı sunucuya erişebilmeli; Render veya uzak bir sunucuda `SERVER_URL` ve `PLAY_URL` değerlerini dağıtım adresiyle değiştirip WebSocket için `wss://` kullan.

Pygame yerel IP adresini otomatik bulur ve QR koduna örneğin `http://192.168.1.25:8000/play/ABC-123` adresini koyar. Otomatik IP yanlış seçilirse PowerShell'de şu şekilde belirtebilirsin:

```powershell
$env:BEYHEKIM_HOST = "192.168.1.25"
py desktop_game.py
```

## Akış

- `MODE_SELECT`: Tek Kişilik veya 1v1 Çırak Düellosu mod seçimi.
- `WAITING_FOR_PLAYER`: Oda kodu ve QR kodu gösterilir.
- `PROLOGUE`: Tabîb Ekmeleddin talimatları aktarır; hem ekrandan hem telefondan "BAŞLA" tuşuna basılarak oyun başlatılır.
- `RHAZI_TURN` (Bey Hekim Sırası): Bey Hekim şifalı cevherleri sırayla kazana atar.
- `PLAYER_TURN`: Telefon butonları üzerinden aynı sıra kazana eklenir.
- `RESOLUTION`: Doğru dizi seviyeyi/raundu kazandırır; 3 can hakkı ve kombo sistemi mevcuttur.
- `CREDITS_VIEW`: Erciyes kulüp kaydı ve Tabîb Ekmeleddin tezhipli PDF risalesi.
