# Mobiel kijken

Open `/mobile` op het mediacenter en log in met de bestaande beheerlogin. Thuis werkt dat via wifi; buiten het thuisnetwerk verbind je eerst je telefoon met je eigen WireGuard-VPN. Er is geen centrale accountserver. Gebruik de pagina op dezelfde pc als de tv-app: een losse beheerserver met een kopie van je accounts deelt geen streamreserveringen met de tv.

De pagina gebruikt echte favorieten, zenderlogo's, programmagids, opnameplanning, opslaggegevens en bewaarde opnames. Live-video wordt lokaal omgezet naar HLS met H.264/AAC, maximaal 1280 pixels breed en 25 fps. Safari gebruikt de ingebouwde HLS-speler. Voor browsers met MediaSource wordt de meegeleverde hls.js-versie gebruikt; er worden geen scripts van een externe CDN geladen.

## Accounts en voorrang

- De tv behoudt zijn actieve kijkaccount.
- Het apart ingestelde opnameaccount wordt voor opnames gereserveerd, ook als er nog geen opname loopt.
- Mobiel kiest een vrij primair of backupkijkaccount met een passende bron voor de gekozen favoriet.
- Gebruikersnaam en wachtwoord bepalen of twee serveradressen hetzelfde account voorstellen. Een serveralias geeft geen extra verbinding.
- Als tv of recorder een door mobiel gebruikt account nodig heeft, stopt eerst de mobiele providerverbinding. Daarna krijgt de tv/recorder het account. Mobiel probeert waar mogelijk een ander vrij kijkaccount; zonder vrije bron verschijnt een melding.
- Er is voorlopig één mobiele speler tegelijk. Sluiten geeft het account vrij. Zonder heartbeat/videoverzoeken vervalt een sessie na 45 seconden.
- Lokale opnames gebruiken geen provideraccount. Terugkijken gebruikt voorlopig het oorspronkelijke providerarchief en kan alleen als dat oorspronkelijke kijkaccount vrij is. Het archief van het backupaccount wordt niet verondersteld hetzelfde aanbod te hebben.

Provideradressen en wachtwoorden blijven op het mediacenter. De browser ontvangt uitsluitend een tijdelijke sessiecode en beveiligde lokale HLS-adressen. Providergegevens staan niet in ffmpeg-procesargumenten; de invoer staat in een privé-manifest. Elke playlist en elk videosegment vereist dezelfde beheerlogin. De website en video komen van dezelfde server.

## Bediening

Live kun je binnen de beschikbare buffer (ongeveer anderhalve minuut) terugspoelen. Met **Naar live** keer je terug naar de actuele uitzending. Bij lokale opnames kun je met de tijdlijn naar een ander moment springen; de server maakt daarvoor een nieuwe afspeelsessie op die positie. Eén tik spoelt 15 seconden; vasthouden verhoogt de stap naar maximaal 30 seconden. Pauzeren van een opname pauzeert ook het omzetten, zodat het gekozen moment bewaard blijft. De laatst bekeken positie van lokale opnames wordt alleen op je telefoon bewaard.

In de gids kun je eerdere uitzendingen openen als het providerarchief die beschikbaar stelt, het huidige programma live bekijken of het resterende deel opnemen, en toekomstige programma's plannen. De app maakt geen voorbeeldopnames meer. Annuleren, stoppen en verwijderen worden bevestigd.

De huidige ondersteuning richt zich op de bestaande x86_64-mediacenterinstallatie. Test op je eigen iPhone/Safari en Android-toestel blijft nodig voor browserbediening, hoorbaar geluid en je eigen WireGuard-route. Een Raspberry Pi 5 is nog geen geteste vervanging voor het huidige mediacenter.

## hls.js

Vendored versie: **1.7.3** uit het officiële npm-pakket `hls.js`; SHA-512-integriteit van het pakket is tijdens het ophalen gecontroleerd.

- Bestand: `mijntv/web/hls.min.js`
- SHA-256: `a12e7ee1cd64a69dcdb314157e45dafcba705bfb0b1440b7935cb265d374423e`
- Licentie: Apache-2.0, meegeleverd als `mijntv/web/hls.LICENSE.txt`
- Bron en compatibiliteit: https://github.com/video-dev/hls.js/tree/v1.7.3
