import logging
import aiohttp
import async_timeout
import re
import json
import os
import asyncio
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import DOMAIN, CONF_NUMBERS

_LOGGER = logging.getLogger(__name__)
# Javított URL - a helyes cím
URL = "https://www.otpbank.hu/portal/hu/megtakaritas/forint-betetek/gepkocsinyeremeny"

# Magyar fix ünnepnapok (hónap, nap)
HOLIDAYS = [
    (1, 1),   # Újév
    (3, 15),  # Nemzeti ünnep
    (5, 1),   # Munka ünnepe
    (8, 20),  # Államalapítás
    (10, 23), # 56-os forradalom
    (11, 1),  # Mindenszentek
    (12, 25), # Karácsony
    (12, 26)  # Karácsony
]

# Magyar hónapnevek a dátum formázáshoz
MONTH_NAMES = {
    1: "január", 2: "február", 3: "március", 4: "április",
    5: "május", 6: "június", 7: "július", 8: "augusztus",
    9: "szeptember", 10: "október", 11: "november", 12: "december"
}

def get_next_workday(start_date):
    """Megkeresi a következő munkanapot, kikerülve a hétvégéket és ünnepeket."""
    current = start_date
    while True:
        # 5 = Szombat, 6 = Vasárnap
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        
        # Ünnepnap ellenőrzés
        if (current.month, current.day) in HOLIDAYS:
            current += timedelta(days=1)
            continue
            
        # Ha egyik sem, akkor ez munkanap
        return current

def calculate_next_draw():
    """Kiszámolja a következő havi sorsolás várható dátumát."""
    today = date.today()
    
    # 1. Megnézzük a jelenlegi hónap 15-ét
    candidate = date(today.year, today.month, 15)
    draw_this_month = get_next_workday(candidate)
    
    # 2. Ha a mai nap még előtte van (vagy aznap), akkor ez a következő
    if today <= draw_this_month:
        return draw_this_month
    else:
        # 3. Ha már elmúlt, akkor a következő hónap 15-ét nézzük
        if today.month == 12:
            next_month = date(today.year + 1, 1, 15)
        else:
            next_month = date(today.year, today.month + 1, 15)
        return get_next_workday(next_month)

