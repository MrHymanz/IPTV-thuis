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
- Een mislukte bron wordt tijdelijk lager geprioriteerd. Zijn alle vrije kijkaccounts in deze herstelpauze, dan probeert mobiel een vrij account alsnog. Alleen echte reserveringen tellen als bezet; een herstelpauze geeft geen onterechte melding dat alle accounts bezet zijn. Accountkeuze en afwijzingen worden zonder provideradressen, gebruikersnamen of wachtwoorden in `stream-diagnostics.log` vastgelegd.
- Lokale opnames gebruiken geen provideraccount. Terugkijken gebruikt voorlopig het oorspronkelijke providerarchief en kan alleen als dat oorspronkelijke kijkaccount vrij is. Het archief van het backupaccount wordt niet verondersteld hetzelfde aanbod te hebben.

Provideradressen en wachtwoorden blijven op het mediacenter. De browser ontvangt uitsluitend een tijdelijke sessiecode en beveiligde lokale HLS-adressen. Providergegevens staan niet in ffmpeg-procesargumenten; de invoer staat in een privé-manifest. Elke playlist en elk videosegment vereist dezelfde beheerlogin. De website en video komen van dezelfde server.

## Bediening

Live kun je binnen de beschikbare buffer (ongeveer anderhalve minuut) terugspoelen. Met **Naar live** keer je terug naar de actuele uitzending. Bij lokale opnames kun je met de tijdlijn naar een ander moment springen; de server maakt daarvoor een nieuwe afspeelsessie op die positie. Eén tik spoelt 15 seconden; vasthouden verhoogt de stap naar maximaal 30 seconden. Pauzeren van een opname pauzeert ook het omzetten, zodat het gekozen moment bewaard blijft. De laatst bekeken positie van lokale opnames wordt alleen op je telefoon bewaard.

In de gids kun je eerdere uitzendingen openen als het providerarchief die beschikbaar stelt, het huidige programma live bekijken of het resterende deel opnemen, en toekomstige programma's plannen. De app maakt geen voorbeeldopnames meer. Annuleren, stoppen en verwijderen worden bevestigd.

## Films en series

De tabs **Films** en **Series** laden hun eigen aanbod en categorieën via de Xtream-API van het hoofdaccount. Gelijknamige categorieën en identieke titel-ID's uit beide onderdelen blijven gescheiden. De categorienaam van de provider wordt overgenomen: HBO en Netflix verschijnen als filter wanneer de provider die indeling aanbiedt. Een M3U zonder bijbehorende Xtream-login biedt deze bibliotheek niet.

De bibliotheek heeft titelzoekopdrachten, categorieën, paginering per zestig titels, film-/seriecovers en detailvensters. Beschrijving, genre, jaar, beoordeling, speelduur, cast en regisseur verschijnen voor zover de provider ze meestuurt. Ontbrekende covers krijgen een tekstalternatief. Series tonen de beschikbare seizoenen en afleveringen op volgorde. Covers worden via het mediacenter opgehaald en gecachet; de browser krijgt geen externe coveradressen of providerinloggegevens.

Aanbod en details worden op aanvraag opgehaald en zes uur gecachet, los van de live-zenderlijst en favorieten. Bij een tijdelijke providerfout blijft eerder opgehaald aanbod beschikbaar met een melding. Een ander hoofdaccount krijgt een eigen cache, kijklijst en afspeelposities. De kijklijst en geschiedenis staan alleen in de lokale browseropslag: wissen van websitegegevens verwijdert ze en ze worden niet met andere telefoons gesynchroniseerd.

Als de categorieën wel beschikbaar zijn maar het volledige aanbod niet kan worden opgehaald (bijvoorbeeld door een time-out of een lijst groter dan 64 MB), vraagt de pagina om eerst een categorie te kiezen. Daarna worden alleen de titels uit die categorie bij de provider opgevraagd en afzonderlijk gecachet. Films en series houden ook hierbij hun eigen categorieën en cache. Foutmeldingen onderscheiden een time-out, geweigerde HTTP-aanvraag, te grote lijst en ongeldige providergegevens; ze tonen geen provideradressen of wachtwoorden.

In **Beheer → Mobiele films en series** kun je per onderdeel kiezen welke providercategorieën mobiel zichtbaar zijn. Na opslaan worden alleen deze categorieën op de achtergrond opgehaald. De mobiele startselectie voegt de vrijgegeven groepen samen; je hoeft niet telkens een categorie te kiezen. Tijdens het ophalen verschijnt bestaande inhoud meteen en wordt het overzicht automatisch aangevuld. De tv-lijst blijft ongewijzigd. Een lege selectie verbergt alle titels van het betreffende onderdeel; verborgen titels zijn ook via bewaarde detail-, cover- en afspeeladressen niet toegankelijk.

