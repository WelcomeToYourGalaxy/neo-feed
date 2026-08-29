#!/usr/bin/env python3
"""
harvest_neo.py — the near-Earth object wire: what is coming close, what has
come down, what the projections say, and the part of the space industry built
to find and deflect it.

Self-contained: fetching, feed parsing, word-edge matching and deduplication are
all in this file. Reads sources_neo.json, writes wire_neo.json. Standard library
only — no dependencies, no API keys, no model calls.

The scope is deliberately narrow. This is not a space-industry feed: launches,
constellations, crewed flight and orbital politics belong elsewhere. What is here
is asteroids, comets and meteoroids on Earth-crossing paths, the events they
produce, the projections made about them, and the surveys, missions and companies
whose work is finding and deflecting them.

Every story carries a standing — official, science, press or specialist — and a
significance score built from confirmed events, official risk listings, named
objects, measured sizes and distances, and forward projections. A fireball
someone filmed and a change to the ESA risk list both appear, and the page never
lets you mistake one for the other.

    python3 harvest_neo.py
    python3 harvest_neo.py --dry-run
    python3 harvest_neo.py --fixtures DIR
"""

import argparse
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, "sources_neo.json")
OUT_PATH = os.path.join(HERE, "wire_neo.json")

RETAIN_DAYS = 45
MAX_ITEMS = 1200
WORKERS = 6
NOTABLE_SCORE = 3       # at or above this a story is marked as notable

# --------------------------------------------------------------------------
# Plumbing: fetching, feed parsing, word-edge matching, fingerprints.
# --------------------------------------------------------------------------
USER_AGENT = ("Mozilla/5.0 (compatible; space-life-news/1.0; "
              "+https://github.com/WelcomeToYourGalaxy/space-life-news)")

TIMEOUT = 25

SNIPPET_CHARS = 240

TAG_RE = re.compile(r"<[^>]+>")

WS_RE = re.compile(r"\s+")

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

def build_gnews_url(loc):
    q = loc["query"] + " when:30d"
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
            "&hl=" + loc["hl"] + "&gl=" + loc["gl"] + "&ceid=" + loc["ceid"])

def fetch(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Encoding": "gzip",
                "Accept-Language": "*",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except Exception as exc:                       # noqa: BLE001 — report, don't crash the run
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print("  ! unreachable: %s (%s)" % (url[:90], last), file=sys.stderr)
    return None

def strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag

def text_of(el):
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", el.text or ""))).strip() if el is not None else ""

def child(node, *names):
    for kid in node:
        if strip_ns(kid.tag) in names:
            return kid
    return None

def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None

def parse_feed(raw, src):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Some publishers serve a stray byte before the declaration.
        try:
            root = ET.fromstring(raw[raw.index(b"<"):])
        except Exception:  # noqa: BLE001
            return []

    nodes = [n for n in root.iter() if strip_ns(n.tag) == "item"]
    atom = False
    if not nodes:
        nodes = [n for n in root.iter() if strip_ns(n.tag) == "entry"]
        atom = True

    out = []
    for n in nodes:
        title = text_of(child(n, "title"))
        if atom:
            link = ""
            for kid in n:
                if strip_ns(kid.tag) == "link" and kid.get("rel", "alternate") == "alternate":
                    link = kid.get("href", "")
                    break
        else:
            link_el = child(n, "link")
            link = (link_el.text or "").strip() if link_el is not None else ""
            if not link:
                link = text_of(child(n, "guid"))
        if not title or not link:
            continue

        outlet_el = child(n, "source")
        outlet = text_of(outlet_el) if outlet_el is not None else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -(len(outlet) + 3)].strip()
        elif not outlet and src["name"].startswith("Google News") and " - " in title:
            # Google News appends the outlet to the headline when it omits <source>.
            head, _, tail = title.rpartition(" - ")
            if head and 2 <= len(tail) <= 45:
                title, outlet = head.strip(), tail.strip()

        stamp = parse_date(text_of(child(n, "pubDate", "published", "updated", "date")))
        snippet = text_of(child(n, "description", "summary", "content"))[:SNIPPET_CHARS]

        out.append({
            "t": title,
            "u": link,
            "o": outlet or src["name"].replace("Google News · ", ""),
            "g": src["lang"],
            "r": src["region"],
            "k": src.get("kind", "news"),
            "d": stamp,
            "s": snippet,
            "w": src["name"],
        })
    return out

