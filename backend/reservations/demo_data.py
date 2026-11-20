"""The public demo restaurant's fictional bookings (#259 decisions 85 and 94).

One source for the seeded diners, read by both the demo seed
(backend/seed_reservations.py), which writes them, and the daily demo purge
(diner_data.purge_demo_location), which keeps a seeded row only while its
details still equal these exactly. Booking `index` of local day `day` has
id `reservation_id(day, index)` and the contact `seeded_contact` returns for
that id. Every phone is 555-01xx and every email @example.com: fiction only.
"""

from datetime import date

LUNCH, DINNER = "Lunch", "Dinner"

# (period, hour, minute, party size). Each day books six of these, a
# different six by weekday, so the week does not repeat itself.
BOOKING_PLAN = [
    (LUNCH, 12, 0, 2), (LUNCH, 12, 30, 4), (LUNCH, 13, 15, 3),
    (DINNER, 17, 30, 2), (DINNER, 18, 0, 4), (DINNER, 18, 30, 7),
    (DINNER, 19, 0, 2), (DINNER, 19, 30, 5), (DINNER, 20, 15, 2),
]
DINERS = [
    "Avery Quill", "Bram Ostrowe", "Celine Marchetti", "Dorian Vale", "Esme Thackery",
    "Felix Ambrose", "Greta Halvorsen", "Hollis Ndiaye", "Ines Calloway", "Jasper Wren",
    "Keira Moss", "Lionel Stroud", "Mira Castellane", "Nico Faraday", "Odette Brightwell",
    "Pascal Inwood", "Quinn Albright", "Rhea Delacroix", "Silas Penhallow", "Talia Okonkwo",
    "Ulric Hale", "Vera Lindgren", "Wesley Ashcombe", "Xiomara Reyes", "Yusuf Kestrel",
    "Zelda Marwood", "Arlo Fenwick", "Bianca Torvald", "Cyrus Bellamy", "Delphine Oakes",
    "Emeric Szabo", "Fiona Garrick", "Gideon Larch", "Harriet Sloane", "Idris Wolcott",
    "June Halloway", "Kasimir Dunne", "Lark Emberly", "Marcus Thistle", "Nadia Quaresma",
    "Orrin Blackwood", "Priya Venkatesan",
]
NOTES = [None, "Celebrating an anniversary", None, "High chair, please", None,
         "Window table if possible", None, "One guest uses a wheelchair"]

_PREFIX = "rsv"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int, width: int) -> str:
    """Fixed-width base36 (the same packing as backend/seed.py's ids)."""
    out = ""
    while n:
        n, rem = divmod(n, 36)
        out = _B36[rem] + out
    return out.rjust(width, "0")[-width:]


def reservation_id(day: date, index: int) -> str:
    """"rsv" + the local date's ordinal (4 base36 chars, to the year 4600) +
    its position in BOOKING_PLAN: the same booking always has the same id."""
    return f"{_PREFIX}{_b36(day.toordinal(), 4)}{_b36(index, 1)}"


def _parse(res_id: str) -> tuple[date, int] | None:
    """(day, index) of a seeded id, or None for any other id."""
    if len(res_id) != 8 or not res_id.startswith(_PREFIX):
        return None
    if any(c not in _B36 for c in res_id[3:]):
        return None
    index = int(res_id[7], 36)
    try:
        day = date.fromordinal(int(res_id[3:7], 36))
    except ValueError:
        return None
    if index >= len(BOOKING_PLAN) or reservation_id(day, index) != res_id:
        return None
    return day, index


def diner(day: date, index: int) -> tuple[str, str, str]:
    """(name, phone, email) of booking `index` on `day`."""
    n = (day.toordinal() * len(BOOKING_PLAN) + index) % len(DINERS)
    name = DINERS[n]
    first, last = name.lower().split(" ", 1)
    return (name,
            f"+1212555{100 + n % 100:04d}",  # 555-0100..0199: fiction only
            f"{first}.{last.replace(' ', '')}@example.com")


def notes(day: date, index: int) -> str | None:
    return NOTES[(day.toordinal() + index) % len(NOTES)]


def seeded_contact(res_id: str) -> tuple[str, str, str, str | None] | None:
    """(diner_name, diner_phone, diner_email, notes) the seed writes for this
    id, or None when the id is not a seeded one."""
    parsed = _parse(res_id)
    if parsed is None:
        return None
    day, index = parsed
    return (*diner(day, index), notes(day, index))
