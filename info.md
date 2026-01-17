# OTP Gépkocsinyeremény Ellenőrző

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Darklightsite&repository=otp_gepkocsinyeremeny&category=Integration)

Home Assistant integráció az OTP gépkocsinyeremény betétek automatikus ellenőrzéséhez.

![Képernyőkép](images/cards_v1.2.png)

## Funkciók

- 🚗 **Automatikus ellenőrzés:** Naponta kétszer ellenőrzi az OTP hivatalos oldalát.
- 📜 **Előzmények:** Visszamenőleg 2 évre tárolja és ellenőrzi a nyereményeket.
- 📊 **Állapotkövetés:** Láblécben jelzi az utolsó frissítés idejét.
- 🚦 **Értesítések:** Azonnal látod a Dashboard-on, ha nyertél.
- 🎨 **Szép kártyák:** 3 különböző stílusú kártya (Comfort, Premium, Advanced).

## Telepítés

### HACS (Ajánlott)

1. Nyisd meg a HACS-ot a Home Assistantban.
2. Kattints a jobb felső sarokban a 3 pöttyre -> **Custom repositories**.
3. Add hozzá az URL-t: `https://github.com/Darklightsite/otp_gepkocsinyeremeny`
4. Típus: **Integration**.
5. Kattints a **Download** gombra.
6. Indítsd újra a Home Assistant-ot.

### Manuális telepítés

1. Töltsd le a repót.
2. Másold a `custom_components/otp_gepkocsinyeremeny` mappát a Home Assistant `custom_components` mappájába.
3. Indítsd újra a Home Assistant-ot.

## Beállítás

1. Menj a **Beállítások** -> **Eszközök és szolgáltatások** -> **Integráció hozzáadása** menübe.
2. Keresd meg: **OTP Gépkocsinyeremény**.
3. Írd be a figyelt betétkönyv számokat.
   - **Tipp:** Megadhatsz tartományokat is (pl. `12345678-12345688`) vagy formázott számokat (pl. `14-1234567`).

## Megjelenítés (Lovelace)

Az integrációhoz 3 különböző stílusú, előre elkészített kártya tartozik a `cards/` mappában:

1. **Advanced (`cards/advanced.yaml`):** (Bal oldali)
   - Klasszikus, sötét tónusú kártya
   - Részletes lista nézet
   - Frissítés gomb és állapotjelző

2. **Premium (`cards/compact.yaml`):** (Középső)
   - Extra látványos **Arany/Fekete** dizájn
   - **Animált** nyeremény jelzés (parti tülök + lüktető keret)
   - Arany gradiens fejléc

3. **Comfort (`cards/simple.yaml`):** (Jobb oldali)
   - Letisztult, "nyugodt" dizájn
   - Egységes zöld/szürke színvilág
   - Kompakt megjelenés

**Használat:**
1. Nyisd meg a kiválasztott `.yaml` fájlt.
2. Másold ki a teljes tartalmát.
3. A Home Assistant Dashboard-on adj hozzá egy **Manual** kártyát és illeszd be a kódot.

Szükséges HACS kiegészítők a szép megjelenéshez:
- [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)
- [stack-in-card](https://github.com/custom-cards/stack-in-card) (Új!)
