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
    if _CONCRETE.search(text) or re.search(
        r"\bsafe routes\b|\bpedestrian\b|\bada\b|\bbridges?\b|\bbox culverts?\b|\bbfp\b",
        text, re.IGNORECASE,
    ):
        return "concrete"
    return "general_construction"


# =====================================================================
# ALL-TRADES TAGGING
# Every bid gets a list of trades. A bid with no trades is skipped.
# The trade slugs below are what the website and alerts filter on.
# =====================================================================

TRADE_LABELS = {
    "general_construction": "General construction",
    "concrete": "Concrete",
    "paving": "Asphalt & paving",
    "earthwork_utilities": "Earthwork & underground utilities",
    "electrical": "Electrical",
    "hvac_mechanical": "HVAC & mechanical",
    "plumbing": "Plumbing",
    "roofing": "Roofing",
    "painting": "Painting & coatings",
    "striping_signage": "Striping & signage",
    "landscaping": "Landscaping & irrigation",
    "janitorial": "Janitorial & cleaning",
    "fencing": "Fencing & barriers",
    "demolition": "Demolition & abatement",
    "windows_doors": "Windows, doors & glazing",
    "flooring": "Flooring",
}

TRADE_PATTERNS = {
    "concrete": CONCRETE_PATTERNS,
    "paving": [
        r"asphalt", r"paving", r"pavement(?! markings?)", r"chip seal", r"slurry seal",
        r"seal ?coat(?:ing)?", r"crack seal(?:ing)?", r"overlay", r"mill(?:ing)? and (?:fill|overlay)",
        r"resurfacing", r"pavement preservation", r"surface treatment",
    ],
    "earthwork_utilities": [
        r"excavation", r"grading", r"earthwork", r"water ?lines?", r"sewer", r"storm ?drain",
        r"pipe(?:line)?s?", r"service lines?", r"culinary", r"wells?", r"waterline", r"utility",
        r"utilities", r"pump station", r"lift station", r"wwps", r"reservoir", r"tank",
        r"canal", r"detention basin", r"trenching", r"water treatment", r"wastewater",
        r"water reclamation",
    ],
    "electrical": [
        r"electrical", r"lighting", r"transformer", r"generators?", r"solar", r"fiber(?: optics?)?",
        r"ev charg(?:ing|ers?)", r"traffic signals?", r"presignals?", r"switchgear", r"panels?",
        r"street ?lights?", r"streetlight system",
    ],
    "hvac_mechanical": [
        r"hvac", r"mechanical", r"boilers?", r"chillers?", r"ac replacement", r"air conditioning",
        r"heating", r"ductless", r"rtus?", r"rooftop units?", r"cooling towers?", r"heat pumps?",
        r"ventilation", r"bms", r"controls", r"walk[- ]in (?:freezer|cooler|refrigerator)s?",
        r"refrigerat(?:ion|or)s?",
    ],
    "plumbing": [r"plumbing", r"water heaters?", r"backflow", r"fixtures?", r"domestic water"],
    "roofing": [r"roof(?:ing|s)?", r"reroof(?:ing)?"],
    "painting": [r"painting", r"paint(?! marking)", r"coatings?", r"epoxy"],
    "striping_signage": [
        r"pavement markings?", r"striping", r"signage", r"signs?", r"signing", r"traffic paint",
        r"wwd", r"wrong[- ]way",
    ],
    "landscaping": [
        r"landscap(?:e|ing)", r"irrigation", r"trees?", r"mowing", r"turf", r"sod", r"weed",
        r"snow removal", r"grounds maintenance", r"seeding",
    ],
    "janitorial": [r"janitorial", r"custodial", r"cleaning"],
    "fencing": [r"fenc(?:e|es|ing)", r"guardrail", r"cable barrier", r"barriers?", r"gates?"],
    "demolition": [r"demolition", r"demo", r"abatement", r"asbestos"],
    "windows_doors": [r"windows?", r"doors?", r"glazing", r"storefront", r"curtain wall"],
    "flooring": [r"flooring", r"carpet", r"tile"],
    "general_construction": GENERAL_PATTERNS + [
        r"construction", r"improvements?", r"repairs?", r"replacement", r"upgrades?",
        r"addition", r"expansion", r"facility", r"building", r"tenant improvement",
        r"\bjoc\b", r"job order contract(?:ing)?", r"on-call construction", r"stage ii",
        r"standard low bid", r"invitational", r"prequalified",
    ],
}

# Not trade work: professional services, purchases, software, and so on
NON_TRADE_PATTERNS = [
    r"master plan", r"planning", r"engineering services?", r"design services?",
    r"design(?!-build| build)", r"architect(?:ural|ure)?", r"study", r"consult(?:ing|ant)",
    r"suppl(?:y|ies)", r"purchase of", r"\bmix\b", r"salt", r"software", r"legal",
    r"request for information", r"\brfi\b", r"insurance", r"audit", r"food service",
    r"meals?", r"nutrition", r"staffing", r"medical", r"physicians?", r"shelter",
    r"public art", r"electrolyte", r"powder", r"vehicles?", r"trucks?", r"uniforms?",
    r"printing", r"banking", r"actuarial", r"special inspection", r"inspection services?",
    r"testing services?", r"technology upgrades?", r"\bpcs?\b", r"exercise equipment",
    r"master developer", r"for-sale homes", r"inventory order", r"\border\b",
]

_TRADE_RES = {slug: _compile(pats) for slug, pats in TRADE_PATTERNS.items()}
_NON_TRADE = _compile(NON_TRADE_PATTERNS)
_ROAD_JOB = re.compile(r"\b(?:sr|us|i)-\d+|\bmp\s?\d|\binterchange\b|\bhighway\b|\bhwy\b", re.IGNORECASE)


def classify_trades(title: str, road_project: bool = False):
    """Return a sorted list of trade slugs for a bid title. Empty list means skip it."""
    text = title or ""
    if _NON_TRADE.search(text):
        return []
    trades = {slug for slug, rx in _TRADE_RES.items() if rx.search(text)}
    if road_project:
        if re.search(r"\bsignals?\b", text, re.IGNORECASE):
            trades.add("electrical")
        if re.search(r"\bbfp\b|\bbridges?\b|\bbox culverts?\b", text, re.IGNORECASE):
            trades.add("concrete")
        if re.search(r"\bsafe routes\b|\bpedestrian\b|\bpathway\b|\bada\b", text, re.IGNORECASE):
            trades.add("concrete")
        # A plain highway segment ("US-89; 3000 South to SR-91") is a paving and civil job
        if not trades or (_ROAD_JOB.search(text) and trades <= {"general_construction"}):
            trades |= {"paving", "earthwork_utilities", "general_construction"}
    # A specialty job alone shouldn't also be tagged general construction
    # just because it says "replacement" or "repairs"
    specialty = trades - {"general_construction"}
    if specialty and "general_construction" in trades and not _GENERAL.search(text):
        trades.discard("general_construction")
    # Road and bridge jobs go to a prime contractor, so they stay general
    # construction too, unless they are purely a specialty trade
    if road_project and trades & {"paving", "earthwork_utilities", "concrete"}:
        trades.add("general_construction")
    return sorted(trades)


def legacy_category(trades):
    """Keeps the original Leadmagnet site working: 'concrete' or 'general_construction'."""
    return "concrete" if "concrete" in trades else "general_construction"
