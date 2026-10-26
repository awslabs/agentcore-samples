"""Claim scenarios for the auto-seeder (hydration/2_autoseed.py).

Each scenario is a structured brief used to drive an LLM "customer simulant"
(playing the policyholder) against the real claims agent. The simulant answers
the agent's follow-up questions using ONLY these facts.

Personas / policies (must match tools/policy_lookup.py + claims_history.py):
  - Bob Thompson     PH-1001  HO-2024-1001 (home), AU-2024-1001 (auto)
                              prior: water damage / burst pipe (Apr 2024)
  - Alice Martinez   PH-1042  HO-2024-1042 (home)
                              prior: theft / break-in (Aug 2024)
  - Charlie Davis    PH-1087  AU-2024-1087 (auto), HO-2024-1087 (home)
                              prior: auto collision (May 2024),
                                     water damage escalated-delayed (Sep 2024)

Auto-mode test personas:
  - David Park       PH-2001  HO-2024-2001 (home)       no prior claims
  - Sarah Chen       PH-2050  AU-2024-2050 (auto)       prior: fender bender (Nov 2025)
  - Marcus Rivera    PH-3001  AU-2024-3001 (auto), HO-2024-3001 (home)  no prior claims
  - Lisa Nguyen      PH-3050  HO-2024-3050 (home)       prior: water damage (Mar 2025)

Dates are explicit (with year) and recent relative to mid-2026 so the agent
doesn't have to guess. A few scenarios intentionally use delayed reporting
and/or repeat claim types to exercise fraud signals.
"""

