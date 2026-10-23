# Zelfstandige tv op LibreELEC

Een aparte Debian-container levert Xorg, Openbox, Tk en mpv. Openbox beheert alleen het app-venster; er verschijnt geen bureaubladmenu. LibreELEC en de bestaande WireGuard-configuratie blijven behouden. Dit is een aangepaste uitvoering, geen standaard LibreELEC-desktopinstallatie.

Deze uitvoering is bedoeld voor Generic x86_64. De container gebruikt het netwerk van de host (inclusief diens VPN-routering), de DRM-videokaart, virtuele terminal 7, ALSA en USB-invoer. Om die apparaten te gebruiken draait uitsluitend deze tv-container met `--privileged`; de bestaande webcontainer blijft ongewijzigd. Xorg luistert niet op TCP. Gebruik geen tweede grafische app tegelijk.

## Installatie

Installeer de Docker-add-on uit de LibreELEC-repository. Maak de bijbehorende `service.system.docker.service` beschikbaar in `/storage/.config/system.d/` en start die met systemd. Deze dienst moet onafhankelijk van de Kodi-app blijven werken.

Kopieer de bronmap naar `/storage/iptv-thuis/source` en bouw:

```sh
/storage/.kodi/addons/service.system.docker/bin/docker build \
  -t iptv-thuis-tv:local \
  -f /storage/iptv-thuis/source/deploy/libreelec/Dockerfile \
  /storage/iptv-thuis/source
```

Maak `/storage/iptv-thuis/data` aan (modus 700). Een bestaande `mijntv.db` moet met SQLite backup worden gekopieerd, geen losse kopie van een draaiende database. Kopieer ook `admin.json` om dezelfde beheerlogin te gebruiken. De data op de tv en een andere Docker-host worden daarna onafhankelijk beheerd.

Voer `create-container.sh` uit. Standaard kiest `IPTV_AUDIO_DEVICE=auto` de HDMI-uitgang waaraan ALSA de aangesloten tv koppelt. Op de geteste AOpen DE7200 met LG-tv is dat `alsa/hdmi:CARD=HDMI,DEV=0`. Stel `IPTV_AUDIO_DEVICE` expliciet in als een vaste aansluiting nodig is. Maak de container opnieuw om dat te wijzigen.

Test eerst tijdelijk met Kodi gestopt en houd SSH open om terug te kunnen:

```sh
systemctl stop kodi
/storage/.kodi/addons/service.system.docker/bin/docker start iptv-thuis-tv
```

Controleer de container-health, het beeld, geluid, zappen en de echte afstandsbediening. Webbeheer: `http://IP-VAN-TV:8080/admin`.

## Automatisch starten en herstel

Plaats `run-tv.sh` in `/storage/iptv-thuis/` en `iptv-thuis.service` in `/storage/.config/system.d/`. Plaats `kodi-iptv.conf` als `/storage/.config/system.d/kodi.service.d/iptv.conf`. De drop-in slaat Kodi alleen over zolang het bestand `/storage/iptv-thuis/enabled` bestaat.

```sh
systemctl daemon-reload
systemctl enable iptv-thuis.service
touch /storage/iptv-thuis/enabled
systemctl start iptv-thuis.service
```

De launcher geeft de container twee minuten om gezond te worden. Bij een mislukte start, een onverwachte beëindiging of een mislukte healthcheck verwijdert hij `enabled` en start Kodi. De healthcheck controleert Xorg, een zichtbaar tv-venster en de beheer-HTTP-server; inhoudelijke streamfouten en een vastgelopen Tk-interface worden niet automatisch vastgesteld. Handmatig terug naar Kodi:

```sh
rm -f /storage/iptv-thuis/enabled
systemctl stop iptv-thuis.service
/storage/.kodi/addons/service.system.docker/bin/docker stop iptv-thuis-tv
systemctl start kodi
```

Voor een update: stop de tv-service, bouw het image opnieuw en maak de container opnieuw met dezelfde datamap. Verwijder nooit de datamap. Controleer na LibreELEC-updates opnieuw Docker, videoweergave en geluid.

## Gecontroleerde installatie

Getest op een AOpen DE7200 (i7-4700MQ, Intel HD Graphics 4600) met LibreELEC 12.2.1 Generic x86_64. Het eigen overzicht werkt op 1920×1080, met zenderlogo’s en de vrije ruimte van de `/storage`-schijf; vijf providerstreams spelen met VAAPI en een actieve stereo-ALSA-uitvoer via HDMI 2. Toetsgestuurd zappen en Terug zijn gecontroleerd. Bij een beëindigde container keert Kodi terug. Automatisch openen van het zichtbare zenderoverzicht na een herstart is gecontroleerd, met WireGuard actief en Kodi inactief. De gebruiker heeft de werking van de echte afstandsbediening bevestigd. Hoorbaar geluid vraagt daarnaast controle bij de televisie.

## Mediaknoppen van USB-afstandsbediening 1915:1025

Deze ontvanger meldt zijn Consumer Control-interface als tablet. Libinput behandelt hem daardoor als tabletpad en geeft volume/mute niet als toetsen door. De tv-image bevat een gerichte Xorg-regel `99-iptv-remote.conf` die alleen deze media-interface met evdev als toetsenbord uitleest. Absolute en relatieve assen van deze interface worden genegeerd. De muis en het gewone toetsenbord van de ontvanger blijven via libinput werken. Zie de [evdev-documentatie](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man4/evdev.4.html).

## YouTube cast

De tv-image bevat Node.js, een vastgelegde versie van `yt-cast-receiver` en yt-dlp met JavaScript-runtime. De native app start de ontvanger als kindproces; de naam in YouTube is **IPTV thuis**. Telefoon en tv moeten elkaar op het lokale netwerk kunnen bereiken. De hostnetwerkconfiguratie maakt DIAL/SSDP-discovery via UDP 1900 en de ontvanger-HTTP-poort TCP 8008 bereikbaar. **Menu → YouTube cast** vraagt een koppelcode aan als automatisch ontdekken niet lukt.

Caststatus en opdrachten gaan via lokale procespipes naar de Tk-interface. Alleen YouTube-video-ID’s worden geaccepteerd; de ontvanger kan geen willekeurige streamadressen of lokale bestanden opgeven. IPTV-opnames blijven onafhankelijk draaien. Stoppen met de afstandsbediening keert terug naar het zenderoverzicht. Ontvangeridentiteit en koppeling staan onder `/data/youtube-cast`, binnen het bestaande gegevensvolume.

Voor diagnose: controleer of TCP 8008 luistert en `/youtube/ssdp/device-desc.xml` een apparaat met naam IPTV thuis teruggeeft. Receiverfouten worden zonder ruwe token- of providergegevens getoond. Dit is geen algemene Google Cast-ontvanger voor andere streamingapps. Zet `IPTV_YOUTUBE_CAST=0` in de containeromgeving om de ontvanger uit te schakelen.

De yt-dlp-integratie gebruikt de `mweb`-client, controleert de beschikbaarheid van de gekozen formaten en verkrijgt benodigde afspeeltokens via `bgutil-ytdlp-pot-provider` 2.0.2. De Node-ontvanger beheert deze extra dienst op uitsluitend `127.0.0.1:4416`. Die poort wordt niet op het LAN gepubliceerd en tokenuitvoer wordt niet gelogd. Dit voorkomt de vastgestelde 403-fouten bij publieke video’s waarvoor alleen het ophalen van de videometadata onvoldoende was.
