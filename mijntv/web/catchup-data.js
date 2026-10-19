'use strict';
const CATCHUP_DATA = {
  "checked_date": "2026-10-19",
  "archive_days": 3,
  "sources": [
    {
      "channel": "NL| NPO 1 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NPO 2 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NPO 3 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| RTL 4 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| RTL 5 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| SBS 6 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| RTL 7 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| RTL 8 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| RTL Z HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| SBS 9 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| VERONICA / DISNEY JR HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NET 5 HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NPO 1 EXTRA HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NPO 2 EXTRA HD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| NPO 1 FHD",
      "days": 3,
      "category": "NL| ALGEMEEN"
    },
    {
      "channel": "NL| ESPN HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ESPN 1 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ESPN 2 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ESPN 3 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ESPN 4 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT HD ²",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT 2 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT 3 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT 4 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT 5 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| ZIGGO SPORT 6 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| EUROSPORT 1 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| EUROSPORT 2 HD",
      "days": 3,
      "category": "NL| SPORT"
    },
    {
      "channel": "NL| FILM 1 PREMIERE HD",
      "days": 3,
      "category": "NL| FILMS"
    },
    {
      "channel": "NL| FILM 1 ACTION HD",
      "days": 3,
      "category": "NL| FILMS"
    },
    {
      "channel": "NL| FILM 1 DRAMA HD",
      "days": 3,
      "category": "NL| FILMS"
    },
    {
      "channel": "NL| FILM 1 FAMILY HD",
      "days": 3,
      "category": "NL| FILMS"
    },
    {
      "channel": "NL| FILMBOX+ ONE",
      "days": 3,
      "category": "NL| FILMS"
    },
    {
      "channel": "NL| DISNEY CHANNEL HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| NICKELODEON HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| NICKELODEON SPIKE HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| NICKTOONS",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| NICK JR",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| NICK MUSIC",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| CARTOON NETWORK HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| BOOMERANG HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| RTL TELEKIDS HD",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| BABY TV",
      "days": 3,
      "category": "NL| KINDEREN"
    },
    {
      "channel": "NL| ZIGGO TV HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| RTL CRIME HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| RTL LOUNGE HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| E! HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| STAR CHANNEL HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| TLC HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| 24 KITCHEN HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| OUTTV HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| SHORTS TV HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| BBC NL HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| FASHION TV HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| MYZEN HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| NJAM! HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| CURIOSITY CHANNEL HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| PARAMOUNT NETWORK HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| COMEDY CENTRAL HD",
      "days": 3,
      "category": "NL| ENTERTAINMENT"
    },
    {
      "channel": "NL| NAT GEO WILD HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| NATIONAL GEOGRAPHIC HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| DISCOVERY HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| DISCOVERY SCIENCE HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| HISTORY HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| CI CRIME INVESTIGATION HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| ID HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| LOVE NATURE HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| ANIMAL PLANET HD",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    },
    {
      "channel": "NL| HORSE & COUNTRY",
      "days": 3,
      "category": "NL| DOCUMENTAIRE"
    }
  ]
};