SCENARIOS = [
    {
        "id": "bob-garage-fire",
        "name": "Bob Thompson",
        "actor_id": "PH-1001",
        "policy_number": "HO-2024-1001",
        "opening": (
            "I need to file a claim. An electrical fire started in my garage from a "
            "faulty extension cord and damaged the wall and some shelving. My homeowner "
            "policy is HO-2024-1001."
        ),
        "facts": """\
- Incident: electrical fire in the attached garage, started by a faulty/overloaded extension cord.
- Date/time: June 14, 2026, about 4:30pm. Filed the next day (June 15).
- Damage: scorched drywall on one garage wall (~6x6 ft), melted shelving unit, soot on the ceiling, one damaged power tool charger. Garage door and car unaffected.
- Injuries: none.
- Fire department: yes, called 911; engine responded and confirmed it was out. Incident #FD-2026-0614.
- Estimated cost: about $7,200 (drywall, repaint, shelving, electrical outlet replacement).
- Documentation: photos of the wall and shelving; the fire department left a report.
- Contact: Bob Thompson, (555) 014-1001, bob.thompson@example.com, 142 Maple Street, Springfield, IL 62704.
""",
    },
    {
        "id": "charlie-auto-collision",
        "name": "Charlie Davis",
        "actor_id": "PH-1087",
        "policy_number": "AU-2024-1087",
        "opening": (
            "I need to file an auto claim. I was rear-ended at an intersection today and "
            "the back bumper and trunk are damaged. My policy is AU-2024-1087."
        ),
        "facts": """\
- Incident: rear-ended by another vehicle at a red light at the intersection of Main St and Oak Ave.
- Date/time: June 20, 2026, about 5:15pm during evening commute. Filed same day.
- Damage: rear bumper crushed inward, trunk lid dented and won't close properly, one tail light broken.
- Injuries: none — I felt a bit of whiplash but I'm fine.
- Other driver: exchanged info; they admitted fault. Their insurance is StateFarm policy #SF-8827741.
- Police report: yes, officer arrived and filed report #PR-2026-0620.
- Estimated cost: body shop estimate of $3,800.
- Documentation: police report, photos of both vehicles at the scene, other driver's info, body shop written estimate.
- Contact: Charlie Davis, (555) 014-1087, charlie.davis@example.com, 27 Cedar Court, Springfield, IL 62704.
""",
    },
    {
        "id": "alice-theft-with-docs",
        "name": "Alice Martinez",
        "actor_id": "PH-1042",
        "policy_number": "HO-2024-1042",
        "opening": (
            "I need to file a theft claim. Someone broke into my home while I was at work "
            "and stole my laptop and some jewelry. Policy HO-2024-1042."
        ),
        "facts": """\
- Incident: burglary / break-in through the back window while I was at work.
- Date/time: June 18, 2026, sometime during the afternoon; discovered when I got home around 6:30pm. Filed the next day (June 19).
- Entry: forced entry through back window — latch broken, frame damaged.
- Stolen: a MacBook Pro laptop (~$2,500) and a gold bracelet + pendant necklace (~$3,000). Total about $5,500.
- Damage: broken rear window latch and frame (~$600 to repair).
- Injuries: none.
- Reports: yes, police report #PR-2026-0618; officer noted forced entry and took fingerprints.
- Documentation: photos of the broken window, Apple receipt for laptop, insurance appraisal for jewelry from 2023.
- Contact: Alice Martinez, (555) 014-1042, alice.martinez@example.com, 88 Birch Lane, Springfield, IL 62704.
- If asked about prior claims: I had a break-in in 2024 too — different incident, different items.
""",
    },
    {
        "id": "bob-vandalism-prompt",
        "name": "Bob Thompson",
        "actor_id": "PH-1001",
        "policy_number": "AU-2024-1001",
        "opening": (
            "I need to file an auto claim. Someone keyed my car and smashed the side mirror "
            "while it was parked on the street overnight. Policy AU-2024-1001."
        ),
        "facts": """\
- Incident: vandalism — car keyed along both driver-side doors and the driver's side mirror smashed while parked on the street overnight.
- Date/time: discovered the morning of June 22, 2026. Filed same morning.
- Damage: deep key scratches along both driver-side doors, broken side mirror housing and glass.
- Injuries: none.
- Reports: yes, filed a police report for vandalism, #PR-2026-0622.
- Estimated cost: body shop estimate of $2,100 (repaint doors, replace mirror assembly).
- Documentation: photos of the scratches and broken mirror, police report.
- Contact: Bob Thompson, (555) 014-1001, bob.thompson@example.com, 142 Maple Street, Springfield, IL 62704.
""",
    },
    {
        "id": "alice-basement-flood",
        "name": "Alice Martinez",
        "actor_id": "PH-1042",
        "policy_number": "HO-2024-1042",
        "opening": (
            "I want to file a claim. My basement flooded during last week's heavy storms — "
            "water backed up through the storm drain and ruined the carpet and drywall. "
            "Policy HO-2024-1042."
        ),
        "facts": """\
- Incident: basement flooding from storm-drain / sewer backup during a heavy rainstorm (rising water came up through the floor drain).
- Date/time: June 15, 2026, overnight during the storm. Filed same day (June 15).
- Damage: soaked carpet throughout the basement, lower 2 ft of drywall, a couch and some boxes of belongings.
- Injuries: none.
- Reports: no police/fire; the city was notified about neighborhood drain backups.
- Estimated cost: about $14,000 (carpet, drywall, furniture).
- Documentation: photos and a water-restoration company's estimate.
- Contact: Alice Martinez, (555) 014-1042, alice.martinez@example.com, 88 Birch Lane, Springfield, IL 62704.
- Note if asked about cause: it was water rising up from the storm drain / sewer backup during the storm, not a burst pipe inside the house. It's flooding from external water.
""",
    },
    {
        "id": "bob-repeat-water-delayed",
        "name": "Bob Thompson",
        "actor_id": "PH-1001",
        "policy_number": "HO-2024-1001",
        "opening": (
            "I'd like to file a claim. A pipe burst under my kitchen sink and the water "
            "damaged the cabinets and flooring. Policy HO-2024-1001."
        ),
        "facts": """\
- Incident: burst supply line under the kitchen sink; sudden internal water damage (NOT flood).
- Date/time: the pipe burst on June 5, 2026. I'm only filing now on June 25 — that's a 20-day delay.
- Damage: warped hardwood flooring in the kitchen, swollen lower cabinets, baseboard damage.
- Injuries: none.
- Reports: none (no police/fire).
- Estimated cost: about $13,500.
- Documentation: photos of the flooring and cabinets, plumber's invoice for the emergency repair.
- Contact: Bob Thompson, (555) 014-1001, bob.thompson@example.com, 142 Maple Street, Springfield, IL 62704.
- If asked about prior claims: I did have a basement burst-pipe water claim a couple of years ago (2024).
- If asked why the delay: honestly, I just didn't get around to it. I kept putting it off. No good excuse.
""",
    },
    {
        "id": "charlie-wind-roof-explained",
        "name": "Charlie Davis",
        "actor_id": "PH-1087",
        "policy_number": "HO-2024-1087",
        "opening": (
            "I want to file a claim. A windstorm tore shingles off my roof and a flying "
            "branch cracked a window about 10 days ago. I was traveling for work and couldn't "
            "file until now. Policy HO-2024-1087."
        ),
        "facts": """\
- Incident: wind damage from a severe windstorm — shingles torn off and a branch cracked a window.
- Date/time: June 10, 2026, late evening during the storm. Filing now on June 20 (10-day delay).
- Damage: roughly 20 shingles gone on the south slope, a cracked upstairs window, bent gutter section.
- Injuries: none.
- Reports: police report available for storm damage in the area, #PR-2026-0610.
- Estimated cost: about $6,400 (roof patch, window replacement, gutter repair).
- Documentation: photos of the roof and window (taken by neighbor while I was away), a roofer's estimate, police report.
- Contact: Charlie Davis, (555) 014-1087, charlie.davis@example.com, 27 Cedar Court, Springfield, IL 62704.
- If asked about the 10-day delay: I was traveling for work — I have flight receipts showing I was out of town June 10-19. My neighbor noticed the damage and took photos for me. I filed as soon as I got back.
- If asked about prior claims: I had a water damage claim in 2024 that was escalated due to delayed reporting, and an auto collision before that.
""",
    },
    {
        "id": "charlie-repeat-water-delayed",
        "name": "Charlie Davis",
        "actor_id": "PH-1087",
        "policy_number": "HO-2024-1087",
        "opening": (
            "I'd like to file a claim. A pipe behind my laundry room wall leaked and damaged "
            "the wall and flooring. Policy HO-2024-1087."
        ),
        "facts": """\
- Incident: slow pipe leak behind the laundry room wall; internal water damage (NOT flood).
- Date/time: I noticed it on June 5, 2026 but kept hoping it would dry out, so I'm only filing now on June 22 — that's a 17-day delay.
- Damage: water-stained and bulging drywall, warped vinyl flooring, some mold starting at the baseboard.
- Injuries: none.
- Reports: none.
- Estimated cost: about $9,200.
- Documentation: photos of the wall and flooring, a plumber's repair invoice.
- Contact: Charlie Davis, (555) 014-1087, charlie.davis@example.com, 27 Cedar Court, Springfield, IL 62704.
- If asked about prior claims: I had a kitchen pipe-leak water claim in 2024 that was escalated for delayed reporting.
- If asked about the delay: I thought it was minor and would dry on its own. No real excuse.
""",
    },
]


