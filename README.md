# dogwalk

**A Walk With Your Dog** — turn your dog's GPS walk data into a cozy pixel-art
exploration game. Your home area is divided into a grid of "screens"; every
GPS point from a walk lights up the cell it falls in, occasionally turning up
a scattered treasure along the way.

## Play it

Open `game.html` in a browser. No build step, no server. It ships with six of
Guinness's real walks around Giffnock, picked to span the things that make one
walk look different from another: a dawn outing at 05:55 and an evening one at
17:34, four and a half kilometres down to a five-hundred-metre pootle round the
block, and weather from clear to properly wet.

Open "Load data" to set your dog's name and load your own walks, either by
pasting JSON or by picking an exported `.json` file. What you load is kept in
`localStorage`, so a reload brings your own walks back rather than the samples;
"Reset to samples" forgets it again. Each walk in the walks panel plays back
with the ▶ button.

Keyboard: space plays and pauses, left/right scrub, up/down change speed,
`+`/`-` zoom, Escape stops. While roaming the arrow keys walk you about instead
— see below.

Expected data shape — an array of GPS points:

```json
[{"lat": 55.792, "lng": -4.293, "t": "2026-08-10T07:12:00Z"}, ...]
```

### Finding the walks in a tracker feed

A GPS collar reports all day, including while the dog is asleep on the couch,
so there are no gaps to split on — on a real month of Tractive data the fixes
arrive every ~150 seconds around the clock, and 44% of them are the dog at
home. What separates a walk from the rest of the day is leaving the house, so
the game:

- finds home as the ~40m cell the dog spends the most *time* in (dwell, not fix
  count, so a tracker that reports more often when moving doesn't put "home" on
  the busiest street);
- takes each contiguous run spent more than 75m from home, allowing a brief dip
  back inside so one walk doesn't become two;
- breaks a run where the collar goes quiet for over 30 minutes, or where the dog
  covered ground faster than 15 km/h — being driven to the park is part of the
  day, but drawing it as a straight line through everyone's gardens is not;
- drops anything under 5 minutes or 250m.

On a month of real data that turns 2,074 raw fixes into 62 walks averaging
2 km and 43 minutes. Summing every raw fix instead gives 237 km, most of it
GPS jitter and car journeys.

### Following the streets

A collar reports every couple of minutes, so consecutive fixes sit a hundred
metres apart and the straight line between them cuts through gardens, houses
and whole blocks. The dog did not walk through those.

So the walkable ways — footpaths, back lanes, residential streets, everything
short of a motorway — are built into a graph, long runs split every ~14m so a
fix can snap close to the line, and the trace is routed along it with Dijkstra.
Footpaths are weighted cheaper than an A-road, so a route through a park beats
the same distance along a main road. A route more than three times the
straight-line distance is rejected as wrong rather than drawn.

Routing runs anchor to anchor, not fix to fix. A collar in a built-up area
throws the odd fix a long way off — 15 of the 74 in the bundled walks are more
than 45m from any way, one of them 166m — and treating each of those as a
failure gave up on the two segments either side of it. Skipping the bad fix and
routing across it puts the line close to where it should have been anyway.

Which highway types get requested matters more than it looks: this corner of
Glasgow has as many `service` ways as residential streets, plus footways,
steps and cycleways. An earlier version routed over those types but never
asked Overpass for them, so every back lane the dog cut down stranded a fix.
Motorways are fetched so they get drawn, but are absent from the walkable set,
so no route ever uses one.

Each original fix keeps its timestamp; the time between two fixes is spread
along the routed length.

Routed stretches are then stepped sideways onto the pavement rather than left
down the middle of the carriageway. Which pavement is read off the GPS itself —
a fix usually sits to one side of the centreline — and the side is held until
the data clearly says otherwise, so noise cannot make the trail zigzag across
the road. Where the fix proves nothing, the British default applies: walk on
the right, facing the oncoming traffic.

Routing makes walks measurably longer — the bundled Jul 25 walk goes from
4.4 km to 5.0 km — because straight lines between sparse fixes always cut the
corners. The walk list shows the routed figure once the map for that area has
loaded.

