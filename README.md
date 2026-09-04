# Râzî'nin Kazanı

FastAPI sunucusu ile telefon kontrolcüsünü Pygame masaüstü oyununa bağlayan küçük bir hafıza oyunu.

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

Pygame penceresindeki QR kodu telefonla okut. Telefon ve masaüstü aynı sunucuya erişebilmeli; Render gibi uzak bir sunucuda `SERVER_URL` ve `PLAY_URL` değerlerini dağıtım adresiyle değiştirip WebSocket için `wss://` kullan.

Pygame yerel IP adresini otomatik bulur ve QR koduna örneğin `http://192.168.1.25:8000/play/ABC-123` adresini koyar. Otomatik IP yanlış seçilirse PowerShell'de şu şekilde belirtebilirsin:

```powershell
$env:RAZI_HOST = "192.168.1.25"
py desktop_game.py
```

## Akış

- `WAITING_FOR_PLAYER`: Oda kodu ve QR kodu gösterilir.
- `RHAZI_TURN`: Râzî rastgele malzeme dizisini gösterir.
- `PLAYER_TURN`: Telefon butonları kuyruk üzerinden kontrol edilir.
- `RESOLUTION`: Doğru dizi seviyeyi artırır; hata veya zaman aşımı oyunu bitirir.

Oyun doğru cevaplarla sınırsız ilerler. Seviye yükseldikçe dizi 3'ten 12 elemana kadar büyür, gösterim aralığı 1.5 saniyeden 0.38 saniyeye iner ve oyuncuya ayrılan süre kısalır. Yeni malzemeler kademeli olarak açılır: Demir, Bakır, Fosfor ve Arsenik.
