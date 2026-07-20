"""Regex maps for offense labels and chrome-drop clauses."""
from __future__ import annotations

import re

_rx = lambda p: re.compile(p, re.I)  # noqa: E731

# Structural join is always middle-dot `` · `` (see normalize_crime_separators).
CODE_MAP = [
    (_rx(r"SEX\s*BAT\s*/?\s*WPN\.?\s*OR\s*FORCE"), "Sexual battery · weapon/force"),
    (_rx(r"SEX\s*BAT\s*BY\s*ADULT\s*/?\s*VCTM\s*UNDER\s*12"), "Sexual battery · adult/victim under 12"),
    (_rx(r"SEX\s*BAT\s*BY\s*JUVEN\s*/?\s*VCTM\s*UNDER\s*12"), "Sexual battery · juvenile/victim under 12"),
    (_rx(r"SEX\s*BAT\s*/?\s*INJ\s*NOT\s*LIKELY"), "Sexual battery · injury not likely"),
    (
        _rx(r"LEWD\s*ASLT\s*/?\s*SEX\s*BAT\s*VCTM\s*<?\s*16"),
        "Sexual battery · victim under 16",
    ),
    (
        _rx(r"LEWD,?\s*LASCIVIOUS\s*(?:CHILD\s*)?U/?16"),
        "Victim under 16",
    ),
    (_rx(r"SEXUAL\s*BATTERY\s*BY\s*ADULT\s*ON\s*ADULT"), "Sexual battery · adult on adult"),
    # VA/FL/etc: FAIL TO REG / FAIL TO REGISTER / FAIL REG/PROVIDE FALSE INFO
    (
        _rx(
            r"FAIL(?:URE)?\s*TO\s*REG(?:IST(?:ER|RATION)?|\b|/)"
            r"|FAIL\s*COMPLY\s*REG|RE-?REGISTR"
            r"|FAIL\s*TO\s*REG/?\s*PROVIDE\s*FALSE"
        ),
        "Fail to register",
    ),
    (_rx(r"TRAVELING\s+TO\s+MEET\s+MINOR"), "Traveling to meet minor"),
    (_rx(r"STATUTORY\s+SEXUAL\s+SEDUCTION"), "Statutory sexual seduction"),
    (_rx(r"COMMUNICATE\s+WITH\s+MINOR\s+FOR\s+IMMORAL"), "Communicate with minor · immoral purposes"),
]

OFFENSE_MAP = [
    (r"rape\s+of\s+(?:a\s+)?child\s+with\s+force", "Rape of child with force"),
    (r"rape\s+and\s+abuse\s+of\s+(?:a\s+)?child", "Rape and abuse of child"),
    (r"rape\s+of\s+(?:a\s+)?child", "Rape of a child"),
    (r"\brape\b.*\b1st\b|\b1st\s+degree\s+rape\b", "Rape 1st degree"),
    (r"\brape\b.*\b2nd\b|\b2nd\s+degree\s+rape\b", "Rape 2nd degree"),
    (r"\brape\b.*\b3rd\b|\b3rd\s+degree\s+rape\b", "Rape 3rd degree"),
    (r"\brape\b", "Rape"),
    # NE/KS style: "1st Degree Sexual Assault F2"
    (
        r"(?:1st|first)\s+degree\s+sexual\s+assault",
        "First degree sexual assault",
    ),
    (
        r"(?:2nd|second)\s+degree\s+sexual\s+assault",
        "Second degree sexual assault",
    ),
    (
        r"(?:3rd|third)\s+degree\s+sexual\s+assault",
        "Third degree sexual assault",
    ),
    (r"sodomy", "Sodomy"),
    (r"child\s+molestation", "Child molestation"),
    (r"sexual\s+exploitation\s+of\s+a\s+child", "Sexual exploitation of a child"),
    (r"aggravated\s+indecent\s+liberties", "Aggravated indecent liberties"),
    (r"indecent\s+liberties", "Indecent liberties"),
    (r"indecent\s+exposure", "Indecent exposure"),
    # MA: Indecent assault and battery on child under 14 / person 14+
    (
        r"indecent\s+assault\s+and\s+battery\s+on\s+(?:a\s+)?"
        r"child\s+under\s+14",
        "Indecent assault and battery · child under 14",
    ),
    (
        r"indecent\s+assault\s+and\s+battery\s+on\s+(?:a\s+)?"
        r"person\s+aged\s+14\s+or\s+older",
        "Indecent assault and battery · person 14 or older",
    ),
    (r"indecent\s+assault\s+and\s+battery", "Indecent assault and battery"),
    (r"open\s+and\s+gross\s+lewdness", "Open and gross lewdness"),
    (r"indecency\s+with\s+(?:a\s+)?child", "Indecency with a child"),
    # CA PC 647.6: ANNOY / MOLEST CHILDREN
    (r"annoy\s*/?\s*molest\s+children", "Annoy/molest children"),
    (r"annoy(?:ing)?\s+(?:or\s+)?molest(?:ing)?\s+(?:a\s+)?child", "Annoy/molest children"),
    (r"criminal\s+sexual\s+conduct", "Criminal sexual conduct"),
    # CO short form: SEX ASSAULT - VIC INCAPABLE APPRAIS COND - ATTEMPT
    (
        r"sex(?:ual)?\s*assault.{0,80}incapable\s+apprais.+\battempt\b"
        r"|\battempt\b.{0,40}sex(?:ual)?\s*assault.{0,80}incapable\s+apprais",
        "Attempted sexual assault · victim incapable of appraising condition",
    ),
    (
        r"sex(?:ual)?\s*assault.{0,80}(?:vic(?:tim)?\s+)?incapable\s+apprais",
        "Sexual assault · victim incapable of appraising condition",
    ),
    (r"\bsex(?:ual)?\s*assault\b", "Sexual assault"),
    (r"sexual\s+performance\s+(?:by|of)\s+(?:a\s+)?child", "Sexual performance by a child"),
    (r"unlawful\s+sexual\s+activity", "Unlawful sexual activity with minor"),
    (
        r"(?:solicit|possess|control|intentionally\s+view).{0,40}child\s+porn"
        r"|possession\s+of\s+child\s+porn|child\s+pornography",
        "Possession of child pornography",
    ),
    (r"false\s+imprison", "False imprisonment"),
]

