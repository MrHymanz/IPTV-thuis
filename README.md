# IPTV thuis

Een zelfstandige Linux-tv-app met grote zenderknoppen en een aparte webbeheerpagina. Ontworpen voor iemand die alleen wil kiezen, kijken en zappen, zonder Kodi-menu's.

**Status: eerste implementatie voor Debian/Ubuntu met X11.** De automatische tests omvatten: webbeheer, opslag met 50.000 zenders en echte mpv-videoweergave/zappen op een virtueel X11-scherm. De eigen IPTV-aanbieder, de USB-afstandsbediening en het automatisch aanmelden moeten op de mediacenter-pc nog worden gecontroleerd. LibreELEC wordt door deze zelfstandige versie niet ondersteund: het is [specifiek gebouwd voor Kodi](https://wiki.libreelec.tv/). Dit project vervangt geen besturingssysteem.

## Functies

- Maximaal tien grote favorieten per pagina; meer pagina's verschijnen automatisch.
- Streams spelen met mpv binnen hetzelfde venster.
- Pijltjes of CH+/CH− zappen direct door de favorieten tijdens de uitzending. Na de laatste volgt de eerste.
- Terug opent de zenderlijst. OK tijdens het kijken toont kort de zendernaam.
- Webbeheer op `http://IP-VAN-TV-PC:8080/admin`: Losse Xtream Codes-inloggegevens gebruiken, M3U uploaden of via een adres ophalen, zoeken, groepen filteren, favorieten toevoegen, verwijderen, hernoemen en ordenen, eigen stream toevoegen.
- Een opgeslagen M3U-adres wordt bij het starten en elke zes uur vernieuwd zolang de app draait. Een bestandsupload vervang je door opnieuw te uploaden.
- Eigen namen en volgorde blijven bewaard bij import. Verdwenen zenders blijven als niet beschikbaar zichtbaar en worden bij zappen overgeslagen.
- Standaard uitgebreide M3U-lijsten met `#EXTINF`, HTTP(S), RTSP, RTMP en UDP. Kodi-stijl HTTP-headers na `|` worden doorgegeven aan mpv. DRM, aanbieder-specifieke loginflows, catch-up en EPG zijn niet geïmplementeerd.

## Installatie op de tv-pc

### Lokaal testen met Docker

```sh
docker compose up -d --build --wait
docker compose exec app cat /data/first-login.txt
```

Open `http://IP-VAN-DOCKER-HOST:8090/admin` en log in als `admin` met het wachtwoord uit de tweede opdracht. Vul je serveradres, IPTV-gebruikersnaam en IPTV-wachtwoord in en klik op **Inloggen en zenders ophalen**, of importeer je M3U. Voeg favorieten toe en open `http://IP-VAN-DOCKER-HOST:8090/tv` om de zenderknoppen, pagina's en direct zappen te testen. Deze browser-preview gebruikt je echte favorieten en haalt wijzigingen elke vijf seconden op, maar speelt **geen live video** af. Echte mpv-weergave blijft onderdeel van de Linux-desktop-app.

De container draait zonder root, met een alleen-lezen app en een blijvend Docker-volume voor de database en beheerconfiguratie. Herstarten of opnieuw bouwen bewaart je gegevens. `docker compose down` stopt de app; voeg geen `--volumes` toe als je de gegevens wilt bewaren. Wijzig de poort desgewenst met `IPTV_PORT=8091 docker compose up -d`. Alleen op de Docker-host bereikbaar maken kan met `IPTV_BIND_ADDRESS=127.0.0.1 docker compose up -d`.

Een Docker-beheerwachtwoord wijzigen: `docker compose stop app`, daarna `docker compose run --rm --service-ports app python -m mijntv --admin-only --data-dir /data --reset-password`. Stop deze tijdelijke instantie na de wijziging met Ctrl+C en start de normale instantie weer met `docker compose up -d`.

### Zelfstandige tv-app

Gebruik Debian/Ubuntu met een lichte desktop, bijvoorbeeld XFCE, en een **X11/Xorg-sessie**. De speler wordt ingebed met een X11-venster-ID; native Wayland is niet het ondersteunde pad. Zie de [mpv-documentatie](https://mpv.io/manual/stable/#options-wid).

```sh
git clone http://192.168.1.40:3000/administrator/IPTV-thuis.git
cd IPTV-thuis
sh tools/install-linux.sh
~/.local/bin/iptv-thuis
```

Het script installeert `python3-tk`, `mpv` en lettertypen met sudo, kopieert de app naar de gebruikersmap en maakt een desktop-autostart-item. Voer het uit als de tv-gebruiker. Stel **automatisch aanmelden** voor die gebruiker in via de desktopinstellingen. Schakel daar ook schermvergrendeling en automatisch slapen uit. Daarna opent de app bij het aanmelden op volledig scherm het zenderoverzicht. De app verandert geen aanmeldinstellingen of schijven.

Handmatig starten zonder installatiescript:

```sh
sudo apt-get install python3 python3-tk mpv fonts-dejavu-core
python3 -m mijntv
```

Bij de eerste start toont het lege overzicht het beheeradres, gebruikersnaam `admin` en het gegenereerde wachtwoord. Open dit adres op een laptop of telefoon op hetzelfde netwerk. Importeer de M3U en kies ongeveer 30 favorieten. Binnen vijf seconden verschijnen ze op de televisie. Alleen de gekozen favorieten verschijnen op het tv-scherm.

## Afstandsbediening

| Knop/toets | Zenderoverzicht | Tijdens kijken |
| --- | --- | --- |
| Pijltjes | Zender kiezen | Volgende/vorige favoriet |
| OK / Enter / spatie | Zender starten | Zendernaam tonen |
| CH+ / PageDown | Volgende pagina | Volgende favoriet |
| CH− / PageUp | Vorige pagina | Vorige favoriet |
| Terug / Escape / Backspace | In overzicht blijven | Stoppen en zenderlijst openen |
| F11 | Volledig scherm wisselen | Volledig scherm wisselen |
| Ctrl+Q | App afsluiten | App afsluiten |

Een USB 2,4GHz-ontvanger die zich als toetsenbord presenteert kan deze toetsen rechtstreeks sturen. CH-knoppen kunnen andere codes sturen; controleer ze op de tv-pc met bijvoorbeeld `xev` en pas indien nodig `TV.key` in `mijntv/tv.py` aan. Volume loopt via het systeem/de televisie; er is geen eigen volume-menu.

## Beheer en gegevens

Er zijn geen externe Python-pakketten nodig. Standaard staat alle configuratie in `~/.local/share/iptv-thuis/`, buiten de repository:

- `mijntv.db`: zenders, streamadressen, favorieten en het opgeslagen M3U-adres.
- `admin.json`: gezouten PBKDF2-hash van het beheerwachtwoord.
- `first-login.txt`: eerste wachtwoord om na een onbeheerde herstart te kunnen inloggen. Bewaar het in je wachtwoordmanager en verwijder dit bestand daarna desgewenst. De app toont dit wachtwoord alleen zolang er geen favorieten zijn.

Nieuwe gegevensbestanden zijn alleen toegankelijk voor de eigen gebruiker. Provideradressen worden niet teruggestuurd in catalogus/favorieten-API's, niet gelogd en niet in mpv-procesargumenten gezet. Een databaseback-up bevat wel providergegevens. Het beheer gebruikt HTTP Basic-authenticatie en bescherming tegen aanvragen vanuit andere websites. De ingebouwde HTTP-server is bedoeld voor het eigen LAN; gebruik HTTPS via een reverse proxy als je versleuteld beheer nodig hebt.

Wachtwoord wijzigen (sluit de app eerst met Ctrl+Q):

```sh
~/.local/bin/iptv-thuis --reset-password
```

Alleen webbeheer draaien, bijvoorbeeld voor ontwikkeling zonder beeldscherm:

```sh
python3 -m mijntv --admin-only --host 127.0.0.1 --port 8080 --data-dir ./data
```

Dit wijzigt uitsluitend de gegevensmap van die instantie. De normale installatie draait beheer en televisie samen op de tv-pc met dezelfde database.

## IPTV met losse inloggegevens

Het webbeheer heeft drie velden voor **Serveradres**, **IPTV-gebruikersnaam** en **IPTV-wachtwoord**. Ze worden gebruikt om het standaard Xtream Codes-adres `/get.php` met `type=m3u_plus` en `output=ts` op te vragen. Je hoeft zelf geen M3U-URL te maken. Speciale tekens in de inloggegevens worden correct gecodeerd. Zie de [playlistdocumentatie](https://github.com/worldofiptvcom/xtream-codes-api/blob/master/docs/utilities/playlists.md).

De provider moet deze Xtream Codes-playlistmethode ondersteunen. De velden worden niet vanuit de opgeslagen configuratie teruggevuld en het wachtwoord wordt na versturen uit het formulier gewist. Het samengestelde adres wordt in de lokale database bewaard voor automatisch vernieuwen; een mislukte aanvraag laat de bestaande zenderlijst intact.

## Importgedrag

Zenderidentiteit wordt afgeleid van `tvg-id`, oorspronkelijke naam, groep en het volgnummer bij exacte duplicaten. Tijdelijke streamtokens tellen niet mee: een vernieuwde URL vervangt de bestaande favoriet. Als de aanbieder naam, groep of ID verandert, voeg je de nieuwe zender opnieuw toe en verwijder je de oude favoriet. Exact gelijk benoemde duplicaten worden op volgorde gekoppeld; onderscheidende namen/IDs zijn betrouwbaarder.

Playlists mogen maximaal **512 MB** zijn, zowel via een download als een bestandsupload. Downloads en uploads worden in blokken naar een tijdelijk bestand in de gegevensmap geschreven en vervolgens regel voor regel in SQLite verwerkt. De volledige playlist en zendercatalogus worden niet tegelijk in het geheugen geladen. Het tijdelijke bestand wordt ook bij fouten opgeruimd; zorg dat de gegevensmap voldoende vrije schijfruimte heeft voor de playlist en database.

Een ongeldige, onderbroken of mislukte import laat de oude lijst intact. De nieuwe catalogus wordt in één SQLite-transactie opgeslagen. Zoekresultaten worden per honderd opgehaald, zodat de beheerpagina niet 50.000 elementen tegelijk hoeft te tekenen. Handmatige streams blijven bij import bestaan.

## Ontwikkelen en testen

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q mijntv tools tests
sh -n tools/install-linux.sh
python3 tools/build.py
```

De optionele videoproef vereist X11 (of Xvfb), tkinter, mpv en ffmpeg; zonder die omgeving wordt alleen die proef overgeslagen. Een afbeelding van het geteste tv-scherm staat in `docs/tv-scherm.png`.

Het bouwscript maakt `dist/IPTV-thuis-0.1.0.zip` met bronbestanden, documentatie, tests en de klikbare mockup. `index.html` is die mockup, geen browser-speler van de echte M3U. Hij ondersteunt ook direct zappen op het voorbeeldscherm.

Controleer voor dagelijks gebruik op de tv-pc: koude boot, HDMI-geluid, vijf echte streams, herhaald zappen, Terug, alle afstandsbedieningsknoppen, netwerkverlies en herstel, en beheer vanaf een tweede apparaat.