def _compile(term):
    if any(ord(ch) > 0x24F for ch in term):        # non-Latin script
        # substring matching is already prefix-like in scripts without word
        # breaks, so a trailing * is a no-op — strip it rather than search for
        # a literal asterisk, which is what used to happen.
        return term[:-1] if term.endswith("*") else term
    if term.endswith("*"):
        return re.compile(r"(?<![a-z0-9])" + re.escape(term[:-1]) + r"[a-z0-9\-]*", re.I)
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I)

def _compile_all(terms):
    return [_compile(t) for t in terms]

def hit(text, compiled):
    """True when any compiled term matches."""
    for c in compiled:
        if isinstance(c, str):
            if c in text:
                return True
        elif c.search(text):
            return True
    return False

def fingerprint(title):
    norm = PUNCT_RE.sub(" ", title.lower())
    return " ".join(WS_RE.sub(" ", norm).strip().split()[:9])

def canon_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query)
        query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"),
                                        urllib.parse.urlencode(query), ""))
    except Exception:  # noqa: BLE001
        return url


# --------------------------------------------------------------------------
# Where the story is.  This is the region the finding concerns, not the region
# the wire was read from — a Japanese outlet reporting on the Amazon files
# under Latin America.  A story with global scope files under Global, and one
# can carry several: a study spanning Africa and South Asia files under both.
# --------------------------------------------------------------------------
GEO = [
    ("africa", "Africa", [
        ("africa*", None), ("sahel", None), ("congo basin", None), ("nigeria*", None),
        ("kenya*", None), ("ethiopia*", None), ("democratic republic of congo", None),
        ("drc", None), ("ghana", None), ("tanzania*", None), ("uganda*", None),
        ("south africa*", None), ("zimbabwe*", None), ("zambia*", None), ("mozambique", None),
        ("angola*", None), ("senegal", None), ("mali", ["africa", "sahel", "bamako", "drought"]),
        ("chad", ["lake", "africa", "sahel", "basin"]), ("sudan*", None), ("somalia*", None),
        ("madagascar", None), ("cameroon", None), ("côte d'ivoire", None), ("ivory coast", None),
        ("botswana", None), ("namibia", None), ("malawi", None), ("rwanda", None),
        ("okavango", None), ("lake victoria", None), ("serengeti", None), ("kalahari", None),
        ("horn of africa", None), ("afrique", None), ("áfrica", None), ("afrika", None),
        ("非洲", None), ("アフリカ", None), ("африк*", None), ("أفريقيا", None), ("अफ्रीका", None),
    ]),
    ("mena", "Middle East & North Africa", [
        ("middle east*", None), ("egypt*", None), ("morocco", None), ("algeria*", None),
        ("tunisia*", None), ("libya*", None), ("saudi arabia", None), ("emirates", None),
        ("qatar", None), ("kuwait", None), ("oman", None), ("yemen*", None), ("iraq*", None),
        ("iran*", None), ("israel*", None), ("palestin*", None), ("gaza", None), ("jordan", None),
        ("lebanon", None), ("syria*", None), ("turkey", ["drought", "climate", "pollution", "earthquake", "istanbul", "anatolia"]),
        ("türkiye", None), ("persian gulf", None), ("red sea", None), ("euphrates", None),
        ("tigris", None), ("dead sea", None), ("sahara", None), ("الشرق الأوسط", None),
        ("中东", None), ("北アフリカ", None),
    ]),
    ("asia", "Asia", [
        ("asia*", None), ("china", None), ("chinese", ["government", "province", "coal", "emissions", "cities"]),
        ("japan*", None), ("korea*", None), ("india", None), ("indian", ["ocean", "government", "farmers", "cities", "monsoon", "state"]),
        ("pakistan*", None), ("bangladesh*", None), ("nepal*", None), ("sri lanka", None),
        ("indonesia*", None), ("vietnam*", None), ("thailand", None), ("philippines", None),
        ("malaysia*", None), ("myanmar", None), ("cambodia*", None), ("laos", None),
        ("mongolia*", None), ("kazakhstan", None), ("uzbekistan", None), ("central asia", None),
        ("himalaya*", None), ("mekong", None), ("ganges", None), ("yangtze", None),
        ("brahmaputra", None), ("tibet*", None), ("borneo", None), ("sumatra", None),
        ("aral sea", None), ("gobi", None), ("siberia*", None), ("アジア", None), ("亚洲", None),
        ("아시아", None), ("एशिया", None), ("азия", None),
    ]),
    ("europe", "Europe", [
        ("europe*", ["union", "countries", "climate", "commission", "continent", "wide", "study", "across"]),
        ("european union", None), ("european commission", None), ("brussels", None),
        ("eu", ["deforestation", "regulation", "law", "directive", "commission", "member states",
                "emissions", "green deal", "farm", "policy", "ban", "target"]),
        ("united kingdom", None), ("britain", None), ("england", None),
        ("scotland", None), ("wales", ["climate", "flood", "farm", "coast"]), ("ireland", None),
        ("france", None), ("germany", None), ("spain", None), ("portugal", None), ("italy", None),
        ("greece", None), ("netherlands", None), ("belgium", None), ("poland", None),
        ("ukraine", None), ("russia*", None), ("sweden", None), ("norway", None), ("finland", None),
        ("denmark", None), ("switzerland", None), ("austria", None), ("romania", None),
        ("hungary", None), ("czech*", None), ("balkans", None), ("danube", None), ("alps", None),
        ("mediterranean", None), ("baltic", None), ("北欧", None), ("欧洲", None), ("ヨーロッパ", None),
        ("유럽", None), ("европ*", None), ("أوروبا", None),
    ]),
    ("latam", "Latin America & Caribbean", [
        ("latin america*", None), ("south america*", None), ("central america*", None),
        ("brazil*", None), ("brasil", None), ("amazon", None), ("amazônia", None), ("amazonía", None),
        ("argentina", None), ("chile", None), ("peru", None), ("colombia*", None),
        ("venezuela*", None), ("ecuador", None), ("bolivia*", None), ("paraguay", None),
        ("uruguay", None), ("mexico", None), ("méxico", None), ("guatemala", None),
        ("honduras", None), ("nicaragua", None), ("costa rica", None), ("panama", None),
        ("cuba", None), ("haiti", None), ("dominican republic", None), ("caribbean", None),
        ("patagonia", None), ("andes", None), ("cerrado", None), ("pantanal", None),
        ("gran chaco", None), ("orinoco", None), ("américa latina", None), ("拉丁美洲", None),
        ("ラテンアメリカ", None), ("латинская америка", None),
    ]),
    ("northam", "North America", [
        ("united states", None), ("u.s.", None), ("usa", None), ("american", ["government", "cities", "states", "west", "farmers", "midwest", "coast"]),
        ("canada", None), ("canadian", None), ("alaska*", None), ("california", None),
        ("texas", None), ("florida", None), ("great lakes", None), ("colorado river", None),
        ("mississippi", None), ("appalachia*", None), ("quebec", None), ("ontario", None),
        ("british columbia", None), ("gulf of mexico", None), ("états-unis", None),
        ("estados unidos", None), ("美国", None), ("加拿大", None), ("アメリカ合衆国", None),
        ("미국", None), ("сша", None),
    ]),
    ("oceania", "Oceania", [
        ("australia*", None), ("new zealand", None), ("aotearoa", None), ("papua", None),
        ("pacific island*", None), ("fiji", None), ("samoa", None), ("tonga", None),
        ("vanuatu", None), ("solomon islands", None), ("kiribati", None), ("tuvalu", None),
        ("great barrier reef", None), ("tasmania*", None), ("murray-darling", None),
        ("オセアニア", None), ("大洋洲", None), ("océanie", None),
    ]),
    ("polar", "Arctic & Antarctic", [
        ("arctic", None), ("antarctic*", None), ("greenland", None), ("svalbard", None),
        ("north pole", None), ("south pole", None), ("tundra", None), ("北極", None),
        ("南極", None), ("арктик*", None), ("antártic*", None), ("arctique", None),
    ]),
    ("ocean", "Oceans & high seas", [
        ("pacific ocean", None), ("atlantic ocean", None), ("indian ocean", None),
        ("southern ocean", None), ("high seas", None), ("open ocean", None),
        ("coral triangle", None), ("mariana", None), ("deep sea", None), ("north sea", None),
        ("bering sea", None), ("south china sea", None), ("océan pacifique", None),
        ("公海", None), ("深海", None),
    ]),
]


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------
TOPICS = [
    ("discovery", "Discovery & surveys", [
        ("newly discovered", ["asteroid", "comet", "object", "neo"]),
        ("pan-starrs", None), ("catalina sky survey", None), ("atlas survey", None),
        ("asteroid terrestrial-impact last alert", None), ("neowise", None),
        ("neo surveyor", None), ("rubin observatory", None), ("vera rubin", None),
        ("minor planet center", None), ("mpc", ["asteroid", "designation", "minor planet"]),
        ("survey telescope", ["asteroid", "neo", "sky"]), ("first detected", ["asteroid", "comet", "object"]),
        ("découverte", ["astéroïde", "comète"]), ("descubrimiento", ["asteroide", "cometa"]),
        ("新発見", ["小惑星", "彗星"]), ("发现", ["小行星", "彗星", "近地"]),
    ]),
    ("approach", "Close approaches", [
        ("close approach", None), ("closest approach", None), ("flyby", ["asteroid", "comet", "earth", "object"]),
        ("lunar distance*", None), ("passes earth", None), ("passed earth", None),
        ("will pass", ["asteroid", "comet", "earth"]), ("safely pass*", None),
        ("skimmed", ["earth", "atmosphere", "asteroid"]), ("geosynchronous", ["asteroid", "pass"]),
        ("aproximación", ["asteroide", "tierra"]), ("passage", ["astéroïde", "terre"]),
        ("vorbeiflug", ["asteroid", "erde"]), ("сблиз", ["астероид", "земл", "комет"]),
        ("лунной дистанции", None), ("лунных дистанц", None),
        ("接近", ["小惑星", "地球", "近地"]), ("飞掠", None), ("飛掠", None), ("掠过", ["地球"]),
        ("근접", ["소행성", "지구"]), ("最接近", None),
    ]),
    ("impact", "Impacts & airbursts", [
        ("airburst*", None), ("bolide*", None),
        ("fireball", ["meteor", "meteoroid", "meteorite", "sky", "atmosphere", "witness",
                      "reported", "camera", "night", "seen over", "streaked"]),
        ("meteorite fall*", None), ("meteorite recovered", None), ("meteorite found", None),
        ("meteorite strike", None), ("impact event", None), ("crater", ["impact", "asteroid", "meteorite"]),
        ("chelyabinsk", None), ("tunguska", None), ("entered the atmosphere", None),
        ("burn* up", ["atmosphere", "meteor", "meteoroid", "object"]),
        ("shock wave", ["meteor", "airburst", "blast"]),
        ("meteorito", ["caída", "cayó", "impacto"]), ("météorite", ["chute", "impact"]),
        ("meteoritenfall", None), ("падение метеорита", None), ("陨石", ["坠落", "撞击"]),
        ("隕石", ["落下", "衝突"]), ("운석", ["낙하", "충돌"]), ("उल्कापिंड", None),
    ]),
    ("risk", "Risk & projections", [
        ("impact probability", None), ("probability of impact", None), ("torino scale", None),
        ("palermo scale", None), ("risk list", ["asteroid", "esa", "neo"]),
        ("sentry", ["nasa", "asteroid", "risk", "impact"]), ("virtual impactor*", None),
        ("potentially hazardous", None), ("apophis", None), ("2024 yr4", None), ("2032", ["asteroid", "impact", "chance"]),
        ("odds of impact", None), ("removed from the risk", None), ("ruled out", ["impact", "asteroid", "collision"]),
        ("collision course", None), ("probabilidad de impacto", None), ("probabilité d'impact", None),
        ("einschlagwahrscheinlichkeit", None), ("撞击概率", None), ("衝突確率", None),
    ]),
    ("defence", "Deflection & defence", [
        ("planetary defen*", None), ("dart mission", None), ("double asteroid redirection", None),
        ("hera mission", None), ("dimorphos", None), ("didymos", None),
        ("kinetic impactor", None), ("gravity tractor", None), ("nuclear deflection", None),
        ("deflect*", ["asteroid", "comet", "object", "impact"]),
        ("planetary defense conference", None), ("défense planétaire", None),
        ("defensa planetaria", None), ("planetenverteidigung", None), ("行星防御", None),
        ("行星防禦", None), ("プラネタリーディフェンス", None), ("행성 방어", None),
        ("планетарная защита", None),
    ]),
    ("missions", "Missions & sample return", [
        ("osiris-rex", None), ("osiris-apex", None), ("bennu", None), ("hayabusa", None),
        ("ryugu", None), ("lucy mission", None), ("psyche mission", None), ("tianwen-2", None),
        ("sample return", ["asteroid", "comet", "bennu", "ryugu"]),
        ("rendezvous", ["asteroid", "comet"]), ("touch-and-go", ["asteroid", "sample"]),
        ("rosetta", ["comet", "philae", "67p"]), ("67p", None), ("comet interceptor", None),
    ]),
    ("science", "Science & composition", [
        ("yarkovsky", None), ("yorp effect", None), ("orbital dynamics", ["asteroid", "comet", "neo"]),
        ("spectral type", ["asteroid"]), ("rubble pile", None), ("albedo", ["asteroid", "object"]),
        ("meteoritics", None), ("chondrite*", None), ("organics", ["asteroid", "meteorite", "bennu", "ryugu"]),
        ("water", ["asteroid", "meteorite", "bennu", "ryugu", "comet"]),
        ("age of the solar system", None), ("primordial", ["asteroid", "comet", "material"]),
    ]),
    ("industry", "Industry & instruments", [
        ("contract", ["asteroid", "neo surveyor", "planetary defense", "telescope", "survey"]),
        ("radar", ["asteroid", "goldstone", "arecibo", "neo"]), ("goldstone", None), ("arecibo", None),
        ("observatory funding", None), ("telescope contract", None), ("startup", ["asteroid", "space mining", "prospect"]),
        ("asteroid mining", None), ("prospect*", ["asteroid", "resources"]),
        ("astroforge", None), ("karman project", None), ("deflection technology", None),
    ]),
    ("policy", "Policy & coordination", [
        ("iawn", None), ("international asteroid warning", None), ("smpag", None),
        ("space mission planning advisory", None), ("copuos", None), ("un asteroid", None),
        ("asteroid day", None), ("civil defence", ["asteroid", "impact", "warning"]),
        ("evacuation plan", ["impact", "asteroid"]), ("warning system", ["asteroid", "impact", "neo"]),
        ("budget", ["planetary defense", "neo surveyor", "asteroid"]),
    ]),
    ("interstellar", "Interstellar visitors", [
        ("interstellar object", None), ("interstellar comet", None), ("'oumuamua", None),
        ("oumuamua", None), ("2i/borisov", None), ("3i/atlas", None), ("hyperbolic orbit", None),
    ]),
]

