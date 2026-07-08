"""
Configuration for all US sex offender registries.

scrape_method values:
  - direct:      published bulk CSV/JSON URL(s) in direct_downloads
  - arcgis:      ArcGIS FeatureServer query API in search_api
  - hybrid:      try direct, then download_page discovery, then HTML tables
  - interactive: search-only website; no automated bulk path
  - html:        attempt static HTML table scrape of registry_url
  - api:         generic REST pagination via search_api
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RegistryConfig:
    """Configuration for a single jurisdiction's sex offender registry."""

    name: str
    abbr: str
    registry_url: str
    direct_downloads: List[str] = field(default_factory=list)
    download_page: Optional[str] = None
    scrape_method: str = "interactive"
    search_api: Optional[str] = None
    state_code_column: str = "state"
    notes: str = ""


# Only methods that have been live-verified for bulk data:
#   GA  — direct CSV works
#   DC  — ArcGIS FeatureServer + CSV fallback
#   AZ  — published CSV URL exists but currently bot-blocked (403)
#   FL  — bulk files behind CAPTCHA/email form (manual)
# All others: interactive search only (HTML landing pages do not expose records).

REGISTRIES: List[RegistryConfig] = [
    RegistryConfig(
        name="National (NSOPW)",
        abbr="US",
        registry_url="https://www.nsopw.gov/",
        scrape_method="interactive",
        notes="National search only. Do not mass-scrape.",
    ),
    RegistryConfig(
        name="Alabama", abbr="AL",
        registry_url="https://www.alea.gov/node/270",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Alaska", abbr="AK",
        registry_url="https://sor.dps.alaska.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Arizona", abbr="AZ",
        registry_url="https://www.azdps.gov/services/public-services-center/sex-offender-compliance",
        direct_downloads=["https://icrimewatch.net/az_offenders.csv"],
        scrape_method="direct",
        notes=(
            "DPS publishes az_offenders.csv via OffenderWatch/iCrimewatch. "
            "Automated fetch often returns HTTP 403 (bot protection); "
            "download manually from the DPS page if needed."
        ),
    ),
    RegistryConfig(
        name="Arkansas", abbr="AR",
        registry_url="https://sexoffenderregistry.ar.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="California", abbr="CA",
        registry_url="https://oag.ca.gov/sex-offender-reg",
        scrape_method="interactive",
        notes="Megan's Law search interface; no public bulk API.",
    ),
    RegistryConfig(
        name="Colorado", abbr="CO",
        registry_url="https://apps.colorado.gov/apps/dps/sor/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Connecticut", abbr="CT",
        registry_url="https://sheriffalerts.com/cap_office_disclaimer.php?office=54567",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Delaware", abbr="DE",
        registry_url="http://sexoffender.dsp.delaware.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="District of Columbia", abbr="DC",
        registry_url="https://sexoffender.dc.gov/",
        direct_downloads=[
            "https://opendata.dc.gov/api/download/v1/items/10e58174831e49a2aebaa129cc1c3bd5/csv?layers=20"
        ],
        search_api=(
            "https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/MPD/FeatureServer/20/query"
        ),
        scrape_method="arcgis",
        notes="Open Data DC FeatureServer (paginated) with CSV fallback.",
    ),
    RegistryConfig(
        name="Florida", abbr="FL",
        registry_url="https://offender.fdle.state.fl.us/offender/sops/search.jsf",
        download_page="https://offender.fdle.state.fl.us/offender/sops/registryDownload.jsf",
        scrape_method="hybrid",
        notes=(
            "FDLE Registry Downloads require email + CAPTCHA; "
            "not fully automatable. Use download page in a browser."
        ),
    ),
    RegistryConfig(
        name="Georgia", abbr="GA",
        registry_url="https://gbi.georgia.gov/services/georgia-sex-offender-registry",
        direct_downloads=["https://state.sor.gbi.ga.gov/SORT_PUBLIC/sor.csv"],
        scrape_method="direct",
        notes="GBI publishes a public SOR CSV (verified working).",
    ),
    RegistryConfig(
        name="Hawaii", abbr="HI",
        registry_url="https://sexoffenders.ehawaii.gov/",
        scrape_method="interactive",
        notes="Bulk system exists but requires registration/login.",
    ),
    RegistryConfig(
        name="Idaho", abbr="ID",
        registry_url="https://www.isp.idaho.gov/sor_id/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Illinois", abbr="IL",
        registry_url="https://sor.isp.illinois.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Indiana", abbr="IN",
        registry_url="https://www.icrimewatch.net/indiana.php",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Iowa", abbr="IA",
        registry_url="https://www.iowasexoffender.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Kansas", abbr="KS",
        registry_url="https://www.kbi.ks.gov/registeredoffender/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Kentucky", abbr="KY",
        registry_url="http://kspsor.state.ky.us/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Louisiana", abbr="LA",
        registry_url="https://lsp.org/community-outreach/sex-offender-registry/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Maine", abbr="ME",
        registry_url="http://sor.informe.org/cgi-bin/sor/index.pl",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Maryland", abbr="MD",
        registry_url="https://dpscs.maryland.gov/onlineservs/socem/default.shtml",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Massachusetts", abbr="MA",
        registry_url="https://www.mass.gov/orgs/sex-offender-registry-board",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Michigan", abbr="MI",
        registry_url="https://mspsor.com/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Minnesota", abbr="MN",
        registry_url="https://coms.doc.state.mn.us/publicregistrantsearch",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Mississippi", abbr="MS",
        registry_url="https://state.sor.dps.ms.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Missouri", abbr="MO",
        registry_url="https://www.mshp.dps.missouri.gov/MSHPWeb/PatrolDivisions/CRID/SOR/SORPage.html",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Montana", abbr="MT",
        registry_url="https://app.doj.mt.gov/apps/svow/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Nebraska", abbr="NE",
        registry_url="https://sor.nebraska.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Nevada", abbr="NV",
        registry_url="https://sexoffenders.nv.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="New Hampshire", abbr="NH",
        registry_url="https://business.nh.gov/nsor/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="New Jersey", abbr="NJ",
        registry_url="https://www.njsp.org/sex-offender-registry/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="New Mexico", abbr="NM",
        registry_url="https://sheriffalerts.com/cap_office_disclaimer.php?office=55290",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="New York", abbr="NY",
        registry_url="https://www.criminaljustice.ny.gov/nsor/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="North Carolina", abbr="NC",
        registry_url="https://sexoffender.ncsbi.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="North Dakota", abbr="ND",
        registry_url="https://www.sexoffender.nd.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Ohio", abbr="OH",
        registry_url="https://www.icrimewatch.net/index.php?AgencyID=55149&disc=",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Oklahoma", abbr="OK",
        registry_url="https://sors.doc.ok.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Oregon", abbr="OR",
        registry_url="https://sexoffenders.oregon.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Pennsylvania", abbr="PA",
        registry_url="https://www.pameganslaw.state.pa.us/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Rhode Island", abbr="RI",
        registry_url="https://risp.ri.gov/safety-education/sex-offenders",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="South Carolina", abbr="SC",
        registry_url="https://scor.sled.sc.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="South Dakota", abbr="SD",
        registry_url="https://sor.sd.gov/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Tennessee", abbr="TN",
        registry_url="https://sor.tbi.tn.gov/home",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Texas", abbr="TX",
        registry_url="https://publicsite.dps.texas.gov/SexOffenderRegistry",
        scrape_method="interactive",
        notes="Search only; very large registry; no public bulk API.",
    ),
    RegistryConfig(
        name="Utah", abbr="UT",
        registry_url="https://www.communitynotification.com/cap_office_disclaimer.php?office=54438",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Vermont", abbr="VT",
        registry_url="https://vcic.vermont.gov/sor",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Virginia", abbr="VA",
        registry_url="https://www.vsp.virginia.gov/sex-offender-registry/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Washington", abbr="WA",
        registry_url="https://www.wasor.org/",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="West Virginia", abbr="WV",
        registry_url="https://apps.wv.gov/StatePolice/SexOffender",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Wisconsin", abbr="WI",
        registry_url="https://appsdoc.wi.gov/public/offenders",
        scrape_method="interactive",
    ),
    RegistryConfig(
        name="Wyoming", abbr="WY",
        registry_url="https://wyomingdci.wyo.gov/criminal-justice-information-services-cjis/sex-offender-registry",
        scrape_method="interactive",
    ),
]

