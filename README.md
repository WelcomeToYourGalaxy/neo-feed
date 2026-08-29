# neo-feed

Near-Earth objects, worldwide, in 25 languages: what is coming close, what has come down, what the
projections say, and who is building the means to deflect it.

`harvest_neo.py` runs every two hours in GitHub Actions, reads 56 wires, refuses the figurative and
the fictional, grades what remains by standing and significance, tags it by subject and region, and
writes `wire_neo.json`. `index.html` loads that file and renders it.

Nothing here rewrites a headline. Titles and snippets are the publishers' own, truncated but never
reworded, and every row keeps its original link. No model in the pipeline, no API key, no paid
service, no dependencies beyond the Python standard library.

## Scope

Asteroids, comets and meteoroids on Earth-crossing paths; the events they produce; the projections
made about them; and the surveys, missions, instruments and companies whose work is finding and
deflecting them.

This is **not** a space-industry feed. Launches, constellations, crewed flight and orbital politics
belong to a different wire; they appear here only when an asteroid or comet is the subject. A NEO
Surveyor contract belongs here. A Starlink launch does not.

## Two judgements, kept separate

**Standing — who is speaking.**

| Standing | What it covers |
|---|---|
| Official | NASA, JPL, ESA Space Safety, the meteor observation networks |
| Science | Journals and preprints |
| Specialist | The observing and planetary press |
| Press | General news across 25 languages |

**Significance — what the story carries.**

| Signal | Worth |
|---|---|
| A confirmed event: airburst, recovered meteorite, atmospheric entry | 2 |
| An official risk listing: Sentry, ESA risk list, Torino or Palermo rating, MPC designation | 2 |
| A measurement: size, distance in lunar distances, velocity | 1 |
| A forward projection | 1 |
| A named object designation, matched by pattern (2024 YR4, 67P, C/2023) | 1 |
| Primary source | 1 |

At **3** or more a row is marked notable, and the *Weight* filter narrows to those. The pips show
the score and the words beside them say what earned it.

## What is refused

The figurative meteor and the fictional asteroid: meteoric rises, *Asteroid City*, *Armageddon*,
box-office returns. Astrology, which uses this vocabulary constantly — Chiron, retrogrades, birth
charts. And doom-mongering with no object behind it: Planet X, Nibiru, end-of-the-world prophecy.
The status line reports how many stories each harvest refused.

## Files

| File | Path in repo | What it is |
|---|---|---|
| `index.html` | `/index.html` | The feed page. Pages serves the repo root, so it must carry this name. |
| `harvest_neo.py` | `/harvest_neo.py` | The harvester. Self-contained. |
| `sources_neo.json` | `/sources_neo.json` | The wire list, with each wire's standing. |
| `wire_neo.json` | `/wire_neo.json` | The output the page reads. Empty placeholder until the first run. Never hand-edit. |
| `neo-feed-weebly-embed.html` | `/neo-feed-weebly-embed.html` | The page wrapped for a Weebly Embed Code element. Regenerate after changing `index.html`. |
| `README.md` | `/README.md` | This file. |
| `harvest.yml` | `/.github/workflows/harvest.yml` | Runs every two hours at :07 and commits the wire. |

## Setup

1. Push these files to the repository root.
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**, save.
3. Actions tab → **Harvest the NEO wire** → *Run workflow*.
4. Settings → Pages → **Deploy from a branch**, branch `main`, folder `/ (root)`.
5. Confirm `https://raw.githubusercontent.com/WelcomeToYourGalaxy/neo-feed/main/wire_neo.json`
   loads in a browser.

If the repository is named something other than `neo-feed`, change `REPO` near the top of the feed
script in `index.html` and regenerate the embed.

## Sources

**Official** — NASA, NASA JPL, ESA Space Safety, ESA Space Science, the American Meteor Society,
the International Meteor Organization.

**Science** — two arXiv queries (near-Earth objects and impact hazard; asteroid deflection,
meteoroids, bolides, interstellar objects), Nature news, ScienceDaily asteroids and comets,
Phys.org astronomy.

**Specialist** — Sky & Telescope, EarthSky, Universe Today, The Planetary Society.

**Press** — Space.com, SpaceNews, and Google News editions in English (US, UK, India, Australia,
Canada, South Africa), Spanish (Spain, Mexico), Portuguese, French, German, Italian, Dutch,
Swedish, Greek, Polish, Russian, Ukrainian, Turkish, Arabic, Hebrew, Persian, Hindi, Bengali,
Indonesian, Vietnamese, Thai, Japanese, Chinese (simplified and traditional), Korean.

**Event searches** — eight aimed at happenings rather than commentary: close approaches, impacts
and airbursts, risk list and projections, deflection and missions, surveys and discovery, sample
return and science, policy and coordination, interstellar visitors.

## Filters

Subject (ten: discovery, close approaches, impacts, risk, deflection, missions, science, industry,
policy, interstellar), Region (by the ground the story concerns), Standing, Weight, Language, and
Window — 24 hours, 7 days, 30 days, older than 30 days, or everything from the 45-day archive.

## Running it locally

```bash
python3 harvest_neo.py              # full run
python3 harvest_neo.py --dry-run    # harvest and report, write nothing
python3 harvest_neo.py --fixtures tests/
```

Python 3.9 or later.