# --------------------------------------------------------------------------
# The gate.
#
# ANCHOR — the story concerns an Earth-crossing small body, an event it caused,
#          or the work of finding and deflecting one.  This is deliberately not
#          a space-industry feed: a launch, a constellation or a crewed mission
#          gets in only when an asteroid or comet is the subject.
# BLOCK  — the film titles, the astrology column, and "meteoric" in its
#          figurative sense, which is what fills a feed like this otherwise.
# --------------------------------------------------------------------------
ANCHOR = [
    "asteroid*", "comet*", "meteor", "meteors", "meteorite*", "meteoroid*", "bolide*",
    "near-earth object*", "near earth object*", "near-earth asteroid*", "neo surveyor",
    "potentially hazardous asteroid*", "minor planet*", "planetary defen*",
    "torino scale", "palermo scale", "impact hazard", "impact probability",
    "airburst*", "fireball", "apophis", "bennu", "ryugu", "didymos", "dimorphos",
    "'oumuamua", "oumuamua", "2i/borisov", "3i/atlas", "interstellar object",
    "pan-starrs", "catalina sky survey", "minor planet center", "hayabusa", "osiris-rex",
    "osiris-apex", "dart mission", "hera mission", "lucy mission", "comet interceptor",
    "astéroïde*", "comète*", "météorite*", "météore*", "bolide*", "géocroiseur*",
    "asteroide*", "cometa*", "meteorito*", "bólido*", "planetoïde*", "vuurbol",
    "meteorit*", "erdnah*", "feuerkugel", "asteroide", "meteorite", "bolide",
    "planetoida*", "bolid*", "астероид*", "метеорит*", "болид*", "околоземн*",
    "астероїд*", "метеорит*", "小行星", "彗星", "隕石", "陨石", "火球", "火流星",
    "近地天体", "近地天體", "소행성", "운석", "화구", "혜성", "كويكب", "نيزك",
    "क्षुद्रग्रह", "उल्कापिंड", "গ্রহাণু", "উল্কাপিণ্ড", "asteroit", "göktaşı",
    "tiểu hành tinh", "thiên thạch", "ดาวเคราะห์น้อย", "อุกกาบาต", "αστεροειδ*",
    "μετεωρίτ*", "אסטרואיד", "מטאוריט", "سیارک", "شهاب‌سنگ", "asteroid dekat bumi",
]

