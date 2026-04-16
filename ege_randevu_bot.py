#!/usr/bin/env python3
"""
Ege Üniversitesi Hastanesi - Göz Hastalıkları Randevu Kontrol Botu v3
"""

import requests
import re
import time
import io
import sys
import os
import logging
import base64
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import unquote

try:
    from PIL import Image, ImageFilter, ImageOps
    import pytesseract
    OCR_AVAILABLE = True
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except ImportError:
    OCR_AVAILABLE = False
    print("⚠ pytesseract/Pillow yüklü değil. Captcha manuel girilecek.")

try:
    from plyer import notification
    NOTIFY_AVAILABLE = True
except ImportError:
    NOTIFY_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("randevu_bot")

# ══════════════════════════════════════════════════════════════════
# KİŞİSEL BİLGİLER — BUNLARI DOLDUR
# ══════════════════════════════════════════════════════════════════
TC_KIMLIK = "xx"
DOGUM_TARIHI = "xx-xx-xxxx"     # GG-AA-YYYY
TELEFON = "(5xx) xxx-xx-xx"

CHECK_INTERVAL = 60             # saniye
SKIP_POLYCLINICS = ["Katarakt"] # Bu kelimeyi içeren poliklinikler atlanır

# Telegram bildirimi — BotFather'dan aldığın değerleri buraya yaz
TELEGRAM_TOKEN = "87260xxxxx:AAFLhn7KNxxxxx"             # Örn: "7123456789:AAH..."
TELEGRAM_CHAT_ID = "15xxxxxxx"           # Örn: "123456789"
# ══════════════════════════════════════════════════════════════════

BASE_URL = "https://hastane.ege.edu.tr"
RANDEVU_URL = f"{BASE_URL}/randevu/"
RANDEVU_DURUMU_URL = f"{BASE_URL}/Randevu/RandevuDurumu.aspx"
RANDEVU_TAKVIM_URL = f"{BASE_URL}/Randevu/RandevuTakvim.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/147.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Origin": BASE_URL,
    "X-MicrosoftAjax": "Delta=true",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