**Sorteren** biedt alfabetisch en beste providerbeoordeling, en bij films ook **IMDb Top 250**. IMDb-rangorde komt uitsluitend uit de officiële ranglijst, niet uit de providercijfers. De app probeert de lijst dagelijks te vernieuwen via [mmdju/imdb-top250-api](https://github.com/mmdju/imdb-top250-api). De JSON-API levert ook bron-, datum- en verouderingsinformatie. Bij oude API-gegevens probeert de app eerst IMDb zelf; lukt dat niet, dan kan een oudere API-momentopname worden gebruikt met een duidelijke melding. Een nieuwere of handmatig beheerde lijst wordt niet door een oudere API-momentopname vervangen. Als beide bronnen falen blijft de bestaande lijst behouden. Via het beheer kun je ook een JSON-lijst met `id` (bijvoorbeeld `tt0111161`), `rank`, `title` en `year` importeren. IMDb-ID's worden rechtstreeks gekoppeld; anders moeten genormaliseerde titel en, indien bekend, jaar eenduidig passen. API-titels zonder IMDb-ID worden uitsluitend op een eenduidige combinatie van titel en jaar gekoppeld. Alleen gekoppelde films uit de vrijgegeven selectie worden getoond. Er worden geen Top-250-posities verzonnen voor onbekende films.

## Weergave en thumbnails

Bij het tandwiel in de mobiele site kies je **Systeeminstelling**, **Licht** of **Donker**. De keuze wordt in de lokale browseropslag bewaard. Alleen algemene introductieteksten en kopjes wisselen bij een bezoek en iedere twee minuten tussen ruim honderd varianten; programmanamen, afleveringen en statusmeldingen blijven letterlijk weergegeven. Dezelfde tekst wordt niet twee keer achter elkaar uit dezelfde groep gekozen.

Bewaarde, afspeelbare opnames krijgen een thumbnail uit het lokale videobestand. Eén achtergrondtaak tegelijk maakt met FFmpeg een klein JPEG-beeld, zonder providerverbinding. Als een thumbnail nog wordt gemaakt of het bestand geen geschikt beeld bevat, blijft het bestaande opnamepictogram zichtbaar. Actieve of geplande opnames worden niet verwerkt. De cache wordt ongeldig wanneer bestandsgrootte of wijzigingstijd verandert en elke afbeelding vereist de beheerlogin.

Films en afleveringen gebruiken dezelfde HLS-speler, pauzeren en spoelknoppen. Bij een bekende speelduur werkt ook de tijdlijn; anders blijft de duur als onbekend aangegeven. De laatst bekeken positie wordt op de telefoon bewaard. **Verder kijken** toont eerder gestarte films en series; bij een serie wordt het laatst bekeken seizoen voorgeselecteerd en de afspeelpositie per aflevering getoond.

Deze streams gebruiken uitsluitend het hoofdaccount. Een film- of aflevering-ID van dat account mag niet zomaar op een backupaccount worden gebruikt. Als tv of opname het hoofdaccount nodig heeft, krijgt die voorrang en stopt eerst de mobiele stream. Er wordt geen extra gelijktijdige providerverbinding geopend om een bezet account te omzeilen. Je eigen provider en fysieke iPhone/Android blijven nodig om de beschikbare titels, codecs, geluid en de netwerkroute te controleren.

De huidige ondersteuning richt zich op de bestaande x86_64-mediacenterinstallatie. Test op je eigen iPhone/Safari en Android-toestel blijft nodig voor browserbediening, hoorbaar geluid en je eigen WireGuard-route. Een Raspberry Pi 5 is nog geen geteste vervanging voor het huidige mediacenter.

## hls.js

Vendored versie: **1.7.3** uit het officiële npm-pakket `hls.js`; SHA-512-integriteit van het pakket is tijdens het ophalen gecontroleerd.

- Bestand: `mijntv/web/hls.min.js`
- SHA-256: `a12e7ee1cd64a69dcdb314157e45dafcba705bfb0b1440b7935cb265d374423e`
- Licentie: Apache-2.0, meegeleverd als `mijntv/web/hls.LICENSE.txt`
- Bron en compatibiliteit: https://github.com/video-dev/hls.js/tree/v1.7.3

## Ontbrekende posters en achtergronden (TMDB)

In **Beheer → Mobiele films en series → Ontbrekende posters en banners aanvullen (TMDB)** sla je een eigen TMDB API-key of API Read Access Token op. De knop **Verbinding testen** controleert de opgeslagen sleutel. Het beheer toont de sleutel nooit terug; leegmaken via **TMDB-aanvulling uitschakelen** schakelt aanvullende aanvragen uit. Zonder sleutel verandert de bestaande providerweergave niet.

Een geldige providerafbeelding blijft de eerste keuze. Een ontbrekende of defecte poster wordt via TMDB aangevuld; voor brede achtergronden worden afzonderlijke backdrop-afbeeldingen gebruikt. Films en series worden apart gezocht. Een expliciete TMDB-ID heeft voorrang, gevolgd door een IMDb-ID. Zonder ID worden titel en, indien bekend, jaartal gecontroleerd en moet er precies één passende titel overblijven. Remakes of meerdere gelijke titels krijgen geen willekeurige afbeelding. Ontbreekt ook bij TMDB een passende afbeelding, dan blijft de tekstcover staan.

De server stuurt alleen de titelidentiteit naar TMDB, nooit IPTV-accounts of provideradressen. De TMDB-sleutel blijft in de private instellingendatabase. Metadata wordt maximaal dertig dagen gecachet; zonder match wordt maximaal een dag gewacht voor een nieuwe zoekpoging. Verbindings- of authenticatiefouten krijgen een herstelpauze van vijf minuten. Een andere sleutel of provider krijgt een eigen cache; verborgen categorieën kunnen ook via oude afbeeldingsadressen niet worden geopend. De rasterafbeeldingen worden lokaal bewaard door de bestaande afbeeldingscache. De mobiele Over-sectie bevat de TMDB-bronvermelding en het officiële logo; dat openbare logo wordt rechtstreeks bij TMDB geladen.

API-documentatie: [identificatie via IMDb](https://developer.themoviedb.org/reference/find-by-id), [afbeeldingen](https://developer.themoviedb.org/docs/image-basics), [API-sleutel aanvragen](https://www.themoviedb.org/settings/api).