BLOCK = [
    # figurative and fictional
    "meteoric rise", "meteoric career", "asteroid city", "armageddon film", "deep impact film",
    "don't look up", "box office", "streaming series", "season finale", "video game",
    "asteroids arcade", "comet browser", "comet tv", "halley's comet beer",
    # astrology, which uses these words constantly
    "horoscope", "astrolog*", "zodiac", "retrograde", "birth chart", "chiron in", "tarot",
    # doom-mongering with no object behind it
    "world will end", "planet x", "nibiru", "doomsday prophecy", "prophecy",
    # sport and product namesakes
    "houston rockets", "premier league", "asteroid crypto", "comet token",
]

# --------------------------------------------------------------------------
# Significance. Standing says who is speaking; this says how much happened.
# --------------------------------------------------------------------------
EVENT = [
    "airburst*", "meteorite fall*", "meteorite recovered", "meteorite found", "meteorite strike",
    "entered the atmosphere", "burned up", "impact event", "struck", "exploded over",
    "witnesses reported", "caught on camera", "shock wave", "damage reported",
    "caída de meteorito", "chute de météorite", "meteoritenfall", "падение метеорита",
    "隕石落下", "陨石坠落", "운석 낙하",
]
OFFICIAL_LIST = [
    "risk list", "sentry", "torino scale", "palermo scale", "impact probability",
    "potentially hazardous", "minor planet center", "iawn", "esa neocc", "jpl small-body",
    "removed from the risk", "ruled out", "designation", "circular",
]
PROJECTION = [
    "projected", "projection", "forecast", "predicted", "will pass", "expected in",
    "2029", "2032", "2036", "next century", "in the coming decades", "long-term risk",
    "trajectory analysis", "orbit refined", "orbital solution",
]
MEASURED = [
    "lunar distance*", "kilometres from earth", "kilometers from earth", "miles from earth",
    "metres wide", "meters wide", "metre-wide", "meter-wide", "kilometre-wide", "kilometer-wide",
    "diameter", "magnitude", "velocity", "km/s", "estimated size",
]
NAMED_OBJECT = re.compile(r"\b(19|20)\d\d\s?[A-Z]{2}\d*\b|\(\d{3,6}\)|\b\d{1,3}[Pp]/\b|\bC/\d{4}\b")


