"""
Ebû Bekir er-Râzî'nin "Tıbbiyeli Bir Dostuna Nasihatler" risalesini
ReportLab ile altın yaldızlı, şık ve Türkçe karakter destekli PDF olarak üretir.
"""

from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

BASE_DIR = Path(__file__).parent
OUTPUT_PDF = BASE_DIR / "assets" / "tibbiye_nasihatleri.pdf"

# Renk paleti
C_GOLD = HexColor("#b8860b")
C_GOLD_LT = HexColor("#d4a848")
C_INK = HexColor("#221710")
C_MUTED = HexColor("#5a4838")
C_BG_ACCENT = HexColor("#fdfaf3")
C_BORDER = HexColor("#8c6239")


def register_fonts() -> str:
    """Türkçe karakter destekli sistem fontunu kaydeder."""
    font_candidates = [
        ("C:/Windows/Fonts/georgia.ttf", "Georgia"),
        ("C:/Windows/Fonts/arial.ttf", "Arial"),
        ("C:/Windows/Fonts/segoeui.ttf", "SegoeUI"),
    ]
    for path, name in font_candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                bold_path = path.replace(".ttf", "bd.ttf") if "georgia" in path or "arial" in path else path
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold_path))
                return name
            except Exception:
                pass
    return "Helvetica"


def draw_background_and_border(canvas, doc):
    """Her sayfaya tarihi tezhip çerçevesi ve kenar süsü çizer."""
    canvas.saveState()
    w, h = doc.pagesize

    # Hafif parşömen arka planı
    canvas.setFillColor(HexColor("#faf6ee"))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)

    # Dış altın çerçeve
    canvas.setStrokeColor(C_GOLD)
    canvas.setLineWidth(2)
    canvas.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm)

    # İç ince kahve çerçeve
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.7)
    canvas.rect(14.5 * mm, 14.5 * mm, w - 29 * mm, h - 29 * mm)

    # Köşe süsleri
    for x, y in [(14.5 * mm, 14.5 * mm), (w - 14.5 * mm, 14.5 * mm),
                 (14.5 * mm, h - 14.5 * mm), (w - 14.5 * mm, h - 14.5 * mm)]:
        canvas.setStrokeColor(C_GOLD)
        canvas.setLineWidth(1.5)
        canvas.circle(x, y, 3 * mm, stroke=1, fill=0)

    # Alt bilgi
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    footer_text = "Ebu Bekir er-Razi'nin Kazani — Erciyes Universitesi & Anadolu Tip Projesi"
    canvas.drawCentredString(w / 2.0, 7 * mm, footer_text)

    canvas.restoreState()


