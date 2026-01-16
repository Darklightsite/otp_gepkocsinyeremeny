# OTP Gépkocsinyeremény Ellenőrző

Home Assistant integráció az OTP gépkocsinyeremény betétek automatikus ellenőrzéséhez.

![Képernyőkép](https://github.com/Darklightsite/otp_gepkocsinyeremeny/blob/main/screenshot.png?raw=true)

## Funkciók

- 🚗 **Automatikus ellenőrzés:** Naponta kétszer ellenőrzi az OTP hivatalos oldalát.
- 📜 **Előzmények:** Visszamenőleg 2 évre tárolja és ellenőrzi a nyereményeket.
- 🚦 **Értesítések:** Azonnal látod a Dashboard-on, ha nyertél.
- 🎨 **Szép kártya:** Prémium "Mushroom" stílusú kártya design.

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
3. Írd be a figyelt betétkönyv számokat (vesszővel elválasztva vagy szóközzel, pl. `50 1234567, 60 9876543`).

## Megjelenítés (Lovelace)

Az integrációhoz tartozik egy előre formázott kártya minta. A `card.minta` fájlban találod a YAML kódot.

Szükséges HACS kiegészítők a szép megjelenéshez:
- [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)
