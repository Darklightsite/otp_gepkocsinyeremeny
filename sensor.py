import logging
import aiohttp
import async_timeout
import re
import json
import os
import io
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import DOMAIN, CONF_NUMBERS

_LOGGER = logging.getLogger(__name__)
# Javított URL - a helyes cím
URL = "https://www.otpbank.hu/portal/hu/megtakaritas/forint-betetek/gepkocsinyeremeny"
# PDF URL minta: https://www.otpbank.hu/static/portal/sw/file/GK_YYYYMMDD.pdf
PDF_URL_TEMPLATE = "https://www.otpbank.hu/static/portal/sw/file/GK_{date}.pdf"

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

def generate_pdf_dates(months_back=24):
    """Generálja a lehetséges PDF dátumokat visszamenőleg."""
    dates = []
    today = date.today()
    
    for i in range(months_back):
        # Minden hónapban a 15-e körüli munkanap
        target_month = today - relativedelta(months=i)
        candidate = date(target_month.year, target_month.month, 15)
        draw_date = get_next_workday(candidate)
        
        # Ha ez a dátum még a jövőben van, kihagyjuk
        if draw_date > today:
            continue
            
        dates.append(draw_date)
        
        # Extra sorsolások (január és július közepén szokott lenni)
        if target_month.month in [1, 7]:
            dates.append((draw_date, True))  # extra flag
    
    return dates

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
        # Nyeremény történelem fájl
        self._history_file = hass.config.path("otp_nyeremeny_history.json")
        self._state_file = hass.config.path("otp_gepkocsi_state.json")
        self._history = self._load_history()
        self._state = self._load_state()
    
    def _load_history(self):
        """Betölti a nyeremény történelmet fájlból."""
        try:
            if os.path.exists(self._history_file):
                with open(self._history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            _LOGGER.warning(f"Nem sikerült betölteni a történelmet: {e}")
        return []
    
    def _load_state(self):
        """Betölti az integráció állapotát."""
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            _LOGGER.warning(f"Nem sikerült betölteni az állapotot: {e}")
        return {"history_scan_done": False, "checked_pdfs": []}
    
    def _save_state(self):
        """Elmenti az integráció állapotát."""
        try:
            with open(self._state_file, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _LOGGER.error(f"Nem sikerült menteni az állapotot: {e}")
    
    def _save_history(self):
        """Elmenti a nyeremény történelmet fájlba."""
        try:
            with open(self._history_file, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _LOGGER.error(f"Nem sikerült menteni a történelmet: {e}")
    
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
    
    async def _extract_text_from_pdf(self, session, url):
        """Letölti és kinyeri a szöveget egy PDF-ből."""
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                pdf_bytes = await response.read()
            
            # Egyszerű szöveg kinyerés a PDF bináris adatból
            # A PDF-ekben a számok általában olvasható formában vannak
            text = ""
            try:
                # Próbáljuk meg UTF-8-ként dekódolni a releváns részeket
                content = pdf_bytes.decode('latin-1', errors='ignore')
                # Keressük a számokat a PDF-ben
                # A nyertes számok formátuma: "50 0088599" vagy "500088599"
                numbers = re.findall(r'\b\d{2}\s?\d{7}\b', content)
                text = " ".join(numbers)
            except Exception as e:
                _LOGGER.debug(f"PDF feldolgozási hiba: {e}")
            
            return text
        except Exception as e:
            _LOGGER.debug(f"PDF letöltési hiba ({url}): {e}")
            return None
    
    async def _scan_historical_pdfs(self, session):
        """Átvizsgálja az elmúlt 2 év PDF-jeit nyereményekért."""
        if self._state.get("history_scan_done"):
            return
        
        _LOGGER.info("Történelmi sorsolások vizsgálata elkezdődött (2 év)...")
        found_any = False
        
        today = date.today()
        checked_pdfs = set(self._state.get("checked_pdfs", []))
        
        # 24 hónap visszamenőleg
        for months_ago in range(24):
            target_date = today - relativedelta(months=months_ago)
            
            # Próbáljuk a hónap közepét (15.) és néhány nappal utána
            for day_offset in [15, 16, 17, 18, 19]:
                try:
                    check_date = date(target_date.year, target_date.month, day_offset)
                except ValueError:
                    continue
                
                if check_date > today:
                    continue
                
                date_str = check_date.strftime("%Y%m%d")
                
                if date_str in checked_pdfs:
                    continue
                
                # Normál sorsolás
                pdf_url = PDF_URL_TEMPLATE.format(date=date_str)
                text = await self._extract_text_from_pdf(session, pdf_url)
                
                if text:
                    draw_info = f"{check_date.year}. {MONTH_NAMES[check_date.month]} {check_date.day}."
                    for num in self.my_numbers:
                        if check_number_in_content(num, text):
                            if self._add_to_history(num, draw_info, source="history_scan"):
                                found_any = True
                    checked_pdfs.add(date_str)
                    break  # Ha megtaláltuk a hónap PDF-jét, továbblépünk
                
                # Extra sorsolás (január, július)
                if target_date.month in [1, 7]:
                    extra_url = PDF_URL_TEMPLATE.format(date=f"{date_str}_extra")
                    extra_text = await self._extract_text_from_pdf(session, extra_url)
                    if extra_text:
                        draw_info = f"{check_date.year}. {MONTH_NAMES[check_date.month]} {check_date.day}. (extra)"
                        for num in self.my_numbers:
                            if check_number_in_content(num, extra_text):
                                if self._add_to_history(num, draw_info, source="history_scan"):
                                    found_any = True
        
        # Mentés
        self._state["history_scan_done"] = True
        self._state["checked_pdfs"] = list(checked_pdfs)
        self._save_state()
        
        if found_any:
            self._history.sort(key=lambda x: x.get("rogzitve", ""), reverse=True)
            self._save_history()
        
        _LOGGER.info(f"Történelmi vizsgálat befejezve. Összesen {len(self._history)} nyeremény találva.")

    async def _async_update_data(self):
        try:
            async with async_timeout.timeout(120):  # Hosszabb timeout a PDF-ek miatt
                async with aiohttp.ClientSession() as session:
                    # Először a történelmi vizsgálat (csak egyszer fut le)
                    await self._scan_historical_pdfs(session)
                    
                    # Aktuális oldal ellenőrzése
                    async with session.get(URL) as response:
                        raw_content = await response.text()
            
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
                self._save_history()
            
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