USER_AGENT = (
    "Public-SOR-Archiver/1.2 "
    "(public bulk archival of published U.S. registry data; respectful low-rate access)"
)

DEFAULT_DELAY = 1.0
MAX_RETRIES = 3
REQUEST_TIMEOUT = 90


def get_registry_by_abbr(abbr: str) -> Optional[RegistryConfig]:
    for reg in REGISTRIES:
        if reg.abbr.lower() == abbr.lower():
            return reg
    return None


def get_registry_by_name(name: str) -> Optional[RegistryConfig]:
    for reg in REGISTRIES:
        if reg.name.lower() == name.lower():
            return reg
    return None


def get_direct_download_sources() -> List[RegistryConfig]:
    return [r for r in REGISTRIES if r.direct_downloads]


def get_bulk_capable_sources() -> List[RegistryConfig]:
    """Registries with an automated bulk path (direct/arcgis/api/hybrid with downloads)."""
    bulk_methods = {"direct", "arcgis", "api", "hybrid"}
    return [
        r
        for r in REGISTRIES
        if r.abbr != "US"
        and (
            r.scrape_method in bulk_methods
            or r.direct_downloads
            or r.search_api
        )
    ]


def get_all_state_registries() -> List[RegistryConfig]:
    return [r for r in REGISTRIES if r.abbr != "US"]
