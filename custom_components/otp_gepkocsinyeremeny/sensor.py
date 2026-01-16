"""
OTP Bank Gépkocsinyeremény betét ellenőrző integráció.
"""
import logging
import re
import json
import os
import aiohttp
import async_timeout
import asyncio
from datetime import timedelta, date, datetime

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components import persistent_notification
from homeassistant.const import CONF_NAME
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_NUMBERS = "numbers"
URL = "https://www.otpbank.hu/portal/hu/megtakaritas/forint-betetek/gepkocsinyeremeny"

SCAN_INTERVAL = timedelta(hours=12)

PLATFORMS = ["sensor", "button"]

async def async_setup_entry(hass, entry, async_add_entities):
    """Integráció beállítása config entry-ből."""
    numbers = entry.data.get(CONF_NUMBERS, "")
    
    coordinator = OTPCoordinator(hass, numbers)
    await coordinator.async_config_entry_first_refresh()

    async_add_entities([
        OTPSensor(coordinator),
        OTPRefreshButton(coordinator)
    ])

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
        
        # Betétszámok tisztítása
        self.my_numbers = []
        if numbers_str:
            raw_nums = numbers_str.replace(",", " ").split()
            for num in raw_nums:
                clean_num = re.sub(r"[^0-9]", "", num)
                if len(clean_num) > 0:
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

        self._history, self._checked_pdfs, self._all_winners = await self.hass.async_add_executor_job(load)

    async def _async_save_files(self):
        """Fájlok mentése."""
        def save():
            with open(self._history_file, 'w') as f: json.dump(self._history, f, indent=2)
            with open(self._state_file, 'w') as f: json.dump({"checked_pdfs": self._checked_pdfs}, f)
            with open(self._all_winners_file, 'w') as f: json.dump(self._all_winners, f, indent=2)

        await self.hass.async_add_executor_job(save)

    async def _scan_historical_pdfs(self, session, html_content):
        """Végignézi az összes elérhető PDF-et és elmenti a nyerteseket."""
        _LOGGER.info("Történelmi sorsolások vizsgálata...")
        pdf_urls = self._extract_pdf_urls_from_html(html_content)
        
        changes_made = False

        for url in pdf_urls:
            date_match = re.search(r'GK_(\d{8})', url)
            if not date_match: continue
            
            date_key = date_match.group(1)
            
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
                    match = re.search(r'\b([56]\d)\s?(\d{7})\b', line)
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
                    # Találat!
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
        if not self._all_winners:
            await self._async_load_files()
        
        # Először nézzük meg a cache-ből (hátha új számot adott hozzá a user)
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
                    if ld_match: last_draw = ld_match.group(1)

                    # Történelmi PDF-ek szkennelése
                    await self._scan_historical_pdfs(session, html_content)

            # Adatok összeállítása
            current_winners = []
            # Csak a legfrissebb sorsolás nyerteseit emeljük ki az aktuális nézethez?
            # Vagy mutassuk az összeset a history-ból ami "új"? 
            # A kártya design szerint külön van history és aktuális.
            # Most az egyszerűség kedvéért az összes history elemet használjuk.
            
            # Rendezés dátum szerint csökkenő
            self._history.sort(key=lambda x: x.get("datum", ""), reverse=True)
            
            return {
                "nyeremenyek": len(self._history),
                "nyertes_reszletek": self._history, # Ez a "current_winners" helyett is mehet
                "utolso_sorsolas": last_draw,
                "kovetkezo_sorsolas": next_draw,
                "nyeremeny_tortenelem": self._history,
                "figyelt_db": len(self.my_numbers)
            }

        except Exception as err:
            _LOGGER.error(f"Hiba az OTP adatok lekérésekor: {err}")
            # Ha hiba van, akkor is visszaadjuk a cachelt adatokat
            return {
                "nyeremenyek": len(self._history),
                "nyertes_reszletek": self._history,
                "utolso_sorsolas": "Hiba a lekérdezésben",
                "kovetkezo_sorsolas": "Ismeretlen",
                "nyeremeny_tortenelem": self._history,
                "figyelt_db": len(self.my_numbers)
            }

class OTPSensor(SensorEntity):
    """Fő szenzor."""
    _attr_has_entity_name = True
    
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = "otp_nyeremenyek_sensor"
        self._attr_name = None # Az eszköz nevét használja
        self._attr_icon = "mdi:car-convertible"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, "otp_main")},
            name="OTP Betétek",
            manufacturer="OTP Bank",
        )

    @property
    def state(self):
        return self.coordinator.data["nyeremenyek"]

    @property
    def extra_state_attributes(self):
        return self.coordinator.data
    
    @property
    def available(self):
        return True

    async def async_update(self):
        await self.coordinator.async_request_refresh()

class OTPRefreshButton(ButtonEntity):
    """Gomb a manuális frissítéshez."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = "otp_refresh_button"
        self._attr_name = "Adatbázis Frissítése"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, "otp_main")},
            name="OTP Betétek",
        )

    async def async_press(self) -> None:
        """Gomb megnyomása."""
        await self.coordinator.async_request_refresh()
