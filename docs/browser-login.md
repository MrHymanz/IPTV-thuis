# Browserlogin en wachtwoordmanagers

Browsers krijgen `/auth/login`, geen HTTP Basic-popup. Traditioneel HTML-formulier
met labels, `name=username/password`, `autocomplete=username/current-password` en
POST/303-navigatie; geen wachtwoord in JavaScript, browseropslag, URL of cookies.
De gebruikersnaam blijft admin; bestaande `admin.json` en wachtwoorden blijven gelijk.
Native tv-/streamclients behouden Basic-ondersteuning, inclusief de challenge.

Sessies leven12uur in servergeheugen; tokens staan daar alleen gehasht. Na herstart
moet een browser opnieuw inloggen. HTTPS-cookies zijn host-only `__Host-iptv_session`,
Secure, HttpOnly, SameSite=Lax en Path=/. Direct LAN-HTTP gebruikt een aparte cookie
zonder Secure-prefix; gebruik de bestaande HTTPS-proxy voor versleutelde toegang.
X-Forwarded-Proto moet door die proxy betrouwbaar worden ingesteld.

Login gebruikt een eenmalige CSRF-cookie/nonce, origincontrole, begrensde request-
grootte en loginpogingen. Cookie-authenticated schrijfacties vereisen dezelfde origin
en de bestaande X-MijnTV-/content-typecontroles. POST `/auth/logout` trekt de sessie in.
Een logout-cookie voorkomt opnieuw aanmelden met een oude gecachte Basic-login.

De 1Password-extensie zelf is geen onderdeel van automatische tests; de juiste
formuliervelden, standaardnavigatie, cookiebeveiliging en authgrenzen worden getest.
Sla het item op met de gebruikte website-URL, bijvoorbeeld
`https://iptv.appeltaartmetslagroom.com`.
