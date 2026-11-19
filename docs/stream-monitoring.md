# Streambereikbaarheid per account

De streamdiagnostiek bevat uitsluitend vaste gebeurtenisnamen, accountrollen,
interne bron-/zender-ID's en numerieke meetwaarden. Stream-URL's, gebruikersnamen,
wachtwoorden en ruwe ffmpeg-fouten horen niet in deze logregels.

- `stream_first_data`: de gebruikte bron levert werkelijk streamdata.
- `stream_data`: eenmaal per minuut zolang nieuwe bytes binnenkomen. Dit gebruikt
  de bestaande verbinding en opent geen teststream.
- `stream_feed_end`: reden, ontvangen bytes, duur en tijd sinds de laatste data.
  `closed` is normaal stoppen, geen bereikbaarheidsfout. HTTP-weigering, timeout
  en verbindingsfout zijn afzonderlijke redenen.
- `mobile_ready` / `mobile_failed`: mobiele verwerking met expliciete accountrol;
  een decoder-/omzettingsfout bewijst geen onbereikbare provider.

Nieuwe accountgebonden regels bevatten `role_version=2`. De drie rollen zijn
`primary`, `backup` en `recording`. De accountnaam is geen gebruikersnaam of
accountfingerprint. Lokale opgenomen bestanden hebben geen provideraccountrol.

Oudere opnamefeeds konden onjuist `account=primary` loggen doordat de rol van de
secondary-slot niet werd doorgegeven aan de Feed. Dit is gecorrigeerd door een
kopie van de bron met de berekende rol door te geven; de bronselectie, URL,
accountreservering en kijk-/opnameprioriteit veranderen hierdoor niet. Omdat
zender-ID's accountonafhankelijk zijn, mag een collector oude primary-regels niet
op basis van hetzelfde zender-ID alsnog aan hoofd- of opnameaccount toeschrijven.

Het homelab-project leest de bestaande bestanden en configuratievlaggen via SSH.
Het opent geen providerverbindingen en wijzigt de accountgegevens niet. Historische
waarnemingen zijn geen garantie dat alle URL's nu werken; een ongebruikt account
kan terecht geen recente waarneming hebben.