ANCHOR_C = _compile_all(ANCHOR)
BLOCK_C = _compile_all(BLOCK)
EVENT_C = _compile_all(EVENT)
OFFICIAL_LIST_C = _compile_all(OFFICIAL_LIST)
PROJECTION_C = _compile_all(PROJECTION)
MEASURED_C = _compile_all(MEASURED)
TOPICS_C = [(tid, label, [(_compile(t), _compile_all(g) if g else None) for t, g in terms])
            for tid, label, terms in TOPICS]
GEO_C = [(gid, label, [(_compile(t), _compile_all(g) if g else None) for t, g in terms])
         for gid, label, terms in GEO]


def relevant(text):
    """An Earth-crossing body, an event it caused, or the work of finding and
    deflecting one. Figurative meteors and astrology columns are refused."""
    if hit(text, BLOCK_C):
        return False
    return hit(text, ANCHOR_C)


def significance(text, standing):
    """How much actually happened, as a score and the reasons for it."""
    total, reasons = 0, []
    if hit(text, EVENT_C):
        total += 2
        reasons.append("event")
    if hit(text, OFFICIAL_LIST_C):
        total += 2
        reasons.append("risk listing")
    if hit(text, MEASURED_C):
        total += 1
        reasons.append("measured")
    if hit(text, PROJECTION_C):
        total += 1
        reasons.append("projection")
    if NAMED_OBJECT.search(text):
        total += 1
        reasons.append("named object")
    if standing in ("official", "science"):
        total += 1
        reasons.append("primary source")
    return total, reasons


