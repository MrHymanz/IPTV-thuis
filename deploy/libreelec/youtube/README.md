# YouTube-ontvanger

`receiver.mjs` koppelt de YouTube-app op iPhone/Android via DIAL en de YouTube Lounge-service aan de native Tk/mpv-app. De Node-ontvanger stuurt uitsluitend gevalideerde opdrachten over stdin/stdout naar Python. De speler blijft eigendom van de tv-app; de Node-ontvanger heeft geen toegang tot IPTV-inloggegevens. Afspelen gebruikt mpv en yt-dlp, maximaal 1080p.

De LibreELEC-Dockerfile installeert dit automatisch. De image bevat daarnaast `bgutil-ytdlp-pot-provider` 2.0.2 voor de door YouTube gevraagde afspeelverificatie. De ontvanger start de bijbehorende lokale dienst op `127.0.0.1:4416` en stopt die bij afsluiten; tokens verschijnen niet in app-logs. yt-dlp gebruikt de `mweb`-client met `--check-formats`, zodat metadata van een onbereikbare mediastream niet als een bruikbare afspeelbron wordt beschouwd.

Voor een bestaande Debian/Ubuntu X11-installatie:

1. Installeer Node.js 22, npm, mpv, FFmpeg en een actuele yt-dlp inclusief de standaardextra’s en JavaScript-ondersteuning. Zorg dat `node` en `yt-dlp` beschikbaar zijn in het PATH van de tv-app. Configureer yt-dlp met `--js-runtimes node`, `--extractor-args youtube:player_client=mweb` en `--check-formats`. Installeer ook de [tokenprovider en bijbehorende plugin](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) en laat de provider uitsluitend op localhost luisteren.
2. Voer in deze map `npm ci --omit=dev --ignore-scripts` uit.
3. Start de tv-app met `IPTV_YOUTUBE_CAST=1` en `IPTV_CAST_SCRIPT=/absoluut/pad/naar/deze/map/receiver.mjs` in de omgeving.

De ontvanger luistert op TCP 8008 en adverteert met SSDP op UDP 1900. Alleen een native tv-instantie start de ontvanger; `--admin-only` doet dit niet. De ontvanger slaat zijn identiteit op in `youtube-cast` onder de gegevensmap van de app. Die identiteit en eventuele koppeling horen niet in Git of een publiek bronarchief.

Selecteer **IPTV thuis** via de castknop van YouTube. Een code aanvragen kan met **Menu → YouTube cast**. Voer die in via de YouTube-instellingen op de telefoon. Stoppen met Terug beëindigt alleen de videoweergave; bestaande IPTV-opnames blijven behouden.

De versie en transitive afhankelijkheden staan in `package-lock.json`. `yt-cast-receiver` heeft een MIT-licentie; de gebruikte `@patrickkfkan/peer-dial` is vrij voor niet-commercieel gebruik. Raadpleeg voor commercieel gebruik de [upstream licentievoorwaarden](https://github.com/patrickkfkan/yt-cast-receiver#license). Dit project levert geen algemene Chromecast- of AirPlay-implementatie.