Playback is paced off the walk's own clock, at a sixth of real time, capped at
twelve minutes. Pacing it by point count (as an earlier version did) played a
37-minute walk in two, because a real walk is barely a dozen fixes.

### Exploring

The map opens zoomed all the way out, showing the whole area at once; that is
also the limit for zooming out, so you can never pull back into empty space.
Zoom with the buttons, the wheel, or a pinch. Picking a walk waits for its map
to arrive before the dog sets off — no starting him across bare ground — and
zooms in to follow him.

Loading reports a real percentage across three phases: art assets, the map
fetch (streamed, so the figure tracks bytes actually received) and the world
build. The build runs in batches with a paint between them — it used to be one
frozen second and a half, which is no use to a progress bar and no fun to sit
through. Progress only ever moves forward, since the three phases report
independently and do not finish in a fixed order.

Dragging takes the camera off the dog so you can look around mid-walk; the ⌖
button hands him back. Zooming does the same.

Ground the dog has not covered is shaded rather than blacked out. The walk you
are watching starts dark and lifts as he actually reaches it, so "explored"
moves while you watch; everything covered on the other walks stays lit, because
he really has been there.

The trail he leaves is coloured by how fast he was going at that moment —
dark where he was dawdling, bright where he was striding — so a walk's shape
tells you something once it is drawn, rather than being one flat line. The key
sits under the transport bar.

Where he stopped is marked. A stop is a real one: a cluster of consecutive
fixes within 40m of each other lasting four minutes or more. That is a lamp
post worth a proper investigation, a chat with somebody, or a sit down — not a
treasure scattered at random, which is what an earlier version did and which
meant nothing.

A world is kept once built. Nearby walks snap to the same 200m grid of bounds,
so playing a second walk around the same streets re-lights the fog and redraws
the trail rather than rebuilding the whole town, and picking up a walk you have
already watched is close to instant.

### Roaming

"Roam" hands you the lead. You pick who is walking — her or him — and then you
walk the real streets yourself, with the dog following you rather than the other
way round.

Before you set off you say who your dog is: breed, colour, collar and name.
There are five builds and six coats, drawn from the same lists the spritesheet
was generated from so the buttons cannot drift out of step with the art. The
collar is a separate tintable sprite laid over him rather than baked into the
sheet — baking it would mean every breed times every coat times every collar,
seven hundred odd frames to offer six colours; one overlay per breed costs five.
The choice is kept in `localStorage`, and the dog trotting on the loading screen
is yours.

`WASD` or the arrow keys, shift to jog, space to grip the lead, `E` to let him
off it, Escape to finish. On a phone a drag is a
thumbstick that anchors wherever you put your thumb down, rather than panning
the camera — panning away from yourself mid-walk is not something you want, and
a stick parked in a corner is not something you can reach.

You start on the pavement outside his home, not in the middle of the road and
not in the back garden: his home fix usually falls inside the footprint of his
own house, and the nearest tile that merely is not a wall tends to be a garden
hemmed in on three sides. Buildings and water are solid, and walking into a wall
at an angle slides you along it. So is the street furniture: parked cars are
four metres of car and you weave between them, and you stop at lamp posts, bins,
postboxes and benches rather than strolling through them. The dog is not stopped
by the small ones, since those are the very things he is trying to get his nose
into. Hedges and garden walls are not solid either, because they are a tile
thick and blocking them would turn every front garden into a maze.

Movement is timed off the wall clock rather than off Phaser's frame delta.
Phaser smooths and clamps its delta towards a nominal 60fps: on a slow renderer
it reported 16.7ms for every one of 29 frames that between them spanned two and
a half seconds, so anything scaled by it ran at a fifth of the speed it was set
to. Each frame is then split into steps of at most 25ms, because one long step
at a jog can jump clean through a wall a metre thick.

The dog follows on breadcrumbs rather than on a straight line to you — he walks
where you walked, which keeps him out of the hedges and takes him round corners
instead of through them. He trails about three and a half metres back, hurries
when he has dropped behind and ambles when he is close, and when you stand still
for a couple of seconds he goes off and has a sniff at something nearby.

