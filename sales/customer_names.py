"""Conservative, explicit identities for imported customer/event label variants.

Do not infer identity from similar first names, abbreviations, program names or
branch prefixes. Dates/day numbers are annotations only for the listed events.
"""
import re

GROUPS = {
    'Ala Eh Festival': ['Ala Eh', 'Ala Eh Batangas', 'Ala Eh Festival'],
    'Dairy Box Sales': ['Dairy Box', 'Dairy Box Sales'],
    'Tikme': ['Tikme'],
    'BSU': ['BSU'],
    'SM': ['SM'],
    'San Jose': ['San Jose'],
    'Sta. Cruz': ['Sta Cruz'],
    'Sta. Rosa': ['Sta Rosa'],
    'Sto. Tomas': ['Sto Tomas'],
    'Kadiwa-DBM': ['Kadiwa DBM'],
    'Kadiwa-BIR': ['Kadiwa BIR'],
    'Kadiwa-San Pedro': ['Kadiwa San Pedro'],
    'Kadiwa-Sta. Cruz': ['Kadiwa Sta Cruz'],
    'Kadiwa-Sta. Rosa': ['Kadiwa Sta Rosa'],
    'Bombay-Lipa': ['Bombay Lipa'],
    'Camsur': ['Cam Sur'],
    'MHO-Garcia': ['MHO Garcia'],
    'Members': ["Member's", "Member's Sales", 'Members Sales', 'Members'],
    'Staff Sales': ['Staff', 'Staff Sales'],
    'Lito Ayson': ['Sir Lito Ayson', 'Lito Ayson'],
    'Eat Plenty Food Corp': ['Eat Plenty', 'Eat Plenty Food Corp'],
    # Customer identities explicitly confirmed by the owner on 2026-10-23.
    'Jamie Ortiz': ['Jaime Ortiz', 'Jamie Ortiz'],
    'Cassie Alagar': ['Cassie', 'Cassie A.', 'Cassie Alagar'],
    'Sir Sonny': ['Sonny', 'Sir Sonny', 'Sonny Bombay'],
    'Jonny Decena': ['Jhonny Decena', 'Jonny Decena'],
}


def formatting_key(label):
    return re.sub(r"[\s._()'’\-]+", '', label).casefold()


ALIASES = {formatting_key(alias): canonical for canonical, variants in GROUPS.items()
           for alias in [canonical, *variants]}
DAY = re.compile(r'^(BSU|SM|San Jose|Sta\.?\s*Cruz|Tikme)[\s-]+Day\s*\d+(?:\s*&\s*\d+)?$', re.I)
DATED = re.compile(r'^(Dairy Box(?: Sales)?|Tikme|Kadiwa[\s_-]+DBM)\s+'
                   r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
                   r'Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*'
                   r'\d{1,2}(?:\s*[-,]\s*\d{1,2})*(?:\s+Sales)?$', re.I)
ALA = re.compile(r'^Ala\s+Eh(?:\s+(?:Batangas|Festival))?'
                 r'(?:\s+Dec(?:ember)?\.?\s*\d{1,2}(?:\s*-\s*\d{1,2})?)?(?:\s+Sales)?$', re.I)


def consolidated_name(label):
    label = ' '.join(label.split())
    if ALA.fullmatch(label):
        return 'Ala Eh Festival'
    match = DAY.fullmatch(label) or DATED.fullmatch(label)
    if match:
        label = match.group(1)
    return ALIASES.get(formatting_key(label), label)
