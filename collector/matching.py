"""
Decides whether a bid is relevant to concrete contractors.

Every bid gets one of three answers:
  "concrete"              Direct concrete work (sidewalks, slabs, skate parks, canal lining...)
  "general_construction"  Building/site jobs where the general contractor will hire a concrete sub
  None                    Not relevant, so skip it

All words are matched as WHOLE words, so "cement" no longer matches
inside "replacement" or "placement".

To tune: add or remove patterns in the lists below.
"""

import re

# Direct concrete work
CONCRETE_PATTERNS = [
    r"concrete", r"cement(?:itious)?", r"flatwork", r"sidewalks?", r"curbs?",
    r"curb and gutter", r"slabs?", r"footings?", r"foundations?",
    r"retaining walls?", r"(?:ada|pedestrian|curb) ramps?", r"driveways?",
    r"shotcrete", r"gunite", r"rebar", r"precast", r"skate ?parks?",
    r"canal lining", r"culverts?", r"bridge decks?", r"paving", r"pavement",
    r"parking lots?", r"plazas?", r"hardscape", r"walkways?", r"pathways?",
    r"trails?",
]

# General construction where a concrete sub is usually needed
GENERAL_PATTERNS = [
    r"standard low bid", r"general stage", r"design-build", r"design build",
    r"construction manager", r"cm/gc", r"cmgc", r"general contractor",
    r"new .{0,40}building", r"remodel", r"renovation", r"playgrounds?",
    r"restrooms?", r"dugouts?", r"observatory", r"shade structure",
    r"construction:", r"site improvements?", r"park improvements?",
    r"street improvements?", r"road improvements?", r"reconstruction",
    r"vbs construction", r"accessibility", r"infrastructure improvements?",
]

# Specialty trades: drop these from the general construction group
OTHER_TRADE_PATTERNS = [
    r"mechanical", r"electrical", r"elevator", r"hvac", r"ac replacement",
    r"roof(?:ing)?", r"glazing", r"mep", r"bms", r"plumbing", r"fire alarm",
    r"controls", r"signage", r"fenc(?:e|ing)", r"irrigation", r"water heater",
    r"ductless", r"transformer", r"window", r"siding", r"flooring", r"carpet",
]

# Never relevant: planning, design, supplies, services
EXCLUDE_PATTERNS = [
    r"master plan", r"planning", r"engineering", r"design(?!-build| build)",
    r"study", r"inspections?", r"consult(?:ing|ant)", r"marking", r"paint",
    r"maintenance", r"suppl(?:y|ies)", r"mix", r"asphalt concrete",
    r"salt", r"sweeper", r"legal", r"software", r"request for information",
    r"\brfi\b", r"courtesy posting", r"architect(?:ural|ure)?",
    r"public art",
]


def _compile(patterns):
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE)


_CONCRETE = _compile(CONCRETE_PATTERNS)
_GENERAL = _compile(GENERAL_PATTERNS)
_OTHER_TRADE = _compile(OTHER_TRADE_PATTERNS)
_EXCLUDE = _compile(EXCLUDE_PATTERNS)


def classify(title: str):
    """Return 'concrete', 'general_construction', or None for a bid title."""
    text = title or ""
    if _EXCLUDE.search(text):
        return None
    if _CONCRETE.search(text):
        return "concrete"
    if _GENERAL.search(text) and not _OTHER_TRADE.search(text):
        return "general_construction"
    return None


# ---------------------------------------------------------------------
# Road projects (UDOT): almost every road job needs concrete work
# (curb, gutter, barrier, ramps, sidewalk), so they count as general
# construction unless they are clearly signals, signs, or striping.
# ---------------------------------------------------------------------
ROAD_OTHER_TRADE_PATTERNS = [
    r"signals?", r"presignals?", r"queue cutters?", r"signing", r"signs?",
    r"striping", r"pavement markings?", r"lighting", r"fiber(?: optics?)?",
    r"its", r"cameras?", r"wwd", r"wrong[- ]way", r"rumble strips?",
    r"guardrail", r"cable barrier", r"seeding", r"weed", r"mowing",
]

_ROAD_OTHER_TRADE = _compile(ROAD_OTHER_TRADE_PATTERNS)


def classify_road_project(title: str):
    """Return 'concrete', 'general_construction', or None for a road project title."""
    text = title or ""
    if _EXCLUDE.search(text) or _ROAD_OTHER_TRADE.search(text):
        return None
    if _CONCRETE.search(text) or re.search(r"\bsafe routes\b|\bpedestrian\b|\bada\b", text, re.IGNORECASE):
        return "concrete"
    return "general_construction"