AUTO_MODE_SCENARIOS = [
    {
        "id": "david-kitchen-fire",
        "name": "David Park",
        "actor_id": "PH-2001",
        "policy_number": "HO-2024-2001",
        "opening": (
            "Hi, I need to file a claim. There was a grease fire in my kitchen last night "
            "that damaged the cabinets and part of the ceiling. My policy is HO-2024-2001."
        ),
        "facts": """\
- Incident: grease fire on the stovetop spread to the overhead cabinets and scorched the ceiling.
- Date/time: last night around 7:30 PM while cooking dinner. Filing today (next day).
- Damage: three upper cabinets destroyed, ceiling drywall scorched and needs replacement, exhaust fan melted, smoke damage to adjacent walls.
- Injuries: minor burn on my hand (treated at home, no hospital visit).
- Fire department: yes, called 911. Engine arrived, confirmed it was out.
- Estimated cost: about $18,000 (cabinets $8k, ceiling repair $4k, exhaust fan $1.5k, smoke remediation $4.5k).
- Documentation: photos taken immediately after, fire department report available.
- Contact: David Park, (555) 020-2001, david.park@example.com, 88 Willow Lane, Springfield, IL 62704.
- No prior claims — this is my first claim ever.
""",
    },
    {
        "id": "sarah-parking-collision",
        "name": "Sarah Chen",
        "actor_id": "PH-2050",
        "policy_number": "AU-2024-2050",
        "opening": (
            "I need to file an auto claim. Someone hit my car in a parking lot yesterday "
            "and drove off while I was inside a store. My policy is AU-2024-2050."
        ),
        "facts": """\
- Incident: hit-and-run in a grocery store parking lot. Someone backed into the driver side rear quarter panel and left.
- Date/time: yesterday afternoon, between 2:00 and 2:45 PM. Filing today (next day).
- Damage: dented and scraped rear quarter panel (driver side), tail light housing cracked, bumper misaligned.
- Injuries: none (wasn't in the car).
- Police report: yes, filed yesterday — have the report number. Store security footage shows a white SUV backing into my car.
- Estimated cost: body shop estimate of $4,800.
- Documentation: police report, store security footage (store manager said they can provide a copy), body shop written estimate, photos of damage.
- Contact: Sarah Chen, (555) 020-2050, sarah.chen@example.com, 15 Oak Ridge Dr, Springfield, IL 62704.
- If asked about prior claims: I had a minor fender bender last November that was covered.
""",
    },
    {
        "id": "marcus-fender-bender",
        "name": "Marcus Rivera",
        "actor_id": "PH-3001",
        "policy_number": "AU-2024-3001",
        "opening": (
            "Hi, I need to file an auto claim. I had a fender bender in a parking garage "
            "earlier today. My policy number is AU-2024-3001."
        ),
        "facts": """\
- Incident: low-speed collision in a parking garage — I was backing out of a space and clipped the car behind me.
- Date/time: earlier today, about 11:30am. Filing same day.
- Damage: my rear bumper is cracked and scraped, tail light housing cracked on one side. The other car had a small dent on their front fender.
- Injuries: none on either side.
- Other driver: exchanged info, no dispute.
- Police report: yes, garage security called police; have the report.
- Estimated cost: body shop estimate of $2,500 for my car.
- Documentation: police report, photos of both vehicles, exchanged insurance info, body shop estimate.
- Contact: Marcus Rivera, (555) 030-3001, marcus.rivera@example.com, 44 Sunset Blvd, Springfield, IL 62704.
- No prior claims — this is my first claim.
""",
    },
    {
        "id": "lisa-pipe-burst",
        "name": "Lisa Nguyen",
        "actor_id": "PH-3050",
        "policy_number": "HO-2024-3050",
        "opening": (
            "I need to file a homeowner claim. A pipe burst in my upstairs bathroom yesterday "
            "and water leaked down through the ceiling into the living room below. "
            "My policy is HO-2024-3050."
        ),
        "facts": """\
- Incident: pipe burst in upstairs bathroom — water flooded the bathroom floor and seeped through to the living room ceiling below.
- Date/time: yesterday morning around 8am. Discovered immediately (heard the water). Called a plumber right away. Filing today (next day).
- Damage: bathroom floor tiles cracked from pressure, living room ceiling drywall soaked and sagging, carpet in living room soaked, one light fixture damaged.
- Injuries: none.
- Reports: none (no police/fire needed).
- Plumber: came within 2 hours, shut off water and patched the pipe. Plumber invoice: $400.
- Estimated total cost: about $9,000 (ceiling drywall $2.5k, carpet replacement $3k, bathroom tile repair $2k, light fixture $500, plumber $400, water mitigation $600).
- Documentation: photos taken right away of water coming through ceiling, plumber's invoice, water mitigation company came for fans/dehumidifiers.
- Contact: Lisa Nguyen, (555) 030-3050, lisa.nguyen@example.com, 72 Magnolia Way, Springfield, IL 62704.
- If asked about prior claims: I had a burst pipe claim in 2025 — different pipe, different area of the house. That one was in the basement.
""",
    },
    {
        "id": "sarah-delayed-theft",
        "name": "Sarah Chen",
        "actor_id": "PH-2050",
        "policy_number": "AU-2024-2050",
        "opening": (
            "I want to file a claim. Someone broke into my car about two and a half weeks "
            "ago and stole my laptop bag and some electronics from the back seat. "
            "Policy AU-2024-2050."
        ),
        "facts": """\
- Incident: car break-in, smashed the rear passenger window, stole items from the back seat.
- Date/time: about 18 days ago. I discovered it the next morning. Filing today — that's an 18-day delay.
- Items stolen: laptop (MacBook Pro, ~$2,400), noise-canceling headphones (~$350), gym bag with shoes (~$200). Total about $3,000.
- Damage to vehicle: smashed rear passenger window ($450 to replace).
- Injuries: none.
- Police report: yes, filed the day after the break-in — have the report number.
- Documentation: police report, photos of the broken window, Apple receipt for laptop.
- Contact: Sarah Chen, (555) 020-2050, sarah.chen@example.com, 15 Oak Ridge Dr, Springfield, IL 62704.
- If asked about the 18-day delay: I was dealing with getting the window fixed first and honestly just kept putting off the insurance call. No good excuse — I just procrastinated.
- If asked about prior claims: I had a parking lot hit-and-run claim recently and a fender bender last year.
""",
    },
    {
        "id": "marcus-roof-no-docs",
        "name": "Marcus Rivera",
        "actor_id": "PH-3001",
        "policy_number": "HO-2024-3001",
        "opening": (
            "I'd like to file a claim for roof damage. There was a hailstorm about three "
            "weeks back and I think it damaged my roof. Policy HO-2024-3001."
        ),
        "facts": """\
- Incident: roof damage from a hailstorm.
- Date/time: about 3 weeks ago. Filing today — roughly a 21-day delay.
- Damage: several shingles are cracked or missing, possible dent on a roof vent.
- Injuries: none.
- Reports: no police report, no weather service report cited.
- Estimated cost: no contractor estimate yet — haven't had anyone look at it.
- Documentation: no photos taken. No inspection report. No weather report.
- Contact: Marcus Rivera, (555) 030-3001, marcus.rivera@example.com, 44 Sunset Blvd, Springfield, IL 62704.
- If asked about the delay: "I just got around to it." No travel, no extenuating circumstances.
- If asked for photos or documentation: "I haven't taken any yet. I can see it from the ground though."
- If asked for a contractor estimate: "I haven't called anyone yet."
- If asked about prior claims: no prior claims.
""",
    },
    {
        "id": "lisa-sewer-backup",
        "name": "Lisa Nguyen",
        "actor_id": "PH-3050",
        "policy_number": "HO-2024-3050",
        "opening": (
            "I need to file a claim urgently. My basement flooded this morning — sewage "
            "water came up through the floor drain during heavy rain. "
            "Policy HO-2024-3050."
        ),
        "facts": """\
- Incident: basement flooding from sewer line backup during heavy rain. Water and sewage came up through the basement floor drain.
- Date/time: this morning, early, during heavy rainfall. Filing same day.
- Damage: entire basement floor covered in water/sewage, carpet ruined, lower drywall soaked, washer/dryer may be damaged, storage items destroyed.
- Injuries: none.
- Reports: no police/fire; called the city water department to report the backup.
- Estimated cost: about $11,000 (carpet/pad removal $2k, drywall $3k, sanitization $2.5k, personal property $2k, appliance assessment $1.5k).
- Documentation: photos taken this morning, video of water coming up through drain.
- Contact: Lisa Nguyen, (555) 030-3050, lisa.nguyen@example.com, 72 Magnolia Way, Springfield, IL 62704.
- Note if asked about cause: this is sewer/drain backup from external water during heavy rain — NOT a burst pipe inside the house. The water came up from the city sewer system through the floor drain.
- If asked about prior claims: I had a burst pipe claim last year, but that was completely different — an internal pipe, not flooding.
""",
    },
]


def get_scenarios(ids=None):
    """Return all scenarios, or only those whose id is in `ids`."""
    all_scenarios = SCENARIOS + AUTO_MODE_SCENARIOS
    if not ids:
        return SCENARIOS
    wanted = set(ids)
    return [s for s in all_scenarios if s["id"] in wanted]