#### The lead

The game is the lead. A real dog walk is a negotiation between two wills: he
wants to go left and inspect something appalling, you want to get on. The lead
is where those two meet, so it is drawn between you and it is the only readout
that matters — sagging and leather-brown when he is getting his way, straight
and red when he is not.

He wants things, and the things are real: the lamp posts, bins, postboxes and
benches are the street furniture where it actually went, the trees are the
trees, and the named shops and churches are straight off the OSM tags. Walk him
near one and he makes for it. Let him have it and he gets a proper go at it and
the walk gets better. Keep walking and the lead comes taut — he is held on the
arc at full stretch, straining in the right direction — and after about three
seconds he gives it up, which costs you both a little.

Those wants are thinned to one every eight metres or so, named places first.
Every tree in the town is about nineteen thousand of them, which means he wants
something at every single step, the lead is permanently taut, and the choice you
are supposed to be making stops being a choice. Thinned out, a walk gets a
rhythm: a stretch of plain walking, then something worth stopping for.

**Squirrels.** Every half-minute or so one bolts past. He is after it instantly
and you have about a second to grip the lead — hold space, or the button that
appears. Grip it and he strains but stays. Miss it and he slips the lead and is
gone, and you have to call him back before you can clip him on again. Either way
he loved it; missing it is funnier, not worse.

**Other dogs.** Other people are out walking theirs. They spawn on the real
pavements within about ninety metres of you, amble along them — straight on
where that works, otherwise whichever turn does, which is enough to send them
round corners and along the fronts of the houses without a graph to follow —
and are recycled when you leave them behind. Each has a randomly chosen owner,
breed, coat, collar, name and temperament.

Bring your dog close to theirs and the two of them stop for a sniff while the
owner says something. The lead still applies: the greeting only counts while
they are actually together and you are not hauling him off, so marching straight
past means he does not get to say hello. Nervous dogs are a real thing, so a shy
one's owner gives you a wide berth and says why, and their dog is never made to
greet yours.

**Off the lead.** Only in a park, which is the actual rule. Off it he ranges
much further, goes for things he could never reach on the lead, and earns by the
ground he covers rather than by the clock — paying by the second would mean
standing still in a park scored as well as a good run about. Wander off the park
with him still loose and the bar tells you to clip him back on.

**Afterwards** you get a verdict, rated on how good a walk it was for *him* —
joy per hundred metres, not distance, because the question is not how far you
went but whether he got anything out of it. The top verdicts carry a minimum
distance of their own: one squirrel forty metres from the front door is not the
best day of his life, however well it scores per metre.

The bar reads out how far you have walked and where you are, off the same OSM
names the map is lettered with — "outside the library" if you are next to a
named place, the street otherwise. Ground you cover yourself counts as explored
exactly like ground he covered on a real walk, because you were both actually
there; it is kept in lat/lng so it survives the world being rebuilt around a
different walk, and saved to `localStorage` so an afternoon of exploring is
still there tomorrow.

The light follows your own clock, since this walk is happening now rather than
in the archive.

### Shops, houses and the rest

Buildings are classified off their own OSM tags rather than all drawn the same.
A shop, a café or a bank gets a felted flat roof with a parapet and a proper
shopfront onto the pavement — painted fascia, plate window, stallriser, recessed
door — so the parade on Fenwick Road reads as a parade and not as forty
identical bungalows. Schools, churches and halls take slate; blocks of flats
take the flat roof; everything else is a house, with its own pitched roof colour
and a front elevation of windows, bays and the occasional door. The procedural
fallback, for corners of the map OSM has barely touched, still puts up houses,
because a Glasgow suburb overwhelmingly is houses.

Ground touching a shop or a civic building is paved right up to the wall.
Nobody keeps a privet hedge outside a chip shop, and a school forecourt is not
a front garden.

Named shops, schools and churches are lettered on the map alongside the street
and park names, fading in as you zoom down to walking scale — at map scale they
are clutter, and at street scale they are the point of the street.