def parse_numbers(raw_text):
    """Szöveg szétszedése és tartományok kibontása."""
    expanded_numbers = []
    parts = [p.strip() for p in raw_text.replace("\n", ",").split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            try:
                start_str, end_str = part.split("-")
                start = int(start_str.replace(" ", "").replace(".", ""))
                end = int(end_str.replace(" ", "").replace(".", ""))
                if start < end:
                    for i in range(start, end + 1):
                        expanded_numbers.append(str(i))
                else:
                    expanded_numbers.append(str(start)) 
            except ValueError:
                pass
        else:
            clean_num = part.replace(" ", "").replace(".", "")
            if clean_num:
                expanded_numbers.append(clean_num)
    return expanded_numbers

def find_number_with_car(num, content):
    """Megkeresi a számot a tartalomban és visszaadja az autó típusát is.
    
    Returns:
        tuple: (found: bool, car_type: str or None)
    """
    num_clean = num.replace(" ", "").replace(".", "")
    
    # Formázott verzió szóközökkel (pl. "50 0088599")
    formatted = ""
    if len(num_clean) >= 8:
        formatted = f"{num_clean[:2]} {num_clean[2:]}"
    
    # Keressük a számot és az utána következő autó típust
    # Formátum: "50 0088599 Toyota Aygo X 1,5 Hybrid Comfort e-CVT A2"
    patterns = [
        # Szóközökkel formázott
        rf'{re.escape(formatted)}\s+([A-Za-záéíóöőúüűÁÉÍÓÖŐÚÜŰ][^\n\r<>]{{5,50}}?)(?:\n|\r|<|$)',
        # Szóközök nélkül
        rf'{re.escape(num_clean)}\s+([A-Za-záéíóöőúüűÁÉÍÓÖŐÚÜŰ][^\n\r<>]{{5,50}}?)(?:\n|\r|<|$)',
    ]
    
    for pattern in patterns:
        if not pattern.startswith(rf'{re.escape("")}'):
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                car_type = match.group(1).strip()
                # Tisztítás - eltávolítjuk a felesleges karaktereket
                car_type = re.sub(r'\s+', ' ', car_type)
                return True, car_type
    
    # Egyszerű ellenőrzés autó típus nélkül
    if formatted and (formatted in content or num_clean in content.replace(" ", "")):
        return True, None
    elif num_clean in content.replace(" ", ""):
        return True, None
    
    return False, None

def check_number_in_content(num, content):
    """Ellenőrzi, hogy egy szám szerepel-e a tartalomban (visszafelé kompatibilitás)."""
    found, _ = find_number_with_car(num, content)
    return found

def extract_all_winners_from_text(text):
    """Kinyeri az összes nyertes számot és autót a szövegből.
    
    Returns:
        list: [{"szam": "500012345", "auto": "Toyota..."}]
    """
    results = []
    # Pattern: szám (ami 9 jegyű és 5/6-tal kezdődik) + opcionálisan autó név
    # Formátum 1: 50 0088599 Toyota Aygo...
    # Formátum 2: 500088599 Toyota Aygo...
    
    # Először szedjük szét sorokra
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Keresünk számot a sorban
        # \b(5|6)\d\s?\d{7}\b
        match = re.search(r'\b([56]\d)\s?(\d{7})\b', line)
        if match:
            full_num = f"{match.group(1)}{match.group(2)}" # 500088599
            
            # Autó keresése a szám után
            car_part = line[match.end():].strip()
            
            # Tisztítás
            car_part = re.sub(r'^\s*[-–]\s*', '', car_part) # Kötőjel eltávolítása az elejéről
            car_part = re.sub(r'\s+', ' ', car_part)
            
            entry = {"szam": full_num}
            if car_part and len(car_part) > 3: # Ha maradt valami értelmes szöveg
                entry["auto"] = car_part
            
            results.append(entry)
            
    return results

async def async_setup_entry(hass, entry, async_add_entities):
    raw_input = entry.data.get(CONF_NUMBERS, "")
    my_numbers = parse_numbers(raw_input)
    coordinator = OtpCoordinator(hass, my_numbers)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([OtpSensor(coordinator)], True)

class OtpCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, my_numbers):
        super().__init__(
            hass,
            _LOGGER,
            name="OTP Scraper",
            # Naponta kétszer frissít (12 óránként)
            update_interval=timedelta(hours=12),
        )
        self.my_numbers = my_numbers
        self.hass = hass
        # Fájl elérési utak
        self._history_file = hass.config.path("otp_nyeremeny_history.json") # Felhasználó saját találatai
        self._state_file = hass.config.path("otp_gepkocsi_state.json")       # Szkennelési állapot
        self._all_winners_file = hass.config.path("otp_all_winners.json")    # Összes valaha volt nyertes (globális cache)
        
        self._history = []
        self._state = {"history_scan_done": False, "checked_pdfs": []}
        self._all_winners = {} # {"YYYYMMDD": {"text": "...", "numbers": [...]}}
    
    async def _async_load_files(self):
        """Betölti a fájlokat async módon."""
        def load_sync():
            history = []
            state = {"history_scan_done": False, "checked_pdfs": []}
            all_winners = {}
            
            # 1. Saját történelem
            try:
                if os.path.exists(self._history_file):
                    with open(self._history_file, 'r', encoding='utf-8') as f:
                        history = json.load(f)
            except Exception as e:
                _LOGGER.warning(f"Nem sikerült betölteni a történelmet: {e}")
            
            # 2. Állapot
            try:
                if os.path.exists(self._state_file):
                    with open(self._state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)
            except Exception as e:
                _LOGGER.warning(f"Nem sikerült betölteni az állapotot: {e}")

            # 3. Globális nyereménylista
            try:
                if os.path.exists(self._all_winners_file):
                    with open(self._all_winners_file, 'r', encoding='utf-8') as f:
                        all_winners = json.load(f)
            except Exception as e:
                _LOGGER.warning(f"Nem sikerült betölteni a globális nyereménylistát: {e}")
            
            return history, state, all_winners
        
        self._history, self._state, self._all_winners = await self.hass.async_add_executor_job(load_sync)
    
    async def _async_save_state(self):
        """Elmenti az integráció állapotát async módon."""
        def save_sync():
            try:
                with open(self._state_file, 'w', encoding='utf-8') as f:
                    json.dump(self._state, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _LOGGER.error(f"Nem sikerült menteni az állapotot: {e}")
        
        await self.hass.async_add_executor_job(save_sync)
    
    async def _async_save_history(self):
        """Elmenti a nyeremény történelmet fájlba async módon."""
        def save_sync():
            try:
                with open(self._history_file, 'w', encoding='utf-8') as f:
                    json.dump(self._history, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _LOGGER.error(f"Nem sikerült menteni a történelmet: {e}")
        
        await self.hass.async_add_executor_job(save_sync)

    async def _async_save_all_winners(self):
        """Elmenti a globális nyereménylistát fájlba async módon."""
        def save_sync():
            try:
                with open(self._all_winners_file, 'w', encoding='utf-8') as f:
                    json.dump(self._all_winners, f, ensure_ascii=False, indent=2)
            except Exception as e:
                _LOGGER.error(f"Nem sikerült menteni a globális nyereménylistát: {e}")
        
        await self.hass.async_add_executor_job(save_sync)
    
    def _add_to_history(self, num, draw_info, car_type=None, source="current"):
        """Hozzáad egy nyereményt a történelemhez ha még nincs benne."""
        # Ellenőrizzük, hogy már szerepel-e
        for item in self._history:
            if item.get("szam") == num and item.get("datum") == draw_info:
                # Ha már van, de nincs autó típus és most van, frissítsük
                if car_type and not item.get("auto"):
                    item["auto"] = car_type
                    return True
                return False
        
        entry = {
            "datum": draw_info,
            "szam": num,
            "rogzitve": datetime.now().isoformat(),
            "forras": source
        }
        if car_type:
            entry["auto"] = car_type
        
        self._history.append(entry)
        car_info = f" - {car_type}" if car_type else ""
        _LOGGER.info(f"Új nyeremény rögzítve: {num} ({draw_info}){car_info}")
        return True
    
    def _extract_pdf_urls_from_html(self, html_content):
        """Kinyeri a PDF URL-eket az OTP oldalból."""
        pattern = r'https://www\.otpbank\.hu/static/portal/sw/file/GK_\d{8}(?:_extra)?\.pdf'
        urls = re.findall(pattern, html_content)
        
        # Deduplikálás megtartva a sorrendet
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        _LOGGER.debug(f"Talált PDF URL-ek: {len(unique_urls)} db")
        return unique_urls
    
    def _parse_date_from_pdf_url(self, url):
        """Kiolvassa a dátumot egy PDF URL-ből."""
        match = re.search(r'GK_(\d{4})(\d{2})(\d{2})(_extra)?\.pdf', url)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            is_extra = match.group(4) is not None
            return year, month, day, is_extra
        return None
    
    async def _extract_text_from_pdf(self, session, url):
        """Letölti és kinyeri a szöveget egy PDF-ből."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    _LOGGER.debug(f"PDF nem elérhető ({response.status}): {url}")
                    return None
                pdf_bytes = await response.read()
            
            # Egyszerű szöveg kinyerés a PDF bináris adatból
            text = ""
            try:
                content = pdf_bytes.decode('latin-1', errors='ignore')
                text = content # Visszaadjuk a nyers tartalmat további feldolgozásra
            except Exception as e:
                _LOGGER.debug(f"PDF feldolgozási hiba: {e}")
            
            return text
        except asyncio.TimeoutError:
            _LOGGER.debug(f"PDF letöltési timeout: {url}")
            return None
        except Exception as e:
            _LOGGER.debug(f"PDF letöltési hiba ({url}): {e}")
            return None
    
    async def _scan_historical_pdfs(self, session, html_content):
        """Átvizsgálja a történelmi PDF-eket az oldalról kinyert linkek alapján."""
        _LOGGER.info("Történelmi sorsolások vizsgálata elkezdődött...")
        found_any = False
        updates_made = False
        
        checked_pdfs = set(self._state.get("checked_pdfs", []))
        
        # PDF URL-ek kinyerése az oldalból
        pdf_urls = self._extract_pdf_urls_from_html(html_content)
        
        if not pdf_urls:
            _LOGGER.warning("Nem találtam PDF linkeket az oldalon!")
            return
        
        _LOGGER.info(f"Összesen {len(pdf_urls)} PDF link található az oldalon")
        
        pdf_count = 0
        for url in pdf_urls:
            # Dátum kinyerése az URL-ből key-nek
            date_info = self._parse_date_from_pdf_url(url)
            if not date_info:
                continue
            
            year, month, day, is_extra = date_info
            date_key = f"{year}{month:02d}{day:02d}{'_extra' if is_extra else ''}"

            # Ha már megvan a globális cache-ben, nem töltjük le újra, de ellenőrizzük a számainkat
            if date_key in self._all_winners:
                draw_data = self._all_winners[date_key]
                draw_info = draw_data["text"]
                for winner in draw_data["numbers"]:
                    if winner["szam"] in self.my_numbers:
                        if self._add_to_history(winner["szam"], draw_info, winner.get("auto"), source="history_cache"):
                            found_any = True
                continue

            # Ha még nincs meg, letöltjük
            if url in checked_pdfs and not self._state.get("force_rescan"):
                 # Ha már checkoltuk és nincs force rescan, de nincs a cache-ben (furcsa), akkor letöltjük
                 pass
            
            # PDF letöltése és ellenőrzése
            text = await self._extract_text_from_pdf(session, url)
            
            if text:
                pdf_count += 1
                extra_suffix = " (extra)" if is_extra else ""
                draw_info = f"{year}. {MONTH_NAMES[month]} {day}.{extra_suffix}"
                
                # Összes nyertes kinyerése
                all_raw_winners = extract_all_winners_from_text(text)
                
                # Mentés a globális cache-be
                self._all_winners[date_key] = {
                    "text": draw_info,
                    "url": url,
                    "scan_date": datetime.now().isoformat(),
                    "numbers": all_raw_winners
                }
                updates_made = True
                
                # Ellenőrizzük a saját számainkat
                for winner in all_raw_winners:
                    if winner["szam"] in self.my_numbers:
                        if self._add_to_history(winner["szam"], draw_info, winner.get("auto"), source="history_scan"):
                            found_any = True
                            _LOGGER.info(f"🎉 Nyertes szám találva: {winner['szam']} ({draw_info})")
                
                checked_pdfs.add(url)
            
            # Kis szünet a kérések között
            await asyncio.sleep(0.5)
        
        # Mentés
        self._state["history_scan_done"] = True
        self._state["checked_pdfs"] = list(checked_pdfs)
        if "force_rescan" in self._state:
            del self._state["force_rescan"]
            
        await self._async_save_state()
        
        if updates_made:
             await self._async_save_all_winners()

        if found_any:
            self._history.sort(key=lambda x: x.get("rogzitve", ""), reverse=True)
            await self._async_save_history()
        
        _LOGGER.info(f"Történelmi vizsgálat befejezve. {pdf_count} új PDF feldolgozva.")

    def _check_numbers_against_cache(self):
        """Ellenőrzi a felhasználó számait a globális cache-ben."""
        found_any = False
        for date_key, data in self._all_winners.items():
            draw_info = data["text"]
            for winner in data["numbers"]:
                if winner["szam"] in self.my_numbers:
                    if self._add_to_history(winner["szam"], draw_info, winner.get("auto"), source="cache_check"):
                        found_any = True
        return found_any

    async def _async_update_data(self):
        # Első futáskor betöltjük a fájlokat
        if not self._history and not self._all_winners:
            await self._async_load_files()
        
        # Mindig ellenőrizzük a cache-t, hátha új számot adott hozzá a user
        if self._check_numbers_against_cache():
             self._history.sort(key=lambda x: x.get("rogzitve", ""), reverse=True)
             await self._async_save_history()

        try:
            async with async_timeout.timeout(180):  # 3 perc timeout a PDF-ek miatt
                async with aiohttp.ClientSession() as session:
                    # Aktuális oldal letöltése
                    async with session.get(URL) as response:
                        raw_content = await response.text()
                    
                    # Történelmi vizsgálat (kiegészíti a hiányzókat)
                    await self._scan_historical_pdfs(session, raw_content)
            
            # 1. Utolsó sorsolás - keressük a "XXX. sorsolás" mintát
            match_sorsolas = re.search(r'(\d+)\.\s*sorsolás', raw_content)
            sorsolas_szam = match_sorsolas.group(1) if match_sorsolas else ""
            
            # Dátum keresése (pl. "2026. január 15.")
            match_date = re.search(r'(\d{4})\.\s*([a-zA-ZáéíóöőúüűÁÉÍÓÖŐÚÜŰ]+)\s*(\d+)\.?', raw_content)
            if match_date:
                last_draw_text = f"{match_date.group(1)}. {match_date.group(2)} {match_date.group(3)}."
                if sorsolas_szam:
                    last_draw_text = f"{sorsolas_szam}. sorsolás ({last_draw_text})"
            else:
                last_draw_text = f"{sorsolas_szam}. sorsolás" if sorsolas_szam else "Ismeretlen"
            
            # 2. Következő sorsolás számítása (15-e + munkanap logika)
            next_date_obj = calculate_next_draw()
            days_hu = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
            day_name = days_hu[next_date_obj.weekday()]
            next_draw_text = f"{next_date_obj.year}. {next_date_obj.month:02d}. {next_date_obj.day:02d}. ({day_name})"

            # 3. Nyereményellenőrzés az aktuális oldalon
            clean_content = raw_content.replace("&nbsp;", " ")
            
            found_winners = []  # Lista: [{"szam": "...", "auto": "..."}]
            for num in self.my_numbers:
                found, car_type = find_number_with_car(num, clean_content)
                if found:
                    winner_info = {"szam": num}
                    if car_type:
                        winner_info["auto"] = car_type
                    found_winners.append(winner_info)
            
            # 4. Történelem frissítése ha van nyertes az aktuális oldalon
            if found_winners:
                for winner in found_winners:
                    self._add_to_history(
                        winner["szam"], 
                        last_draw_text, 
                        car_type=winner.get("auto"),
                        source="current"
                    )
                self._history.sort(key=lambda x: x.get("rogzitve", ""), reverse=True)
                await self._async_save_history()
            
            # Egyszerű lista a számokról (visszafelé kompatibilitás)
            found_numbers = [w["szam"] for w in found_winners]
            
            return {
                "count": len(found_numbers),
                "winners": found_numbers,
                "winners_detail": found_winners,  # Részletes lista autókkal
                "checked_count": len(self.my_numbers),
                "last_draw": last_draw_text,
                "next_draw_est": next_draw_text,
                "history": self._history[:20],  # Max 20 legutóbbi nyeremény
                "history_total": len(self._history)
            }
        except Exception as err:
            _LOGGER.error(f"Hiba az OTP adatok lekérésekor: {err}")
            raise UpdateFailed(f"Hiba: {err}")

class OtpSensor(SensorEntity):
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = "otp_checker_sensor"
        self._attr_name = "OTP Nyeremények"
        self._attr_icon = "mdi:car-sports"

    @property
    def native_value(self):
        return self.coordinator.data["count"]

    @property
    def extra_state_attributes(self):
        return {
            "nyertes_betetek": self.coordinator.data["winners"],
            "nyertes_reszletek": self.coordinator.data.get("winners_detail", []),
            "figyelt_db": self.coordinator.data["checked_count"],
            "utolso_sorsolas": self.coordinator.data["last_draw"],
            "kovetkezo_sorsolas": self.coordinator.data["next_draw_est"],
            "nyeremeny_tortenelem": self.coordinator.data.get("history", []),
            "osszes_nyeremeny": self.coordinator.data.get("history_total", 0)
        }

    async def async_update(self):
        await self.coordinator.async_request_refresh()