def topics_for(text):
    hits = []
    for tid, _label, terms in TOPICS_C:
        for term, guards in terms:
            if not hit(text, [term]):
                continue
            if guards and not hit(text, guards):
                continue
            hits.append(tid)
            break
    return hits


def regions_for(text):
    hits = []
    for gid, _label, terms in GEO_C:
        for term, guards in terms:
            if not hit(text, [term]):
                continue
            if guards and not hit(text, guards):
                continue
            hits.append(gid)
            break
    return hits or ["unlocated"]


def load_sources():
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    srcs = []
    for s in cfg.get("direct", []):
        srcs.append({"name": s["name"], "lang": s["lang"], "standing": s["standing"],
                     "region": s["standing"], "kind": s.get("kind", "news"), "url": s["url"]})
    for block, prefix in (("gnews", "Google News · "), ("events", "Events · ")):
        for loc in cfg.get(block, []):
            srcs.append({"name": prefix + loc["label"], "lang": loc["lang"],
                         "standing": loc["standing"], "region": loc["standing"],
                         "kind": "news", "url": build_gnews_url(loc)})
    return srcs, cfg


def run(dry_run=False, fixtures=None):
    sources, cfg = load_sources()
    print("Reading %d wires…" % len(sources))

    def read(src):
        if fixtures:
            path = os.path.join(fixtures, re.sub(r"[^\w.-]", "_", src["name"]) + ".xml")
            if not os.path.exists(path):
                return src, None
            with open(path, "rb") as fh:
                return src, fh.read()
        return src, fetch(src["url"])

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for src, raw in pool.map(read, sources):
            results.append((src, raw))

    previous = []
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as fh:
                previous = json.load(fh).get("items", [])
        except Exception:  # noqa: BLE001
            previous = []

    seen_fp, seen_url, items = set(), set(), []

    def absorb(row):
        fp = fingerprint(row["t"])
        cu = canon_url(row["u"])
        if fp in seen_fp or cu in seen_url:
            return False
        seen_fp.add(fp)
        seen_url.add(cu)
        items.append(row)
        return True

    stats, ok_count, refused = [], 0, 0
    for src, raw in results:
        stat = {"name": src["name"], "lang": src["lang"], "standing": src["standing"],
                "region": src["standing"], "kept": 0, "refused": 0, "ok": False}
        if raw:
            stat["ok"] = True
            ok_count += 1
            for row in parse_feed(raw, src):
                text = (row["t"] + " " + row["s"]).lower()
                if hit(text, BLOCK_C):
                    stat["refused"] += 1
                    refused += 1
                    continue
                if not hit(text, ANCHOR_C):
                    continue
                total, reasons = significance(text, src["standing"])
                row["x"] = topics_for(text) or ["science"]
                row["w"] = regions_for(text)
                row["p"] = total
                row["y"] = reasons
                row["st"] = src["standing"]
                if absorb(row):
                    stat["kept"] += 1
        stats.append(stat)
        print("  %-34s %s" % (src["name"][:34],
                              "unreachable" if not raw
                              else "%d kept, %d refused" % (stat["kept"], stat["refused"])))

    fresh_urls = {canon_url(i["u"]) for i in items}
    for row in previous:
        if "x" in row:
            absorb(row)

    cutoff = int(time.time() * 1000) - RETAIN_DAYS * 86400000
    items = [i for i in items if (i.get("d") or cutoff + 1) >= cutoff]
    items.sort(key=lambda i: i.get("d") or 0, reverse=True)
    items = items[:MAX_ITEMS]
    fresh = sum(1 for i in items if canon_url(i["u"]) in fresh_urls)

    languages = {}
    for loc in cfg.get("gnews", []):
        languages.setdefault(loc["lang"], re.sub(r"\s*\(.*\)$", "", loc["label"]))
    languages.setdefault("en", "English")

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {"stories": len(items), "new_this_run": fresh,
                   "languages": len({i["g"] for i in items}),
                   "notable": sum(1 for i in items if i.get("p", 0) >= NOTABLE_SCORE),
                   "refused": refused,
                   "wires_ok": ok_count, "wires_total": len(sources)},
        "notable_score": NOTABLE_SCORE,
        "languages": languages,
        "standings": [
            {"id": "official", "label": "Official"},
            {"id": "science", "label": "Science"},
            {"id": "specialist", "label": "Specialist"},
            {"id": "press", "label": "Press"},
        ],
        "topics": [{"id": tid, "label": label} for tid, label, _ in TOPICS],
        "geo": ([{"id": gid, "label": label} for gid, label, _ in GEO] +
                [{"id": "unlocated", "label": "No single region"}]),
        "sources": stats,
        "items": items,
    }

    print("\n%d stories (%d new, %d notable) · %d refused · %d languages · %d/%d wires answered"
          % (len(items), fresh, payload["counts"]["notable"], refused,
             payload["counts"]["languages"], ok_count, len(sources)))

    if dry_run:
        print("\n--dry-run: wire_neo.json not written")
        return payload

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("Wrote %s (%.0f KB)" % (OUT_PATH, os.path.getsize(OUT_PATH) / 1024))
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures")
    args = ap.parse_args()
    run(dry_run=args.dry_run, fixtures=args.fixtures)
