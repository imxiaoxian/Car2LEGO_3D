"""Shared shot contract; unregistered close-ups never become reconstruction cameras."""

PRIMARY_SHOTS = {"front": "front", "rear": "back", "body": "left", "wheels": "right"}

SUPPLEMENTAL_SHOTS = {
    "top": ("Exact overhead view of the complete car, front toward image LEFT. Orthographic camera, "
            "all panels closed. Show the bonnet, windscreen, roof, rear deck and both body edges.",
            ["body", "glazing", "front", "rear"]),
    "front_right": ("Complete car in a front-right three-quarter view, camera elevation 15 degrees, "
                    "long focal length. Show the right door, front fascia and windscreen together.",
                    ["front", "doors", "glazing"]),
    "rear_left": ("Complete car in a rear-left three-quarter view, camera elevation 15 degrees, "
                  "long focal length. Show the rear pillar, left door, rear deck and rear fascia together.",
                  ["rear", "doors", "glazing"]),
    "rear_right": ("Complete car in a rear-right three-quarter view, camera elevation 15 degrees, "
                   "long focal length. Show the right rear quarter, rear glass, rear deck and fascia together.",
                   ["rear", "doors", "glazing"]),
}

DETAIL_SHOTS = {
    "front_lights": ("Close-up of the complete front fascia including both lamp groups, grille, bumper, "
                     "bonnet edge and surrounding body. Distinguish main lamps, DRLs, indicators, lenses, "
                     "recesses and trim. Preserve the anchor's pop-up headlamp state where applicable.",
                     ["front", "lights"]),
    "rear_lights": ("Close-up of the complete rear fascia including both lamp groups, grille, exhausts, "
                    "bumper and adjacent deck. Preserve individual lens segments, housings and trim.",
                    ["rear", "lights"]),
    "door_windows": ("Exterior close-up of the CLOSED left door, entire side window, A/B pillars, "
                     "mirror, sill and adjacent body. Show the integrated door/window outline, window "
                     "inset, frame thickness and beltline. Keep surrounding panels for spatial context.",
                     ["doors", "glazing"]),
    "windscreen_cowl": ("Oblique close-up spanning bonnet rear edge, cowl, entire windscreen, "
                        "A pillars and roof front edge. Show slope, taper, glass/frame boundary and "
                        "continuous transition to both doors. Keep every named interface in frame.",
                        ["front", "glazing", "roof"]),
    "rear_transition": ("Oblique close-up spanning roof rear edge, rear pillars, rear glass, rear deck, "
                        "rear wheel shoulder and spoiler if present. Show how these surfaces join, "
                        "including buttresses, vents, panel steps and frame thickness.",
                        ["roof", "rear", "glazing"]),
    "side_intakes": ("Close-up of the left side intake region, sill, door trailing edge and rear quarter. "
                     "Preserve ducts, louvers and their depth if visible. If no intake exists, show the "
                     "unaltered panel instead of inventing an opening.", ["doors", "intakes", "rear"]),
    "wheel_arches": ("Low oblique close-up of the left front wheel and complete arch with adjacent "
                     "bonnet, bumper, sill and door edge. Show tyre profile, rim, arch lip, body clearance "
                     "and shoulder transition. Do not fabricate hidden suspension.", ["wheels", "body"]),
    "cockpit": ("Interior overview through an open door showing all front seats, dashboard, steering "
                "wheel, center tunnel, footwell and door lining in one coherent image. Use the exact "
                "vehicle seating layout and a single consistent upholstery/trim scheme. No cutaway.",
                ["interior", "doors"]),
    "seats": ("Interior close-up of both front seats including cushions, backrests, headrests, seat "
              "bases and their relation to the center tunnel and floor. Match the locked upholstery. "
              "Only show seats present in this exact vehicle; do not add racing harnesses by default.",
              ["interior", "seats"]),
    "dashboard": ("Interior close-up from the passenger side showing the complete dashboard, "
                  "instrument binnacle, steering wheel, center console, gear selector and footwell. "
                  "Preserve driving side and the same trim. Keep mounting context visible.",
                  ["interior", "dashboard"]),
}

SHOT_NAMES = ("overview", *PRIMARY_SHOTS, *SUPPLEMENTAL_SHOTS, *DETAIL_SHOTS)
REFERENCE_GROUPS = ("views", "supplemental_views", "details")


def auxiliary_instructions(name):
    focus, _ = {**SUPPLEMENTAL_SHOTS, **DETAIL_SHOTS}[name]
    return (focus + " Output ONE image from ONE camera, no panels, contact sheet, text or annotations. "
            "Use the same real vehicle, variant, paint, wheels, panel states and trim as the identity "
            "anchor and source. This is a synthetic appearance reference, not measured geometry or "
            "a LEGO model. Do not simplify lamp segments, window frames or surface intersections. "
            "Keep exterior panels closed except where interior access is explicitly requested. "
            "Do not invent unseen details or change the variant to fill uncertainty.")


def shot_requests():
    primary = [dict(view=slot, focus=f"A single complete orthographic {view} elevation of the same car. "
                   "Four-view reconstruction contract v1.", angles=[view],
                   reason="Labelled multiview reconstruction input") for slot, view in PRIMARY_SHOTS.items()]
    return primary + [dict(view=name, focus=focus, angles=[name], modules=modules,
                          reason="Supplemental unregistered appearance evidence; not a reconstruction camera")
                      for name, (focus, modules) in {**SUPPLEMENTAL_SHOTS, **DETAIL_SHOTS}.items()]
