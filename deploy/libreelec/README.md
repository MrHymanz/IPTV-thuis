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

Voer `create-container.sh` uit. De standaard HDMI-keuze is `alsa/hdmi:CARD=HDMI,DEV=2`, voor de geteste AOpen DE7200. Stel `IPTV_AUDIO_DEVICE` anders in als een andere aansluiting wordt gebruikt. Maak de container opnieuw om dat te wijzigen.

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
