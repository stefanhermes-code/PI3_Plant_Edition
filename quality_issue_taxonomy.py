"""Controlled vocabulary for the Quality Issue page's "Issue type" field.

Added 2026-08-01, replacing a bare free-text field (previously any string
was accepted, e.g. "hardness drift", "shrinkage", "collapse", "splitting" -
whatever the person typing happened to call it that day, with no way to
later group/count/trend issues reliably since two people describing the
same fault would use different words).

QUALITY_ISSUE_TAXONOMY below is sourced from Laader Berg's "Troubleshooting
Guide For Slabstock Foaming" (Revision 2010, Release 2014-01-01) - the
FAULT column of that guide's fault/recommendation tables becomes the
`name` here, and the RECOMMENDATION column (condensed to plain language)
becomes `typical_causes`, shown to the person logging the issue as
reference/guidance once they pick a type, not as instructions to follow
blindly - it's the same "reference for human judgement, not a directive"
posture the rest of this app uses for PI3 output.

Consolidation note: the source guide lists several splits/cracks faults
that differ mainly by WHERE on the block they appear (bottom corner,
shoulder, centre, small side, inclined, chimney over the trough inlets,
...) rather than by distinct underlying mechanism. Encoding each of those
as its own taxonomy entry would just duplicate the "Location in block"
field this page already has. Split-type entries below are consolidated by
mechanism/appearance instead; the reviewer captures the specific position
in "Location in block" and "Notes" as before.

Structure: an ordered dict of category -> list of issue-type dicts, each
with `name` (the controlled value actually stored in
QualityObservation.observation_type - unchanged column, still a plain
string, just no longer free-typed) and `typical_causes` (guidance text).
The last category, "Routine / No Issue", also carries the escape hatch
for a genuine one-off that doesn't match anything here yet ("Other").

No schema/migration needed: QualityObservation.observation_type stays a
String(200) column exactly as before - this module only changes what the
UI lets a person choose to put into it. Historical rows already in the
database (demo data, UAT data, or anything imported before this taxonomy
existed) that don't match any `name` below still display and edit fine -
see views/6_Quality_Observation.py's handling of a legacy/unmatched value.
"""

OTHER_ISSUE_NAME = "Other (not yet in this list)"

