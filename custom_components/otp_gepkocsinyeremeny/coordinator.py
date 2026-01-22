"""Adatkezelő a koordinációhoz."""
import logging
import re
import json
import os
import aiohttp
import async_timeout
import asyncio
from datetime import timedelta, datetime

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components import persistent_notification
from .const import DOMAIN, CONF_NUMBERS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=12)
URL = "https://www.otpbank.hu/portal/hu/megtakaritas/forint-betetek/gepkocsinyeremeny"

class OTPCoordinator(DataUpdateCoordinator):
    """Adatok kezelése és frissítése."""

    def __init__(self, hass, numbers_str):
        """Inicializálás."""
        super().__init__(
            hass,
            _LOGGER,
            name="OTP Gépkocsinyeremény",
            update_interval=SCAN_INTERVAL,
        )
        self.hass = hass
        
        # Betétszámok tisztítása (formátumok: "14 8008533", "148008533", "60 0588196")
        self.my_numbers = []
        if numbers_str:
            # Először vesszővel elválasztjuk
            parts = numbers_str.split(",")
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                # Check for range
                # Try to split by " - " (space dash space) first, as it's the most likely separator for formatted numbers
                range_parts = []
                if " - " in part:
                     range_parts = part.split(" - ")
                elif "-" in part:
                    # If only single dash, it's definitely a range separator or a single number with dash
                    # If multiple dashes (e.g. 14-123-14-129 without spaces), it's ambiguous, but let's try splitting by middle if even?
                    # Safer: if count is odd, middle one is separator? No.
                    # Fallback: check split count
                    splits = part.split("-")
                    if len(splits) == 2:
                        range_parts = splits
                    # If > 2, lets try to see if it's 2 numbers with dashes?
                    # e.g. "14-123-14-129" -> 4 parts.
                    # We can't safely guess without spaces. 
                    # But the "28 vs 20" issue suggests users might use dashes. All we can do is try " - " first.

                if len(range_parts) == 2:
                    # Clean both parts
                    start_str = re.sub(r"[^0-9]", "", range_parts[0])
                    end_str = re.sub(r"[^0-9]", "", range_parts[1])
                    
                    # Range validation:
                    # 1. Both must be at least 8 digits (valid account numbers)
                    # 2. Lengths should match (to prevent 14-12345678 where first part is prefix)
                    if (len(start_str) >= 8 and len(end_str) >= 8 and 
                        len(start_str) == len(end_str)):
                        try:
                            start_num = int(start_str)
                            end_num = int(end_str)
                            # Max 1000 range limit
                            if end_num > start_num and (end_num - start_num) < 1000: 
                                for i in range(start_num, end_num + 1):
                                    self.my_numbers.append(str(i))
                                continue # Range processed
                            else:
                                _LOGGER.warning(f"Érvénytelen tartomány (túl nagy vagy fordított): {part} (Diff: {end_num - start_num})")
                        except ValueError:
                            _LOGGER.warning(f"Nem szám formátumú tartomány: {part}")
                    else:
                        if "-" in part and len(start_str) > 5 and len(end_str) > 5:
                            _LOGGER.warning(f"Tartomány eldobva (hossz eltérés vagy túl rövid): {part} ({len(start_str)} vs {len(end_str)} számjegy)") 

                # Normal processing (single number or failed range)
                # Összerakjuk a szóközöket (pl. "14 8008533" -> "148008533")
                clean_num = re.sub(r"\s+", "", part)
                # Csak számjegyeket tartunk meg
                clean_num = re.sub(r"[^0-9]", "", clean_num)
                if len(clean_num) >= 8:  # Minimum 8 számjegy kell
                    self.my_numbers.append(clean_num)
        
        _LOGGER.debug(f"Figyelt betétek: {self.my_numbers}")

        self._state_file = hass.config.path("otp_gepkocsi_state.json")
        self._history_file = hass.config.path("otp_nyeremeny_history.json")
        self._all_winners_file = hass.config.path("otp_all_winners.json")
        
        self.data = {
            "nyeremenyek": 0,
            "nyertes_reszletek": [],
            "utolso_sorsolas": "Ismeretlen",
            "kovetkezo_sorsolas": "Ismeretlen",
            "nyeremeny_tortenelem": [],
            "figyelt_db": len(self.my_numbers)
        }
        
        self._history = []
        self._checked_pdfs = []
        self._all_winners = {}

    async def _extract_text_from_pdf(self, session, url):
        """Letölti és kinyeri a szöveget egy PDF-ből pypdf segítségével."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status != 200:
                    _LOGGER.debug(f"PDF nem elérhető ({response.status}): {url}")
                    return None
                pdf_bytes = await response.read()
            
            text = ""
            try:
                def parse_pdf():
                    import io
                    from pypdf import PdfReader
                    f = io.BytesIO(pdf_bytes)
                    reader = PdfReader(f)
                    extracted = ""
                    for page in reader.pages:
                        extracted += page.extract_text() + "\n"
                    return extracted

                text = await self.hass.async_add_executor_job(parse_pdf)
                
            except ImportError:
                _LOGGER.error("A pypdf könyvtár nem található!")
                text = pdf_bytes.decode('latin-1', errors='ignore')
            except Exception as e:
                _LOGGER.debug(f"PDF feldolgozási hiba (pypdf): {e}")
                text = pdf_bytes.decode('latin-1', errors='ignore')
            
            return text
        except asyncio.TimeoutError:
            _LOGGER.debug(f"PDF letöltési timeout: {url}")
            return None
        except Exception as e:
            _LOGGER.debug(f"PDF letöltési hiba ({url}): {e}")
            return None

    def _extract_pdf_urls_from_html(self, html_content):
        """Kinyeri a PDF URL-eket az OTP oldalból."""
        pattern = r'(?:https://www\.otpbank\.hu)?/static/portal/sw/file/GK_\d{8}(?:_extra)?\.pdf'
        urls = re.findall(pattern, html_content)
        
        seen = set()
        unique_urls = []
        for url in urls:
            if url.startswith("/"):
                url = f"https://www.otpbank.hu{url}"
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        # Fallback: generate URLs for historical months (2 years = 24 months)
        from datetime import datetime, timedelta
        base_url = "https://www.otpbank.hu/static/portal/sw/file/GK_{}.pdf"
        today = datetime.now()
        for months_ago in range(24):  # Check last 24 months (2 years)
            check_date = today - timedelta(days=months_ago * 30)
            # Try 15th of month
            date_str = check_date.strftime("%Y%m") + "15"
            fallback_url = base_url.format(date_str)
            if fallback_url not in seen:
                seen.add(fallback_url)
                unique_urls.append(fallback_url)
            # Try 17th of month (sometimes used)
            date_str17 = check_date.strftime("%Y%m") + "17"
            fallback_url17 = base_url.format(date_str17)
            if fallback_url17 not in seen:
                seen.add(fallback_url17)
                unique_urls.append(fallback_url17)
        
        return unique_urls

    def _parse_date_from_pdf_url(self, url):
        """Kinyeri a dátumot a PDF URL-ből."""
        match = re.search(r'GK_(\d{4})(\d{2})(\d{2})', url)
        if match:
            return f"{match.group(1)}. {self._get_month_name(match.group(2))} {match.group(3)}."
        return "Ismeretlen dátum"
    
    def _get_month_name(self, month_str):
        months = ["", "január", "február", "március", "április", "május", "június", 
                 "július", "augusztus", "szeptember", "október", "november", "december"]
        try:
            m = int(month_str)
            if 1 <= m <= 12:
                return months[m]
        except:
            pass
        return month_str

    async def _async_load_files(self):
        """Fájlok betöltése."""
        def load():
            history = []
            checked = []
            all_winners = {}
            
            if os.path.exists(self._history_file):
                try: 
                    with open(self._history_file, 'r') as f: history = json.load(f)
                except: pass
            
            if os.path.exists(self._state_file):
                try:
                    with open(self._state_file, 'r') as f: 
                        state = json.load(f)
                        checked = state.get("checked_pdfs", [])
                except: pass

            if os.path.exists(self._all_winners_file):
                try:
                    with open(self._all_winners_file, 'r') as f:
                        all_winners = json.load(f)
                except: pass
                
            return history, checked, all_winners

        _LOGGER.info("Adatok betöltése fájlokból...")
        self._history, self._checked_pdfs, self._all_winners = await self.hass.async_add_executor_job(load)
        _LOGGER.info(f"Adatok betöltve: {len(self._history)} korábbi találat, {len(self._all_winners)} sorsolás a gyorsítótárban.")

    async def _async_save_files(self):
        """Fájlok mentése."""
        def save():
            with open(self._history_file, 'w') as f: json.dump(self._history, f, indent=2)
            with open(self._state_file, 'w') as f: json.dump({"checked_pdfs": self._checked_pdfs}, f)
            with open(self._all_winners_file, 'w') as f: json.dump(self._all_winners, f, indent=2)

        _LOGGER.info("Adatok mentése fájlba...")
        await self.hass.async_add_executor_job(save)
        _LOGGER.info("Adatok sikeresen elmentve.")

    async def _scan_historical_pdfs(self, session, html_content):
        """Végignézi az összes elérhető PDF-et és elmenti a nyerteseket."""
        _LOGGER.info("Történelmi sorsolások vizsgálata...")
        pdf_urls = self._extract_pdf_urls_from_html(html_content)
        
        changes_made = False

        for url in pdf_urls:
            date_match = re.search(r'GK_(\d{8})', url)
            if not date_match: continue
            
            date_key = date_match.group(1)
            if "_extra" in url:
                date_key += "_extra"
            
            # Ha már megvan és van benne adat, kihagyjuk
            if date_key in self._all_winners and self._all_winners[date_key].get("numbers"):
                continue
                
            _LOGGER.debug(f"Feldolgozás: {url}")
            text = await self._extract_text_from_pdf(session, url)
            
            if text:
                all_raw_winners = []
                lines = text.split('\n')
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    # Keresés: szám (5 vagy 6 kezdettel, 9 számjegy)
                    match = re.search(r'\b(\d{2})\s?(\d{7})\b', line)
                    if match:
                        full_num = f"{match.group(1)}{match.group(2)}"
                        car_part = line[match.end():].strip()
                        # Tisztítás
                        car_part = re.sub(r'^\s*[-–]\s*', '', car_part)
                        car_part = re.sub(r'\s+', ' ', car_part)
                        
                        entry = {"szam": full_num}
                        if car_part and len(car_part) > 3:
                            entry["auto"] = car_part
                        all_raw_winners.append(entry)
                
                date_text = self._parse_date_from_pdf_url(url)
                
                self._all_winners[date_key] = {
                    "text": date_text,
                    "url": url,
                    "scan_date": datetime.now().isoformat(),
                    "numbers": all_raw_winners
                }
                changes_made = True
                _LOGGER.info(f"Sorsolás ({date_text}) feldolgozva: {len(all_raw_winners)} nyertes.")
        
        if changes_made:
            await self._async_save_files()
            # Ha változott az adatbázis, ellenőrizni kell a saját számokat
            self._check_numbers_against_cache()

    def _check_numbers_against_cache(self):
        """Összeveti a saját számokat a teljes adatbázissal."""
        new_win = False
        for date_key, data in self._all_winners.items():
            for winner in data.get("numbers", []):
                if winner["szam"] in self.my_numbers:
                    # Találat - ellenőrizzük, hogy már nincs-e benne (szám + dátum alapján, mert lehet többször nyerni)
                    exists = any(h["szam"] == winner["szam"] and h["datum"] == data["text"] for h in self._history)
                    if not exists:
                        _LOGGER.warning(f"NYEREMÉNY TALÁLAT! {winner['szam']} - {data['text']}")
                        self._history.append({
                            "datum": data["text"],
                            "szam": winner["szam"],
                            "auto": winner.get("auto", "Ismeretlen típus"),
                            "forras": "Előzmények"
                        })
                        new_win = True
                        
                        # Értesítés küldése
                        persistent_notification.create(
                            self.hass, 
                            f"Gratulálunk! A {winner['szam']} betétkönyv nyert!\nNyeremény: {winner.get('auto', 'Autó')}\nSorsolás: {data['text']}",
                            title="🚗 OTP Gépkocsinyeremény",
                            notification_id=f"otp_win_{winner['szam']}"
                        )

        if new_win:
             self.hass.async_create_task(self._async_save_files())
    
    async def _async_update_data(self):
        """Adatok frissítése."""
        _LOGGER.info("OTP Gépkocsinyeremény adatfrissítés indítása...")
        
        if not self._all_winners:
            await self._async_load_files()
        
        # Először nézzük meg a cache-ből (hátha új számot adott hozzá a user)
        # Törölt számok eltávolítása az előzményekből
        original_count = len(self._history)
        self._history = [h for h in self._history if h.get("szam") in self.my_numbers]
        
        if len(self._history) != original_count:
             _LOGGER.info(f"Eltávolítva {original_count - len(self._history)} régi nyeremény a törölt számok miatt.")
             await self._async_save_files()

        self._check_numbers_against_cache()
        
        try:
            async with async_timeout.timeout(180):
                async with aiohttp.ClientSession() as session:
                    async with session.get(URL) as response:
                        html_content = await response.text()
                    
                    # Aktuális dátumok keresése az oldalon
                    next_draw = "Ismeretlen"
                    last_draw = "Ismeretlen"
                    
                    nd_match = re.search(r'Következő sorsolás:.*?(\d{4}\.\s*\w+\s*\d+\.)', html_content)
                    if nd_match: next_draw = nd_match.group(1)
                    
                    ld_match = re.search(r'Legutóbbi sorsolás:.*?(\d{4}\.\s*\w+\s*\d+\.)', html_content)
                    if not ld_match: # Try new format
                        ld_match = re.search(r'sorsolás\s*-\s*(\d{4}\.\s*\w+\s*\d+\.)', html_content)
                    
                    if ld_match: last_draw = ld_match.group(1)

                    if last_draw == "Ismeretlen":
                         # Fallback: Scrape failed, calculate theoretical date
                         # Logic: If today < 15th, last draw was previous month 15th
                         # If today >= 15th, last draw was this month 15th
                         now = datetime.now()
                         if now.day < 15:
                             # Previous month
                             first_of_month = now.replace(day=1)
                             last_month = first_of_month - timedelta(days=1)
                             target_date = last_month.replace(day=15)
                         else:
                             # This month
                             target_date = now.replace(day=15)
                         
                         months_hu = ["", "január", "február", "március", "április", "május", "június", 
                                     "július", "augusztus", "szeptember", "október", "november", "december"]
                         
                         last_draw = f"{target_date.year}. {months_hu[target_date.month]} {target_date.day}."
                         _LOGGER.info(f"Sorsolás dátuma nem található, becsült dátum használata: {last_draw}")

                    # Parse current drawing winners from HTML (latest drawing shows on page, not PDF)
                    html_winners = re.findall(r'\b(\d{2})\s?(\d{7})\b', html_content)
                    if html_winners and last_draw != "Ismeretlen":
                        # Extract date key from last_draw (e.g. "2026. január 15." -> "20260115")
                        date_match = re.search(r'(\d{4})\.\s*(\w+)\s*(\d+)', last_draw)
                        if date_match:
                            year = date_match.group(1)
                            month_name = date_match.group(2).lower()
                            day = date_match.group(3).zfill(2)
                            months = {"január": "01", "február": "02", "március": "03", "április": "04",
                                      "május": "05", "június": "06", "július": "07", "augusztus": "08",
                                      "szeptember": "09", "október": "10", "november": "11", "december": "12"}
                            month = months.get(month_name, "01")
                            draw_key = f"{year}{month}{day}"
                            
                            if draw_key not in self._all_winners or not self._all_winners[draw_key].get("numbers"):
                                current_winners = []
                                seen_nums = set()
                                for match in html_winners:
                                    num = f"{match[0]}{match[1]}"
                                    if num not in seen_nums and len(num) == 9:
                                        seen_nums.add(num)
                                        current_winners.append({"szam": num})
                                if current_winners:
                                    self._all_winners[draw_key] = {
                                        "text": last_draw,
                                        "url": "HTML",
                                        "scan_date": datetime.now().isoformat(),
                                        "numbers": current_winners
                                    }
                                    _LOGGER.info(f"HTML-ből kinyerve {len(current_winners)} nyertes szám ({last_draw})")
                                    await self._async_save_files()
                                    self._check_numbers_against_cache()

                    # Történelmi PDF-ek szkennelése
                    await self._scan_historical_pdfs(session, html_content)

            # Adatok összeállítása
            self._history.sort(key=lambda x: x.get("datum", ""), reverse=True)
            
            # Ha a legutóbbi sorsolás dátuma ismeretlen, de van adatunk, vegyük a legfrissebbet
            # Ha a legutóbbi sorsolás dátuma ismeretlen, de van adatunk, vegyük a legfrissebbet
            if last_draw == "Ismeretlen" and self._history:
                last_draw = self._history[0].get("datum", "Ismeretlen")

            # Csak a legutóbbi sorsolás nyereményei
            latest_winners = []
            if last_draw != "Ismeretlen":
                latest_winners = [w for w in self._history if w.get("datum") == last_draw]

            # Következő sorsolás (következő hónap 15-e)
            today = datetime.now()
            next_month = today.replace(day=15) + timedelta(days=32)
            next_draw_date = next_month.replace(day=15)
            # Ha ma 15-e előtt vagyunk, akkor ehavi 15-e (kivéve ha ma van a sorsolás)
            if today.day < 15:
                next_draw_date = today.replace(day=15)
            
            months_hu = ["", "január", "február", "március", "április", "május", "június", 
                        "július", "augusztus", "szeptember", "október", "november", "december"]
            next_draw = f"{next_draw_date.year}. {months_hu[next_draw_date.month]} {next_draw_date.day}."

            return {
                "nyeremenyek": len(self._history),
                "nyertes_reszletek": latest_winners,  # Csak a legutóbbiak
                "utolso_sorsolas": last_draw,
                "kovetkezo_sorsolas": next_draw,
                "nyeremeny_tortenelem": self._history, # Teljes történelem
                "figyelt_db": len(self.my_numbers),
                "adatbazis_frissitve": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "frissites_allapota": "Sikeres"
            }
            
            _LOGGER.info("OTP adatfrissítés sikeresen befejeződött.")
            return self.data

        except Exception as err:
            _LOGGER.error(f"Hiba az OTP adatok lekérésekor: {err}")
            return {
                "nyeremenyek": len(self._history),
                "nyertes_reszletek": self._history,
                "utolso_sorsolas": "Hiba a lekérdezésben",
                "kovetkezo_sorsolas": "Ismeretlen",
                "nyeremeny_tortenelem": self._history,
                "figyelt_db": len(self.my_numbers),
                "adatbazis_frissitve": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "frissites_allapota": f"Hiba: {str(err)}"
            }