DROP_CLAUSE = re.compile(
    r"(?ix)^(?:"
    r"commission\s+of\s+or\s+attempt.*"
    r"|attempt,?\s*solicit,?\s*or\s*conspire.*"
    r"|chapter\s+\d+.*"
    r"|f\.?s\.?\s*[\d.]+.*"
    r"|s\.?\s*\d{3}\.\d+.*"
    r"|rcw\s+[\d\s.a-z]+$"
    r"|guilty/?convict.*"
    r"|adjudication\s+withheld.*"
    r"|principal\s*$"
    r"|charge\s+correlation\s+pending.*"
    r"|no\s+picture\s+available.*"
    r"|registration\s+of\s+criminal\s+offenders.*"
    # VA/SC registry chrome (DAVID DAVILA): tier label, not a charge
    r"|tier\s+(?:i{1,3}|iv|v|[1-5]|one|two|three|four|five)"
    r"\s+sex\s+offender.*"
    r"|registration\s+viol.*"
    r"|2nd\s*$|1st\s*$|3rd\s*$"
    r"|scars,?\s*marks\s+and\s+tattoos.*"
    r"|alias(?:es)?\s*(?:information)?:?$"
    r"|photos?:?$"
    r"|more\s+information.*"
    r"|compliant\s+tier\s+level.*"
    r"|offender\s+age\s+at\s+time.*"
    r"|physical\s+description.*"
    r"|name:?$|level:?.*"
    r"|status\s*:.*"
    r"|conviction\s*:.*"
    r"|statute\s*number\(s\)?\s*:?\s*$"  # bare header without codes
    r"|texas\s+offenses?$"
    r"|texas\s+penal\s+code.*"
    r"|this\s+link\s+reflects.*"
    r")$"
)

# Common registry offense abbreviations -> full words. Used only to help
# OFFENSE_MAP recognize abbreviated charges (e.g. "ATT SEX ASSLT" ->
# "ATTEMPT SEXUAL ASSAULT"). CODE_MAP still runs on the original text, so its
# abbreviation patterns (SEX BAT / VCTM …) are unaffected.
_ABBREV_EXPANSIONS = [
    (re.compile(r"\bASSLT\b", re.I), "ASSAULT"),
    (re.compile(r"\bASLT\b", re.I), "ASSAULT"),
    (re.compile(r"\bATT\b", re.I), "ATTEMPT"),
    (re.compile(r"\bVCTM\b", re.I), "VICTIM"),
    (re.compile(r"\bVIC\b", re.I), "VICTIM"),
    (re.compile(r"\bBAT\b", re.I), "BATTERY"),
    (re.compile(r"\bAGG\b", re.I), "AGGRAVATED"),
    (re.compile(r"\bAGGRAV\b", re.I), "AGGRAVATED"),
    (re.compile(r"\bWPN\b", re.I), "WEAPON"),
    (re.compile(r"\bINJ\b", re.I), "INJURY"),
    (re.compile(r"\bDEG\b", re.I), "DEGREE"),
    (re.compile(r"\bFEL\b", re.I), "FELONY"),
    (re.compile(r"\bMISD\b", re.I), "MISDEMEANOR"),
    (re.compile(r"\bPOSS\b", re.I), "POSSESSION"),
]


def expand_offense_abbreviations(text: str) -> str:
    """Expand common registry offense abbreviations for label matching only."""
    t = text or ""
    for rx, repl in _ABBREV_EXPANSIONS:
        t = rx.sub(repl, t)
    return t