QUALITY_ISSUE_TAXONOMY = {
    "Rise & cure behavior": [
        {
            "name": "Boiling",
            "typical_causes": "Large bubbles appear and burst at the surface. Check silicone quality/output "
            "and tin catalyst quality/output; reduce amine catalyst; check for contamination with silicone "
            "or grease lubricants.",
        },
        {
            "name": "Collapse",
            "typical_causes": "Foam rises and then falls. Check silicone and tin catalyst quality/output; "
            "reduce amine catalyst; look for contaminants in the foam system.",
        },
        {
            "name": "Crazy balls",
            "typical_causes": "Small bubbles moving rapidly under the foam surface. Increase mixer speed; "
            "minimise splashing at lay-down.",
        },
        {
            "name": "Flashing / sparklers",
            "typical_causes": "Excessive effervescence on the top surface of rising foam. Decrease TDI, "
            "silicone, and amine catalyst; increase tin catalyst; check for metering errors; decrease "
            "component temperatures.",
        },
        {
            "name": "Smoking",
            "typical_causes": "Excessive TDI vapours from the surface of the foam. Check metering of TDI, "
            "polyol, and water; reduce TDI output.",
        },
        {
            "name": "Sticky spots",
            "typical_causes": "Local areas of wet, imperfectly mixed ingredients. Check for lead/lag "
            "conditions; increase mixing efficiency; check component tank level; look for system "
            "contaminants.",
        },
        {
            "name": "Creeping cream line",
            "typical_causes": "Cream line tends to move back toward the pour point. Speed up the conveyor; "
            "increase conveyor angle; lower amine catalyst level.",
        },
        {
            "name": "Undercutting / under-running",
            "typical_causes": "Liquid reactants flow under the already-rising foam mass. Speed up the "
            "conveyor; decrease conveyor angle; increase catalyst levels.",
        },
        {
            "name": "Relaxation",
            "typical_causes": "Block rises to maximum height, then settles back. Increase silicone and tin "
            "catalyst (check output); reduce amine catalyst; reduce stirrer speed and/or air injection.",
        },
        {
            "name": "Sink back",
            "typical_causes": "Excessive sink-back of the foam after cell opening. Check tin catalyst pump "
            "output and activity; if the structure is abnormally fine/open, check stirrer speed/air "
            "injection; check the tertiary amine catalyst blend.",
        },
        {
            "name": "Slow curing",
            "typical_causes": "Polymer strength builds too slowly; foam too weak/sticky to cut; block "
            "dimensionally unstable leaving the tunnel. Increase amine and/or tin catalyst; check metering "
            "of water/TDI/polyol/tin; check for catalyst deactivation; raise component temperatures; "
            "improve mixer efficiency.",
        },
        {
            "name": "Scorching",
            "typical_causes": "Discoloration and loss of properties in the foam core; high internal "
            "temperature during curing. Check TDI/water/polyol outputs; check for contaminants; reduce the "
            "block size.",
        },
        {
            "name": "Odour",
            "typical_causes": "Finished foam has an undesirable odour. Try a different amine catalyst; use "
            "less-odorous formulation additives; give the foam more time to degas.",
        },
        {
            "name": "Tacky block surface",
            "typical_causes": "Surface of the foam block remains sticky for a prolonged time. Increase "
            "total catalyst levels; check block storage conditions; see also Slow curing.",
        },
    ],
    "Cell structure & surface texture": [
        {
            "name": "Moon craters",
            "typical_causes": "Small pits or pockmarks on the block surface. Reduce trapped air at "
            "lay-down; minimize splashing at lay-down.",
        },
        {
            "name": "Coarse foam",
            "typical_causes": "Foam is composed of large cells. Check silicone level/activity; increase "
            "mixing speed and/or air injection.",
        },
        {
            "name": "Dead foam",
            "typical_causes": "Foam has low resiliency and closed cells. Reduce tin catalyst and silicone "
            "level; try running a finer cell size.",
        },
        {
            "name": "Friable / loose foam",
            "typical_causes": "Foam is crumbly and does not build polymer strength. Check metering of "
            "tin/polyol/TDI/water; decrease TDI output; check for reduced tin activity or polyol "
            "reactivity; increase mixing speed/efficiency.",
        },
        {
            "name": "Friable skin",
            "typical_causes": "Skin is soft and flakes off at the touch. Increase, change, or check amine "
            "catalyst activity; increase component temperatures; look for contaminants in the system.",
        },
        {
            "name": "Striations",
            "typical_causes": "A distinct line of unusual cell structure. Increase mixing; check the "
            "injection needle and pigment distribution; check for foam build-up/contamination; clean the "
            "mixing head; minimize splashing at lay-down.",
        },
        {
            "name": "Closed cells / low air penetration",
            "typical_causes": "Closed cells with shrinkage, with either normal or abnormally coarse foam "
            "structure. Reduce tin catalyst; check polyol/TDI temperature; check the amine catalyst blend; "
            "on mixed-isomer TDI feed systems check the blend ratio; adjust stirrer speed or mixer nozzle "
            "diameter as needed.",
        },
        {
            "name": "Voids / pinholes",
            "typical_causes": "Small voids randomly distributed throughout the foam. Increase tin catalyst "
            "and cell size; reduce air injection/mixer speed; check silicone activity; check for "
            "contamination and clean pump filters, mixer chamber, manifold, tube, and trough.",
        },
        {
            "name": "Air holes",
            "typical_causes": "Cross-section covered with smaller or larger air holes. Check for excess air "
            "in polyol or TDI; check for air leakage through the mixing head, seals, or flexible hoses; "
            "check for build-up material; check mixing head pressure and blowing agent temperature.",
        },
        {
            "name": "Excess air bubbles",
            "typical_causes": "Too much air in the mix. Increase mixer pressure; check the air injection "
            "needle/nozzle; reduce air injection flow; remove build-up material from the mixing head/hoses/"
            "trough; check for blocked filters causing under-pressure; check polyol pipe sizing/routing for "
            "trapped air.",
        },
        {
            "name": "Ruptured foam",
            "typical_causes": "Foam with low tensile strength; a line of ruptured, easily split material "
            "near the bottom or top corners. Foam is extremely open - increase the tin level.",
        },
        {
            "name": "Poor fingernail recovery",
            "typical_causes": "Foam recovers slowly when indented with a sharp object. Improve foam air "
            "flow by decreasing tin catalyst and/or silicone levels; try a finer cell size; improve curing "
            "conditions.",
        },
    ],
    "Splits & cracks": [
        {
            "name": "Splits - normal cell structure, open cells",
            "typical_causes": "Splits associated with a normal cell size and open cells. Tin catalyst too "
            "low or deactivated - check output; conveyor speed too slow - increase speed or reduce output; "
            "polyol/TDI temperature too low - check and adjust; silicone level too low - lab-test and "
            "adjust; incorrect amine catalyst blend ratio - compare against standard in the lab.",
        },
        {
            "name": "Splits - abnormal fine/broken cell structure",
            "typical_causes": "Splits associated with an abnormally fine, broken cell structure. Excessive "
            "air in the mix - check for entrained air/leaks; stirrer speed too high - reduce in stages; "
            "mixer exit nozzle too large - reduce diameter in stages.",
        },
        {
            "name": "Gross splits",
            "typical_causes": "Large vertical or horizontal separation in the block. Increase tin catalyst "
            "(check activity); decrease amine catalyst; increase silicone (check activity); decrease water "
            "level; check mechanical factors.",
        },
        {
            "name": "Zigzag (tin) splits",
            "typical_causes": "Crumbly zigzag splits throughout the block or on the sides. Increase tin "
            "catalyst concentration; check for reduced tin reactivity/output; check TDI and water output; "
            "increase silicone level.",
        },
        {
            "name": "Mechanical splits",
            "typical_causes": "Vertical splits in the side skin with a coarse appearance and rolled material "
            "visible - caused by side-paper speed/friction, not chemistry. Check the paper running speed, "
            "brake tension, and friction between paper and side wall.",
        },
        {
            "name": "Chimney splits (top skin)",
            "typical_causes": "1-2 inch wide splits over the trough inlets, about 4 inches deep. Increase "
            "silicone and tin level; check for excess air.",
        },
        {
            "name": "Footprints / build-up splits",
            "typical_causes": "Deep splits appearing progressively at ~80% of block height after 15-20 "
            "minutes; associated with higher densities, no blowing agent, high-molecular-weight polyol, or "
            "long trough dwell time. Increase total output or use a smaller trough; aim for a ~19-29 second "
            "dwell time; consider a lower molecular-weight polyol.",
        },
        {
            "name": "Trough build-up splits",
            "typical_causes": "Side splits caused by too much material build-up in the trough. Reduce "
            "formulation reactivity/amine level or change amine type; increase total output; reduce trough "
            "volume.",
        },
        {
            "name": "Clogged-flexible splits",
            "typical_causes": "Vertical splits in the cross-section, caused by build-up on the trough lip, a "
            "clogged flexible hose, dirty trough inlets, or PE film trapping gassing foam. Clear build-up/"
            "clogs; check PE film length is ~10mm longer than the trough lip.",
        },
    ],
    "Density, shape & dimensional": [
        {
            "name": "Shrinkage",
            "typical_causes": "Block shrinks during curing. Decrease tin catalyst and silicone level; "
            "increase mixer speed/air injection; check for contaminants; decrease TDI index; increase amine "
            "catalyst; lower component temperatures; enlarge the mixer outlet nozzle.",
        },
        {
            "name": "Low block density",
            "typical_causes": "Reduced block height, associated with high curing temperature/scorching or "
            "increased TDI vapour at cut-off. Check for a shortage of blowing agent (methylene chloride) or "
            "water for primary blowing; check output, temperature, and feed tank/filter/valve condition.",
        },
        {
            "name": "Bottom cavitation",
            "typical_causes": "Closed cells with the bottom of the block eaten away. Reduce tin catalyst; "
            "check for metering errors.",
        },
        {
            "name": "Bottom skin densification",
            "typical_causes": "A layer of denser foam at the bottom of the block. Increase silicone level "
            "or check for reduced activity.",
        },
        {
            "name": "Stratification",
            "typical_causes": "Irregular density throughout the block. Look for errors in component "
            "metering; check mechanical factors.",
        },
        {
            "name": "Heavy skin",
            "typical_causes": "Thick skin of high density. Increase total system catalysis and TDI content; "
            "heat the block surface.",
        },
        {
            "name": "Domed profile (Maxfoam)",
            "typical_causes": "Block profile is domed rather than rectangular. Check the full-rise position "
            "relative to the conveyor and correct the flow pattern.",
        },
        {
            "name": "Concave profile (Maxfoam)",
            "typical_causes": "Block profile is concave, often with a bottom corner split; full rise on or "
            "too close to the fall-plate. Reduce amine or fall-plate length; increase conveyor speed or "
            "total output.",
        },
        {
            "name": "Excess-flow grooves",
            "typical_causes": "Two deep grooves on top of the block from excess flow on the fall-plate. "
            "Raise the amine level; reduce the angle of the first fall-plate section; increase air "
            "injection.",
        },
        {
            "name": "Horizontal holes",
            "typical_causes": "A waving surface pattern with horizontal holes (~1 inch) just under the top "
            "skin, from too much flow on the fall-plate. Increase amine level; use a larger trough; lower "
            "the angle of the first fall-plate section.",
        },
        {
            "name": "Shoulder holes (Maxfoam)",
            "typical_causes": "Tiny (~5mm) holes on the shoulders of a Maxfoam block where the foam blows "
            "off - indicates very open cells. Informational: confirms open-cell structure; correct only if "
            "paired with another fault.",
        },
        {
            "name": "Low catalyst tolerance",
            "typical_causes": "Foam shows excessive sensitivity to small changes in tin catalyst "
            "concentration. Use a diluted catalyst for finer metering control; check metering accuracy of "
            "all components; decrease amine catalyst; use a lower-activity silicone.",
        },
    ],
    "Physical property deviation": [
        {
            "name": "Poor tensile strength / weak foam",
            "typical_causes": "Tensile values lower than normal. Check TDI/water/polyol output; reduce cell "
            "size (see Coarse foam); check for a low TDI index; check catalyst filters.",
        },
        {
            "name": "Poor elongation",
            "typical_causes": "Elongation values lower than normal. Check TDI/water/polyol output; check "
            "for a low TDI index.",
        },
        {
            "name": "High compression set",
            "typical_causes": "Compression set values higher than 10%. Decrease tin and silicone levels; "
            "keep the TDI index between 105-108; use a co-catalyst system; improve curing conditions.",
        },
        {
            "name": "Low load-bearing / hardness values",
            "typical_causes": "Formulation produces lower load-bearing (IFD/hardness) values than desired. "
            "Increase TDI index; check for errors in water/TDI/polyol output.",
        },
        {
            "name": "High load-bearing / hardness values",
            "typical_causes": "Formulation produces higher load-bearing (IFD/hardness) values than desired. "
            "Check for errors in water/TDI/polyol output; decrease TDI index.",
        },
    ],
    "Routine / no issue": [
        {
            "name": "No issue found - routine check",
            "typical_causes": "Logged for traceability on a routine batch where nothing abnormal was "
            "observed. No action needed.",
        },
    ],
    "Other / not yet classified": [
        {
            "name": OTHER_ISSUE_NAME,
            "typical_causes": "Use only when the issue genuinely doesn't match any entry above - describe "
            "it in Notes, and consider suggesting it be added to this list.",
        },
    ],
}