def build_pdf() -> Path:
    font_name = register_fonts()
    font_bold = f"{font_name}-Bold" if f"{font_name}-Bold" in pdfmetrics.getRegisteredFontNames() else font_name

    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "RaziTitle",
        fontName=font_bold,
        fontSize=16,
        leading=20,
        textColor=C_GOLD,
        alignment=1,  # Center
        spaceAfter=4,
    )

    style_subtitle = ParagraphStyle(
        "RaziSubtitle",
        fontName=font_name,
        fontSize=10,
        leading=13,
        textColor=C_MUTED,
        alignment=1,
        spaceAfter=10,
    )

    style_intro = ParagraphStyle(
        "RaziIntro",
        fontName=font_name,
        fontSize=9.5,
        leading=13.5,
        textColor=C_INK,
        spaceAfter=8,
        firstLineIndent=10,
    )

    style_item_title = ParagraphStyle(
        "RaziItemTitle",
        fontName=font_bold,
        fontSize=10,
        leading=13,
        textColor=C_GOLD,
        spaceBefore=6,
        spaceAfter=2,
    )

    style_item_body = ParagraphStyle(
        "RaziItemBody",
        fontName=font_name,
        fontSize=9,
        leading=12.5,
        textColor=C_INK,
        spaceAfter=5,
        leftIndent=8,
    )

    story = []

    # Başlık
    story.append(Paragraph("EBÛ BEKİR MUHAMMED BİN ZEKERİYYÂ ER-RÂZÎ", style_title))
    story.append(Paragraph("Felsefe Risaleleri · Tıbbiyeli Bir Dostuna Nasihatler<br/><i>(Risâletün fî Nüsehın li-Ba'dı Ashâbihi mine'l-Etıbbâ — M.S. 865–925)</i>", style_subtitle))
    story.append(HRFlowable(width="90%", thickness=1, color=C_GOLD, spaceBefore=2, spaceAfter=8))

    # Giriş Paragrafı
    intro_p = (
        "Büyük İslam hekimi, kimyager ve filozofu Ebû Bekir er-Râzî'nin hekimlik ahlakına ve "
        "tıbbi deontolojiye dair genç bir hekim dostuna kaleme aldığı bu tarihi mektup, tıp tarihinin "
        "en kıymetli etik metinlerinden biridir. Râzî, tıp sanatının yalnızca bedenleri değil, ruhları da "
        "şifalandıran ilahi ve insani bir emanet olduğunu şu nasihatlerle bildirir:"
    )
    story.append(Paragraph(intro_p, style_intro))

    nasihatler = [
        (
            "1. Hekimliğin Kutsiyeti ve Merhamet İlkesi",
            "Tabip, sanatını asla sadece dünyalık servet veya şan toplamak için icra etmemelidir. Hekimlik, "
            "insanın ıstırabını dindirme sanatıdır. Fakir ve biçare hastaları hiçbir menfaat gözetmeksizin, "
            "en varlıklı beylere gösterdiği özen ve muhabbetle tedavi etmelidir."
        ),
        (
            "2. Hastanın Sırrı Hekimin Namusudur",
            "Bir hekim, hastasının bedeninde, hanesinde veya ruhunda şahit olduğu en mahrem hâlleri "
            "asla başkalarına ifşa etmemelidir. Hastanın sırrı emanettir; mezara kadar tabibin göğsünde mahfuz kalmalıdır."
        ),
        (
            "3. Umut ve Moral Aşılamak (Ruh ile Bedenin Birliği)",
            "Hastanın yanında daima metanetli ol, ona iyileşeceği inancını ve ferahlık hissini aşıla. "
            "Zira insanın neşesi ve ümidi, bedenin hastalığa karşı gösterdiği doğal savunma gücünün en büyük dayanağıdır."
        ),
        (
            "4. Önce Gıda ve Rejim, Sonra Hafif Deva, En Son Ağır Terkip",
            "Bir illeti gıda ve perhizle gidermek mümkünse asla ilaca başvurma. İlaç gerekiyorsa evvela "
            "tek ve hafif bir şifa kaynağı (müfredat) kullan. Çaresiz kalmadıkça ağır ve karmaşık terkipleri (mürekkebat) bedene yükleme."
        ),
        (
            "5. Sürekli Okuma, Gözlem ve Sorgulama",
            "Tabip ömrünün son demine dek ilme susamış bir talebedir. Kadim üstatların (Hipokrat, Galen) eserlerini "
            "ezberlemek yetmez; onları hasta başında bizzat gözlemlemeli, deney yapmalı ve gerektiğinde sorgulamaktan geri durmamalıdır."
        ),
        (
            "6. Tevazu ve Ciddiyet",
            "Kibir, hekimin basiretini bağlar. Teşhisinde tereddüt ettiğin bir dertle karşılaştığında ehline "
            "danışmaktan haya etme. Şifa hekimin hünerinden değil, Hâlık'ın izniyledir; tabip yalnızca şefkatli bir vasıtadır."
        ),
        (
            "7. Zararlı Maddelerden ve Hileden Kaçınma",
            "Hekim hiçbir şart altında cana kastedecek zehirlere, şüpheli iksirlere ve sahtekarlığa alet olamaz. "
            "Kazandaki her karışım yalnızca hayat kurtarmak ve acıyı dindirmek gayesiyle hazırlanmalıdır."
        ),
        (
            "8. Hastanın Huysuzluğuna Sabır ve Nezaket",
            "Ağır ıstırap çeken hasta bazen sitemkar ve huysuz olabilir. Gerçek hekim, hastanın bu zayıflığını "
            "öfkeyle değil, ana-baba şefkatiyle karşılar; güler yüzünü ve tesellisini eksik etmez."
        ),
        (
            "9. Kendi Zihnini ve Bedenini Koru",
            "Yorgun, uykusuz ve zihni dağınık bir hekimin teşhisi yanıltıcı olur. Kendi sağlığına ve ahlakına "
            "ihtimam göster ki, başkalarına sıhhat ve güven dağıtabilesin."
        ),
        (
            "10. Hakikate ve Vicdana Sadakat",
            "Hekim vicdanını hiçbir dünyevi menfaate satamaz. Hakikatin ardından git ve keşfettiğin her şifalı sırrı "
            "insanlığın istifadesine sunmaktan çekinme."
        ),
    ]

    for title, text in nasihatler:
        story.append(Paragraph(title, style_item_title))
        story.append(Paragraph(text, style_item_body))

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="90%", thickness=0.8, color=C_GOLD, spaceBefore=2, spaceAfter=6))

    closing = (
        "<i>«İnsanlara merhamet etmeyen hekimin ilmi de şifası da bereketsizdir.»</i><br/>"
        "<b>— Ebû Bekir er-Râzî</b>"
    )
    story.append(Paragraph(closing, ParagraphStyle(
        "RaziClosing",
        fontName=font_name,
        fontSize=9.5,
        leading=13,
        textColor=C_GOLD,
        alignment=1,
    )))

    doc.build(story, onFirstPage=draw_background_and_border, onLaterPages=draw_background_and_border)
    print(f"PDF successfully generated: {OUTPUT_PDF} ({OUTPUT_PDF.stat().st_size} bytes)")
    return OUTPUT_PDF


if __name__ == "__main__":
    build_pdf()
