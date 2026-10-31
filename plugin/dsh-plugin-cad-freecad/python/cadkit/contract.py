"""Single source of truth for geometry-policy constants shared between the
family generator (design reservations) and the validator (acceptance gates).

W28 (workflow audit): a formula repeated in two places with different
geometric meanings is not a policy. Each policy below is ONE named value
with ONE meaning, consumed by both sides; where design reservations are
STRICTER than the acceptance gate that is explicit and documented, never
implicit via coincidentally equal expressions.
"""

#: Physical socket-head across-corners radius, factor of the clearance-hole
#: radius. THE head envelope: nothing may occupy it above the flange face
#: (validator gate, family cannot design it away).
HEAD_ENLARGE = 1.5


def head_envelope_radius(bolt_r):
    """Acceptance-gate radius about a bolt axis: a real bolt head
    (across-corners ~1.5 x hole radius) must seat and insert here.
    Thin shroud rings closer than the tool reservation are an accepted
    IEC design convention when this gate passes (Allen-driver access)."""
    return HEAD_ENLARGE * float(bolt_r)


def head_envelope_height(bolt_r):
    """Axial envelope above the flange face admitting head + insertion
    path (mm)."""
    return HEAD_ENLARGE * float(bolt_r) + 2.0


#: Design-time reservation (STRICTER than the gate): ribs, bosses and pads
#: the generator places must additionally keep wrench/socket-tooling margin.
#: Shrouds/lips follow ring placement rules, not rib rules, hence the two
#: constants.
TOOL_MARGIN = 2.0


def rib_reservation_radius(bolt_r):
    """Radial reservation for GENERATED ribs/pads about a bolt axis."""
    return HEAD_ENLARGE * float(bolt_r) + TOOL_MARGIN
