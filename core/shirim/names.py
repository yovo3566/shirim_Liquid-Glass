"""Artist-name and song-title normalization, so one performer gets one folder and a song isn't kept twice."""

import re
import unicodedata

DURATION_TOLERANCE = 3  # seconds; re-uploads of one recording differ by a second or two

_HEBREW = re.compile(r'[֐-׿]')
_LATIN = re.compile(r'[A-Za-z]')
_NIQQUD = re.compile(r'[֑-ׇ]')
# "A & B", "A feat. B", "A עם B", "A מארחים את B": the first name is the lead artist
_JOINERS = re.compile(r'\s+(?:&|x|feat\.?|ft\.?|עם|מארחים את|מארח את|מארחת את)\s+', re.IGNORECASE)


def _tidy(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text or '')).strip()


def is_hebrew(text):
    return bool(_HEBREW.search(text or ''))


def clean_artist(name):
    """Drop YouTube channel decorations: 'ישי ריבו - Ishay Ribo' -> 'ישי ריבו', 'X - Topic' -> 'X'."""
    name = re.sub(r'\s+-\s+Topic$', '', _tidy(name))
    if ' - ' in name:
        left, right = (part.strip() for part in name.split(' - ', 1))
        if is_hebrew(left) and not _LATIN.search(left) and _LATIN.search(right):
            name = left
    return name


def split_artists(artist):
    return [name for name in (clean_artist(part) for part in (artist or '').split(', ')) if name]


def lead_artist(artist, known=()):
    """The main performer of a credit such as 'ישי ריבו ואמיר דדון, Akiva' (-> 'ישי ריבו')."""
    credited = split_artists(artist)
    if not credited:
        return ''
    lead = _JOINERS.split(credited[0])[0].strip()
    # Hebrew joins names with a ו prefix; split there only when the part before it is a known artist
    for match in re.finditer(r' ו', lead):
        if lead[:match.start()] in known:
            return lead[:match.start()]
    return lead


def credit(artist, raw_lead, lead):
    """The cleaned full credit, with the lead artist spelled canonically."""
    text = ', '.join(split_artists(artist))
    if raw_lead and text.startswith(raw_lead):
        text = lead + text[len(raw_lead):]
    return text


def title_key(title):
    title = _NIQQUD.sub('', _tidy(title).casefold())
    title = re.sub(r'[^\w\s()]', ' ', title)
    return re.sub(r'\s+', ' ', title).strip()


def same_recording(duration_a, duration_b):
    return not duration_a or not duration_b or abs(duration_a - duration_b) <= DURATION_TOLERANCE
