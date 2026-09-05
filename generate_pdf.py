"""
Tabîb Ekmeleddin (Bey Hekim) Kimdir?
13. Yüzyıl Anadolu Selçuklu Başhekimi ve Mevlânâ'nın Tabibi hakkında
ReportLab ile altın tezhip çerçeveli, şık ve Türkçe karakter destekli PDF üretir.
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
OUTPUT_PDF = BASE_DIR / "assets" / "tabib_ekmeleddin_kimdir.pdf"

# Renk paleti (Selçuklu Turkuazı & Altın Tezhip Uyumu)
C_GOLD = HexColor("#c29227")
C_GOLD_LT = HexColor("#dfb558")
C_TURQUOISE = HexColor("#006d77")
C_INK = HexColor("#1c140d")
C_MUTED = HexColor("#5c4838")
C_BORDER = HexColor("#845b2f")


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
    """Her sayfaya Selçuklu ve Osmanlı tezhip çerçevesi ve kenar süsü çizer."""
    canvas.saveState()
    w, h = doc.pagesize

    # Hafif antik parşömen arka planı
    canvas.setFillColor(HexColor("#faf6ee"))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)

    # Dış kalın altın çerçeve
    canvas.setStrokeColor(C_GOLD)
    canvas.setLineWidth(2.2)
    canvas.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm)

    # İç ince süs çerçevesi
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.8)
    canvas.rect(14.5 * mm, 14.5 * mm, w - 29 * mm, h - 29 * mm)

    # Dört köşe tezhip süslemeleri (Selçuklu yıldızı ve çember motifi)
    corners = [
        (14.5 * mm, 14.5 * mm),
        (w - 14.5 * mm, 14.5 * mm),
        (14.5 * mm, h - 14.5 * mm),
        (w - 14.5 * mm, h - 14.5 * mm),
    ]
    for x, y in corners:
        canvas.setStrokeColor(C_GOLD)
        canvas.setLineWidth(1.6)
        canvas.circle(x, y, 3.5 * mm, stroke=1, fill=0)
        canvas.setStrokeColor(C_TURQUOISE)
        canvas.setLineWidth(0.8)
        canvas.circle(x, y, 1.8 * mm, stroke=1, fill=0)

    # Alt bilgi
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    footer_text = "Tabîb Ekmeleddin'in Kazanı — Erciyes Üniversitesi & Anadolu Tıp Tarihi Projesi"
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

    s_top_kicker = ParagraphStyle(
        "TopKicker",
        fontName=font_bold,
        fontSize=9,
        leading=12,
        textColor=C_TURQUOISE,
        alignment=1,
        spaceAfter=3 * mm,
    )

    s_title = ParagraphStyle(
        "MainTitle",
        fontName=font_bold,
        fontSize=18,
        leading=22,
        textColor=C_GOLD,
        alignment=1,
        spaceAfter=2 * mm,
    )

    s_subtitle = ParagraphStyle(
        "SubTitle",
        fontName=font_name,
        fontSize=11,
        leading=15,
        textColor=C_MUTED,
        alignment=1,
        spaceAfter=5 * mm,
    )

    s_section = ParagraphStyle(
        "SectionHeader",
        fontName=font_bold,
        fontSize=12,
        leading=16,
        textColor=C_TURQUOISE,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )

    s_body = ParagraphStyle(
        "Body",
        fontName=font_name,
        fontSize=9.5,
        leading=14.5,
        textColor=C_INK,
        spaceAfter=2.5 * mm,
        alignment=4,  # Justified
    )

    s_quote = ParagraphStyle(
        "Quote",
        fontName=font_name,
        fontSize=9.5,
        leading=14.5,
        textColor=HexColor("#4a3622"),
        leftIndent=8 * mm,
        rightIndent=8 * mm,
        spaceBefore=3 * mm,
        spaceAfter=3 * mm,
    )

    story = []

    # Üst Bilgi & Başlık
    story.append(Paragraph("ANADOLU SELÇUKLU TIP TARİHİ VE KÜLTÜR MİRASI", s_top_kicker))
    story.append(Paragraph("TABÎB EKMELEDDİN (BEY HEKİM) KİMDİR?", s_title))
    story.append(Paragraph("13. Yüzyıl Selçuklu Saray Başhekimi, Dârüşşifa Üstadı ve Hz. Mevlânâ'nın Tabibi", s_subtitle))
    story.append(HRFlowable(width="90%", thickness=1.2, color=C_GOLD, spaceBefore=1 * mm, spaceAfter=4 * mm))

    # Bölüm 1: Tarihî Şahsiyeti ve Menşei
    story.append(Paragraph("1. Tarihî Şahsiyeti ve Selçuklu Sarayındaki Yeri", s_section))
    story.append(Paragraph(
        "Asıl adı <b>Ekmeleddîn Tabîb el-Nahcivânî</b> olan ve Anadolu'da hürmetle <b>Bey Hekim</b> (Beyhekim) "
        "olarak anılan bu usta tabip, 13. yüzyılda Anadolu Selçuklu Devleti'nin payitahtı Konya'da yaşamış "
        "en muteber hekim ve ilim adamlarındandır. Selçuklu sultanları II. İzzeddin Keykavus ve "
        "IV. Rükneddin Kılıçarslan devirlerinde saray başhekimliği (Melikü'l-Hükemâ) makamında bulunmuş; "
        "yalnızca hükümdarların değil, ordu ve halkın sıhhatini de idare etmiştir.",
        s_body
    ))
    story.append(Paragraph(
        "Konya'daki Dârüşşifa'nın baştabipliğini yürütmüş, yüksek tıbbî ahlakı, derin teşhis mahareti ve cömertliği "
        "sebebiyle halk arasında 'hekimlerin efendisi' manasında 'Bey Hekim' lakabıyla şöhret bulmuştur. "
        "Konya'da günümüze kadar ulaşan tarihî <b>Beyhekim Mescidi</b> ve <b>Beyhekim Mahallesi</b>, "
        "onun Anadolu tıbbına ve şehir kültürüne bıraktığı ölümsüz mirasın nişaneleridir.",
        s_body
    ))

    # Bölüm 2: Hz. Mevlânâ ile Dostluğu
    story.append(Paragraph("2. Hz. Mevlânâ Celâleddîn-i Rûmî ile Sırdaşlığı ve Özel Tabipliği", s_section))
    story.append(Paragraph(
        "Tabîb Ekmeleddin, Hz. Mevlânâ'nın yalnızca bedenî hekimi değil, aynı zamanda onun yüksek maneviyat "
        "meclislerinde bulunan en yakın gönül dostlarındandır. Ahmed Eflâkî'nin <i>Menâkıbü'l-Ârifîn</i> adlı "
        "meşhur eserinde anlatıldığı üzere; Mevlânâ hastalandığında derhâl Tabîb Ekmeleddin davet edilir, "
        "hastalığın mizaç ve ruh üzerindeki tesirleri birlikte istişare edilirdi.",
        s_body
    ))
    story.append(Paragraph(
        "<i>«Mevlânâ son demlerinde yatağındayken Tabîb Ekmeleddin onun mübarek nabzını tutmuş, "
        "bedeninin ateş ve zaafını görünce gözyaşlarını tutamamıştı. Mevlânâ ise kadim hekime tebessümle: "
        "'Ekmeleddin! Bu aşk derdidir, ona şurup da çare etmez, merhem de... Bizi Dost'a vuslattan men etme' "
        "diyerek hekimine olan muhabbetini ve vuslat neşesini dile getirmiştir.»</i>",
        s_quote
    ))

    # Bölüm 3: Tıp İlmindeki Mahareti ve Teşhis Usulleri
    story.append(Paragraph("3. Teşhis İlmi, Bitkisel Simya ve Terkîb-i Edviye (İlaç Sanatı)", s_section))
    story.append(Paragraph(
        "Tabîb Ekmeleddin, İbn Sînâ (Avicenna) ve Râzî'nin tıp geleneklerini Selçuklu Anadolu'sunda geliştirerek "
        "uygulamış bir hekimdir. Başlıca maharet sahaları şunlardır:",
        s_body
    ))
    story.append(Paragraph(
        "<b>• İlm-i Nabz (Nabız ile Teşhis):</b> Parmak uçlarıyla hastanın nabız ritmini, atış derinliğini ve süratini "
        "okuyarak organlardaki humma, tıkanıklık ve iltihabı alet olmaksızın en ince ayrıntısıyla tespit edebilirdi.<br/>"
        "<b>• Terkîb-i Edviye ve Simya:</b> Şifalı bitkilerin, madensel tuzların ve doğal cevherlerin imbiklerde "
        "damıtılmasıyla özel terkipler, kuvvet macunları ve şuruplar hazırlamıştır. Anadolu'nun endemik şifa nebatlarını "
        "tedavide sistemleştirmiştir.<br/>"
        "<b>• Dârüşşifa Eğitimi:</b> Konya Dârüşşifası'nda hekim namzetlerine anatomi, mizaç dengesi ve hekimlik ahlakı dersleri "
        "vermiş, çıraklarını hasta başında bizzat yetiştirmiştir.",
        s_body
    ))

    # Bölüm 4: Hekimlik Ahlakı ve Çağlar Aşan Mirası
    story.append(Paragraph("4. Bey Hekim'in Tıp Ahlakı ve Şifa Felsefesi", s_section))
    story.append(Paragraph(
        "Tabîb Ekmeleddin'e göre hekimlik, bir kazanç kapısı değil; Cenâb-ı Hakk'ın 'Şâfî' ismine hürmeten "
        "insana merhametle hizmet etme sanatıdır. Zengin hastadan aldığı ücreti yoksul hastaların dermanı için "
        "vakfetmiş, dârüşşifasında garip ve kimsesizleri karşılıksız tedavi etmiştir.",
        s_body
    ))
    story.append(Paragraph(
        "<i>«Gerçek hekim, hastanın yalnızca nabzına değil, kalbine ve kederine de tabip olandır. "
        "Güleryüz, tatlı dil ve tevekkül bulunmayan reçetenin tesiri eksik kalır.»</i> anlayışıyla Anadolu tıbbının "
        "insan merkezli şifa geleneğine rehberlik etmiştir.",
        s_quote
    ))

    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_GOLD_LT, spaceBefore=2 * mm, spaceAfter=3 * mm))
    story.append(Paragraph(
        "<b>Erciyes Üniversitesi Proje Kurulu</b> · Bu vesika, kadim Anadolu tıp mirasının ve büyük Selçuklu hekimi "
        "Tabîb Ekmeleddin'in (Bey Hekim) aziz hatırasını genç nesillere aktarmak gayesiyle dijitalleştirilmiştir.",
        ParagraphStyle("Footnote", fontName=font_name, fontSize=8, leading=11, textColor=C_MUTED, alignment=1)
    ))

    doc.build(story, onFirstPage=draw_background_and_border, onLaterPages=draw_background_and_border)
    return OUTPUT_PDF


if __name__ == "__main__":
    pdf_path = build_pdf()
    print(f"Tabîb Ekmeleddin PDF başarıyla üretildi: {pdf_path} ({os.path.getsize(pdf_path)} bayt)")