Nothing is allowed to print through anything else. Names go down in order of
importance — the park, then the named places, then the streets — and any label
whose box would land on one already placed is dropped rather than drawn over it.
Street names repeat every 70m along their road, so losing one repeat costs
nothing; two names on top of each other costs both of them.

### Stats

The "Stats" button opens a read-out of everything loaded: total distance as a
hero figure, a row of tiles (time on his paws, average and longest walk, per-day
average, pace), and three charts — distance per day across the whole span with
the empty days left in, walks by hour of day, and distance by weekday.

They are single-series column charts, because bar length already carries the
magnitude and a second colour would encode nothing; the one hue is the HUD's own
amber, which clears 3:1 against the panel. Bars cap at 24px with a 2px gap in
the surface colour rather than a stroke, gridlines are hairline and recessive,
and only the peak of each chart is labelled — a number on every bar goes unread.
Every bar has a hover read-out, and "Show the numbers" gives the same data as a
table so nothing is gated behind colour or a pointer.

On a full month of real data that says: 62 walks, 123 km, 44 hours on his paws,
7am is far and away his walk time, and Saturday is the biggest day.

The walk list can be ordered by most recent, longest, or wettest — sixty-odd
walks is a long scroll with nothing to steer by otherwise.

### Weather

The rain used to be a coin flip off the walk's date, which sits badly in a
project that otherwise refuses to invent anything.
[Open-Meteo's archive](https://open-meteo.com/) gives hourly conditions for a
past date, free and without a key, so the weather on screen is the weather he
actually walked in: rainfall drives the shower, cloud cover flattens the light
(a hundred per cent overcast in Glasgow is not golden hour at any hour), and the
temperature and conditions are reported next to the clock. Past weather never
changes, so each day is cached permanently once fetched. If the lookup fails,
the walk plays dry and says nothing about the weather.

All four readings are animated, not just the rainfall:

- **Rain** falls harder and denser with the hourly total, and splashes where it
  lands. The wind tips it over — the streaks lie along the direction of travel,
  so a gale drives the rain sideways rather than leaving it falling straight
  down through a storm.
- **Snow** replaces it below about 1.5°C: the same millimetre of precipitation,
  but slow, wandering flakes that do not streak and do not splash.
- **Cloud shadows** drift across the ground under broken cloud, running with the
  wind at roughly the wind's own speed. A clear sky has nothing to cast, and
  full overcast casts one flat shadow everywhere, which the light grade already
  handles — so both are left alone. They are sized against the view rather than
  fixed in metres: a real shadow is a few hundred metres across, which at
  walking zoom is wider than the screen and stops reading as something crossing
  the ground.
- **Gusts** blow litter along the pavement once the wind is over about 25 km/h.

The hourly readings are eased between neighbouring hours rather than held flat
across each one. The archive is hourly, but a walk is watched continuously, and
a shower that switches on at the stroke of the hour and off sixty minutes later
looks like a bug.

One request covers the whole span of whatever you loaded rather than one per
walk — a single call instead of sixty-odd, kinder to a free service that does
rate-limit, and it means the walk list and the stats can show conditions without
waiting for you to press play. The list carries a glyph and a temperature per
walk, can be ordered by wettest, and the stats report how far he walked in the
rain.

## Pulling real data from Tractive

Tractive (the GPS tracker) has no official public API, so this uses the
reverse-engineered [`aiotractive`](https://github.com/zhulik/aiotractive)
client with your own Tractive account email/password. This must run
**locally** — never enter Tractive credentials into a browser page.

```bash
pip install -r scripts/requirements.txt
python scripts/export_tractive.py --days 30
```

This writes `walks.json` (override with `--output`) in the
`{lat, lng, t}` shape the game expects — load it into the page via the
"Load file…" button. See `python scripts/export_tractive.py --help` for all
options (specific tracker ID, clipboard copy, a `--raw` dump of the untouched
API response for debugging).

Because Tractive's history endpoint isn't publicly documented, the script
parses responses defensively rather than assuming one fixed schema. It has now
been run against a live account and produced a usable month of history; if a
run comes back empty, re-run with `--raw` and check `<output>.raw.json` to see
what the API actually returned.

## Status / open items

- No backend. Your walks are kept in this browser's `localStorage` only —
  nothing is uploaded, and nothing follows you to another device.
- The bundled samples are real GPS trails. A walk trail is home-address
  adjacent data; swap them out before publishing your own copy if that matters
  to you.
- The Tractive export script's response parsing is best-effort. It works on a
  real account, but the endpoint is undocumented and could change shape.
- The world is built around one walk at a time. A single day trip whose route
  spans several kilometres still has to be drawn coarsely, because the tile
  count is capped; local walks, which are the overwhelming majority, come out
  at about two metres per tile.
- Multi-user "connect your own Tractive account" support (rather than a
  local per-person export script) is a bigger step — it needs a backend,
  since Tractive only supports raw email/password auth (no OAuth), which
  changes how account credentials would need to be handled.

## Art

The tileset is generated, not hand-pixelled by mouse — `scripts/make_tiles.py`
draws every tile and prop from one palette and writes `assets/city.png`,
`assets/props.png` and `assets/city-index.json` (the index map `game.html`
reads its tile numbers from).

```bash
pip install pillow
python3 scripts/make_tiles.py
```

The palette gives each material five values, hue-shifted so shadows drift blue
and highlights drift warm, and texture comes from ordered dithering between
neighbouring steps rather than random noise — which is what keeps a small
palette from going muddy when tiles repeat across a whole town. Light is fixed
to the north-west everywhere, so every cast shadow falls south-east.

Roofs come in five: four pitched — slate, charcoal, pantile, weathered — laid as
staggered courses with vertical joints (drawn flat they read as horizontal
siding), and one felted flat roof for shops and flats. The flat one is drawn as
its own thing rather than slates in grey, because that is the whole point of it:
a bitumen membrane in overlapping strips, ponding where it never lies flat, the
odd rooflight, and a coping-stone parapet where it ends.

The squirrel is mostly tail, because from directly above that is the whole
silhouette — nobody reads the body of a squirrel, everybody reads the tail.

Dogs are five builds and six coats, and what tells two breeds apart at sixteen
pixels is the proportion between body, head and tail plus what the ears are
doing. There are four more walkers beyond the two you can play as, for the
people you meet: two figures repeated down a street reads as a bug rather than
as a neighbourhood.

The two walkers are drawn facing east and rotated to their heading at runtime,
exactly as the dog is — one four-frame cycle covers every direction, and it
keeps the pair of them looking like they belong in the same world. From directly
above, a person is hair, shoulders, a coat and the tips of two shoes, so that is
what has to carry it: the arms swing outside the coat because they are the only
moving parts you can see, and the shoulders are wide across and narrow front to
back. An earlier version had that the other way round and drew a small car.

Ground tiles are 16px; props (parked cars, lamp posts, bins, pillar boxes,
benches) are authored on 32px frames — two tiles across — so a car comes out
about 4m long at native scale with its pixels still landing exactly on the
ground's pixel grid. Nothing is drawn at a fractional scale, because that
would put sprite pixels off the tile grid and break the pixel-art look.

An earlier build of this used Kenney's
[Tiny Town](https://kenney.nl/assets/tiny-town) pack (CC0); it has been removed
now that everything is generated, but the licence text stays in
`ASSETS-LICENSE.txt` for the record.

## Tests

```bash
npm install -D playwright && npx playwright install chromium
node tests/smoke.js
```

Set `CHROMIUM_PATH` if you already have a browser and would rather not download
another (`CHROMIUM_PATH=/opt/pw-browsers/chromium node tests/smoke.js`).

Serves the repo, stubs Overpass with a small hand-written fixture so the run is
offline, and checks the things that have actually broken before: the splash
never clearing, walks not being found in a feed, playback not starting, the HUD
panels trapping you on a phone, and silent page errors. It found two real faults
the first time it ran.

The fixture is a small invented town rather than a single street, because some
of what is being checked only happens at scale: real OSM footprints are only
trusted over the procedural frontage once there are more than a dozen of them,
so a two-building fixture never exercised the path that draws shops as shops.
Weather is checked by injecting hourly rows directly and asking the scene what
it drew, since the archive lookup is offline in a test run.