class EgeRandevuBot:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.viewstate = ""
        self.viewstate_generator = ""
        self.logged_in = False

    def _extract_viewstate(self, text: str):
        soup = BeautifulSoup(text, "html.parser")
        vs = soup.find("input", {"name": "__VIEWSTATE"})
        if vs:
            self.viewstate = vs.get("value", "")
        vsg = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
        if vsg:
            self.viewstate_generator = vsg.get("value", "")
        # Async response format
        m = re.search(r"\|hiddenField\|__VIEWSTATE\|(.+?)\|", text)
        if m:
            self.viewstate = m.group(1)
        m2 = re.search(r"\|hiddenField\|__VIEWSTATEGENERATOR\|(.+?)\|", text)
        if m2:
            self.viewstate_generator = m2.group(1)

    def _extract_captcha_base64(self, text: str) -> str | None:
        m = re.search(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', text)
        return m.group(1) if m else None

    def _solve_captcha(self, base64_data: str) -> str:
        img_bytes = base64.b64decode(base64_data)
        img = Image.open(io.BytesIO(img_bytes))

        if OCR_AVAILABLE:
            img_gray = img.convert("L")
            img_binary = img_gray.point(lambda x: 0 if x < 140 else 255)
            img_clean = img_binary.filter(ImageFilter.MedianFilter(size=3))
            w, h = img_clean.size
            img_large = img_clean.resize((w * 3, h * 3), Image.LANCZOS)
            img_padded = ImageOps.expand(img_large, border=20, fill=255)

            for psm in [7, 8, 13, 6]:
                result = pytesseract.image_to_string(
                    img_padded,
                    config=f"--psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                ).strip().replace(" ", "").upper()
                result = re.sub(r'[^A-Z0-9]', '', result)
                if len(result) == 6:
                    log.info(f"OCR çözüm (PSM {psm}): {result}")
                    return result
            log.warning("OCR başarısız, manuel giriş gerekli.")

        try:
            temp_path = os.path.join(os.environ.get("TEMP", "."), "captcha.png")
            img.save(temp_path)
            if sys.platform == "win32":
                os.startfile(temp_path)
            else:
                img.show()
        except Exception:
            img.save("captcha.png")
        return input("Captcha kodunu girin: ").strip().upper()

    def _notify(self, title: str, message: str):
        log.info(f"🔔 {title}")
        print(f"\n{'='*60}")
        print(f"🎉 {title}")
        print(message)
        print(f"{'='*60}\n")
        try:
            if sys.platform == "win32":
                import winsound
                for _ in range(5):
                    winsound.Beep(1000, 500)
                    time.sleep(0.2)
        except Exception:
            print("\a" * 5)
        if NOTIFY_AVAILABLE:
            try:
                notification.notify(title=title, message=message[:200], timeout=30)
            except Exception:
                pass

        # Telegram bildirimi
        self._send_telegram(f"*{title}*\n{message}")

    def _send_telegram(self, message: str):
        """Telegram'a bildirim gönder."""
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            resp = requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
            }, timeout=10)
            if resp.status_code == 200:
                log.info("📱 Telegram bildirimi gönderildi!")
            else:
                log.warning(f"Telegram hata: {resp.status_code} — {resp.text[:100]}")
        except Exception as e:
            log.warning(f"Telegram gönderilemedi: {e}")

    def _save_debug(self, filename: str, content: str):
        debug_dir = "debug_responses"
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)

    # ─── GİRİŞ ───────────────────────────────────────────────────

    def login(self) -> bool:
        log.info("Giriş sayfası yükleniyor...")
        resp = self.session.get(RANDEVU_URL)
        if resp.status_code != 200:
            log.error(f"HTTP {resp.status_code}")
            return False

        self._extract_viewstate(resp.text)
        captcha_b64 = self._extract_captcha_base64(resp.text)
        if not captcha_b64:
            log.error("Captcha bulunamadı!")
            return False

        captcha_code = self._solve_captcha(captcha_b64)
        log.info(f"Captcha: {captcha_code}")

        data = {
            "ctl00$ScriptManager1": "ctl00$MainContent$upGiris",
            "__EVENTTARGET": "ctl00$MainContent$btnGiris",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstate_generator,
            "ctl00$mkMesaj$hdnMasterMessageLogla": "",
            "ctl00$mkMesaj$hdnMasterMessageTur": "",
            "ctl00$MainContent$mkMesaj$hdnMasterMessageLogla": "",
            "ctl00$MainContent$mkMesaj$hdnMasterMessageTur": "",
            "ctl00$MainContent$tbTCKimlikNo": TC_KIMLIK,
            "ctl00$MainContent$tbDogumTarihi": DOGUM_TARIHI,
            "ctl00$MainContent$tbTelefonNumarası": TELEFON,
            "ctl00$MainContent$tbProtokolNo": "",
            "ctl00$MainContent$tbEpostaAdresi": "",
            "ctl00$MainContent$tbGuvenlikKodu": captcha_code,
            "ctl00$MainContent$cbAnladim": "on",
            "__ASYNCPOST": "true",
        }

        resp = self.session.post(RANDEVU_URL, data=data,
                                 headers={**HEADERS, "Referer": RANDEVU_URL})

        if "pageRedirect" in resp.text and "RandevuDurumu" in resp.text:
            log.info("✅ Giriş başarılı!")
            self.logged_in = True
            return True
        else:
            log.error("❌ Giriş başarısız. (Captcha yanlış olabilir)")
            return False

    # ─── GÖZ HASTALIKLARI BUTONLARINI BUL ─────────────────────────

    def _find_goz_buttons(self, html: str) -> list[dict]:
        """
        HTML yapısı (debug'dan doğrulanmış):
        
        <div id="direkt_270034_4">
          <span>Göz Hastalıkları Genel Polikliniği 1</span>
          <input name="ctl00$...$rpBranslar$ctl12$...$ctl00$btnTakvimGoster_DirektPoliklinik" value="Tarih Seç" />
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        buttons = []

        # "direkt_" ile başlayan div'leri bul — her biri bir poliklinik
        for div in soup.find_all("div", id=re.compile(r"^direkt_")):
            # Poliklinik adı
            name_span = div.find("span", style=re.compile(r"font-size:\s*11pt"))
            if not name_span:
                continue
            poly_name = name_span.get_text(strip=True)

            # Göz Hastalıkları mı?
            if "Göz Hastalıkları" not in poly_name:
                continue

            # Skip kontrolü
            if any(s.lower() in poly_name.lower() for s in SKIP_POLYCLINICS):
                log.info(f"  ⏭ Atlanıyor: {poly_name}")
                continue

            # Tarih Seç butonu
            btn = div.find("input", {"value": "Tarih Seç"})
            if not btn:
                continue

            event_target = btn.get("name", "")
            if event_target:
                buttons.append({
                    "poly_name": poly_name,
                    "event_target": event_target,
                })

        return buttons

    # ─── TAKVİM KONTROLÜ ─────────────────────────────────────────

    def _click_tarih_sec_and_get_calendar(self, event_target: str) -> str:
        """
        'Tarih Seç' butonuna tıkla.
        Yanıt bir redirect: pageRedirect||/Randevu/RandevuTakvim.aspx
        Bu redirect'i takip edip takvim sayfasını GET ile al.
        """
        # 1) RandevuDurumu sayfasını yükle (güncel ViewState)
        resp = self.session.get(RANDEVU_DURUMU_URL)
        self._extract_viewstate(resp.text)

        # 2) "Tarih Seç" butonuna POST (AJAX)
        data = {
            "ctl00$ScriptManager1": f"ctl00$MainContent$ctl00|{event_target}",
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstate_generator,
            "ctl00$mkMesaj$hdnMasterMessageLogla": "",
            "ctl00$mkMesaj$hdnMasterMessageTur": "",
            "txtSearch": "",
            "ctl00$MainContent$mkMesaj$hdnMasterMessageLogla": "",
            "ctl00$MainContent$mkMesaj$hdnMasterMessageTur": "",
            "__ASYNCPOST": "true",
            event_target: "Tarih Seç",
        }

        resp = self.session.post(
            RANDEVU_DURUMU_URL, data=data,
            headers={**HEADERS, "Referer": RANDEVU_DURUMU_URL},
        )

        # 3) Redirect kontrolü
        # Yanıt: 1|#||4|31|pageRedirect||%2fRandevu%2fRandevuTakvim.aspx|
        redirect_match = re.search(r"pageRedirect\|\|([^|]+)\|", resp.text)
        if redirect_match:
            redirect_path = unquote(redirect_match.group(1))
            redirect_url = f"{BASE_URL}{redirect_path}"
            log.info(f"  → Takvim sayfasına yönlendiriliyor: {redirect_url}")

            # 4) Takvim sayfasını GET ile al (normal HTML, AJAX değil)
            calendar_resp = self.session.get(
                redirect_url,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml",
                    "Referer": RANDEVU_DURUMU_URL,
                },
            )
            return calendar_resp.text
        else:
            log.warning("  ⚠ Redirect bulunamadı, yanıt direkt parse ediliyor.")
            return resp.text

    def _parse_calendar(self, html: str, poly_name: str) -> list[dict]:
        """
        Takvim sayfasını parse et.

        Her kartta HEM "Talep Et" butonu HEM "DOLU" yazısı var.
        Hangisinin görünür olduğu CSS display ile kontrol ediliyor:

        DOLU gün:
          <span style="...display:none">       ← Talep Et GİZLİ
              <input value="Talep Et" />
          </span>
          <h3 style="display:block">DOLU</h3>  ← DOLU GÖRÜNÜR

        MÜSAİT gün:
          <span style="...display:block">      ← Talep Et GÖRÜNÜR
              <input value="Talep Et" />
          </span>
          <h3 style="display:none">DOLU</h3>   ← DOLU GİZLİ
        """
        available = []
        soup = BeautifulSoup(html, "html.parser")

        # Tüm "Talep Et" butonlarını bul
        talep_buttons = soup.find_all("input", {"value": "Talep Et"})

        dolu_count = 0
        musait_count = 0

        for btn in talep_buttons:
            # Parent <span>'in style'ını kontrol et
            parent_span = btn.parent
            if not parent_span:
                continue

            parent_style = parent_span.get("style", "")

            # display:none varsa bu gün DOLU
            if "display:none" in parent_style.replace(" ", ""):
                dolu_count += 1
                continue

            # display:none YOKSA bu gün MÜSAİT!
            musait_count += 1

            # Kart bilgisini çıkar — gunItem div'ine git
            card = btn
            for _ in range(10):
                if card.parent is None:
                    break
                card = card.parent
                card_classes = card.get("class", [])
                if "gunItem" in card_classes:
                    break

            card_text = card.get_text(separator="\n", strip=True)

            # Gün adı
            gun_match = re.search(
                r'(pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar)',
                card_text, re.IGNORECASE
            )
            gun = gun_match.group(1).upper() if gun_match else "?"

            # Gün sayısı + ay
            tarih_match = re.search(
                r'(\d{1,2})\s*\n?\s*(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|'
                r'Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)',
                card_text, re.IGNORECASE
            )
            tarih = f"{tarih_match.group(1)} {tarih_match.group(2)}" if tarih_match else "?"

            available.append({
                "poliklinik": poly_name,
                "gun": gun,
                "tarih": tarih,
            })

        log.info(f"  📊 {poly_name}: {dolu_count} DOLU, {musait_count} MÜSAİT")
        return available

    # ─── ANA KONTROL ─────────────────────────────────────────────

    def check_goz_appointments(self) -> list[dict]:
        if not self.logged_in:
            return []

        # 1) Ana sayfayı yükle
        log.info("Bölümler sayfası yükleniyor...")
        resp = self.session.get(RANDEVU_DURUMU_URL)

        # Session kontrolü — login sayfasına dönmüş mü?
        if "btnGiris" in resp.text or "Güvenlik Kodu" in resp.text:
            log.warning("Session süresi dolmuş!")
            self.logged_in = False
            return []

        self._extract_viewstate(resp.text)
        self._save_debug("randevu_durumu.html", resp.text)

        # 2) Göz Hastalıkları butonlarını bul
        goz_buttons = self._find_goz_buttons(resp.text)

        if not goz_buttons:
            log.warning("⚠ Göz Hastalıkları butonları bulunamadı!")
            return []

        log.info(f"Göz Hastalıkları'nda {len(goz_buttons)} poliklinik bulundu:")
        for b in goz_buttons:
            log.info(f"  ✓ {b['poly_name']}")

        # 3) Her poliklinik için takvimi kontrol et
        all_available = []

        for btn_info in goz_buttons:
            poly_name = btn_info["poly_name"]
            event_target = btn_info["event_target"]

            log.info(f"\n📅 {poly_name}")

            try:
                # Tarih Seç → redirect → takvim sayfası
                calendar_html = self._click_tarih_sec_and_get_calendar(event_target)

                safe_name = re.sub(r'[^\w]', '_', poly_name[:40])
                self._save_debug(f"takvim_{safe_name}.html", calendar_html)

                # Takvimi parse et
                available = self._parse_calendar(calendar_html, poly_name)
                if available:
                    all_available.extend(available)

            except Exception as e:
                log.error(f"  Hata: {e}", exc_info=True)

            time.sleep(2)  # Rate limiting

        return all_available

    # ─── ANA DÖNGÜ ────────────────────────────────────────────────

    def run(self):
        print("=" * 60)
        print("  Ege Üniversitesi Hastanesi")
        print("  Göz Hastalıkları Randevu Kontrol Botu v3")
        print("=" * 60)

        if TC_KIMLIK.startswith("XXX"):
            log.error("Lütfen bilgilerinizi script içinde doldurun!")
            return

        consecutive_failures = 0

        while True:
            try:
                if not self.logged_in:
                    self.session = requests.Session()
                    self.session.headers.update(HEADERS)
                    success = self.login()
                    if not success:
                        consecutive_failures += 1
                        wait = min(30 * consecutive_failures, 300)
                        log.info(f"{wait} sn bekleniyor...")
                        time.sleep(wait)
                        continue
                    consecutive_failures = 0

                log.info("─" * 50)
                log.info(f"Kontrol zamanı: {datetime.now().strftime('%H:%M:%S')}")
                appointments = self.check_goz_appointments()

                if appointments:
                    lines = []
                    for apt in appointments:
                        lines.append(
                            f"  🟢 {apt['poliklinik']} — {apt.get('gun','?')} {apt.get('tarih','?')}"
                        )
                    message = "\n".join(lines)
                    self._notify("🏥 MÜSAİT RANDEVU BULUNDU!", message)
                    log.info(f"🌐 Hemen randevu al: {RANDEVU_DURUMU_URL}")
                    # Bulsa bile kontrol etmeye devam et (Telegram'dan göreceksin)
                else:
                    log.info("❌ Tüm günler dolu.")

                log.info(f"Sonraki kontrol {CHECK_INTERVAL} sn sonra...\n")
                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                log.info("\nBot durduruldu.")
                break
            except requests.exceptions.ConnectionError:
                log.warning("Bağlantı hatası! 30 sn bekleniyor...")
                self.logged_in = False
                time.sleep(30)
            except Exception as e:
                log.error(f"Hata: {e}", exc_info=True)
                self.logged_in = False
                time.sleep(30)


if __name__ == "__main__":
    bot = EgeRandevuBot()
    bot.run()