def categories():
    """Ordered list of category names, for the first-level selectbox."""
    return list(QUALITY_ISSUE_TAXONOMY.keys())


def issue_types_for_category(category):
    """List of issue-type dicts ({"name", "typical_causes"}) for one
    category, for the second-level selectbox. Empty list for an unknown
    category rather than raising, since this is driven by UI state."""
    return QUALITY_ISSUE_TAXONOMY.get(category, [])


_NAME_TO_ENTRY = {
    entry["name"]: {"category": category, **entry}
    for category, entries in QUALITY_ISSUE_TAXONOMY.items()
    for entry in entries
}
_NAME_TO_ENTRY_LOWER = {name.lower(): entry for name, entry in _NAME_TO_ENTRY.items()}


def lookup(name):
    """Exact-name lookup -> {"category", "name", "typical_causes"}, or None
    if `name` isn't a taxonomy entry (e.g. a legacy free-text value entered
    before this taxonomy existed)."""
    return _NAME_TO_ENTRY.get(name)


def lookup_case_insensitive(name):
    """Same as lookup() but case-insensitive and whitespace-trimmed - used
    by CSV/Excel import, where a spreadsheet author might type "shrinkage"
    instead of the exact stored casing "Shrinkage"."""
    if not name:
        return None
    return _NAME_TO_ENTRY_LOWER.get(name.strip().lower())


def all_issue_names():
    """Flat list of every controlled issue-type name, in category order -
    the full set of values CSV/Excel import will accept."""
    return list(_NAME_TO_ENTRY.keys())
