"""Virginia vspsor.com offense card + SSL-friendly parse."""
from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scraper.reports.fetcher import ReportFetcher
from scraper.reports.fetcher_crime_va import extract_va_card_offenses


_VA_HTML = """
<html><body>
<div id="convictions" role="tabpanel">
  <h3>Offenses</h3>
  <div class="row">
    <div class="col-lg-6">
      <div class="card noBreak">
        <div class="card-header gold">
          <span>18.2-472.1 - SEX OFFENDER FAIL TO REG/PROVIDE FALSE INFO - </span>
        </div>
        <div class="card-body">
          <div class="row">
            <div class="col text-end">Date Convicted:</div>
            <div class="col fw-bold"><span>02/21/2019</span></div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-lg-6">
      <div class="card noBreak">
        <div class="card-header gold">
          <span>18.2-374.1:1(C) - POSSESSION OF CHILD PORNOGRAPHY - </span>
        </div>
        <div class="card-body">
          <div class="row">
            <div class="col text-end">Date Convicted:</div>
            <div class="col fw-bold"><span>06/08/2011</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="row"><div>Race:</div><div>WHITE</div></div>
</body></html>
"""


class VaCrimeParseTests(unittest.TestCase):
    def test_extract_va_card_offenses(self):
        soup = BeautifulSoup(_VA_HTML, "html.parser")
        crime = extract_va_card_offenses(soup)
        self.assertIn("FAIL TO REG", crime.upper())
        self.assertIn("CHILD PORNOGRAPHY", crime.upper())
        self.assertNotIn("Date Convicted", crime)

    def test_from_html_sets_crime(self):
        f = ReportFetcher.__new__(ReportFetcher)
        found = f._from_html(_VA_HTML, "https://vspsor.com/")
        crime = (found.get("crime") or "").upper()
        self.assertIn("PORNOGRAPHY", crime)
        self.assertIn("FAIL TO REG", crime)


if __name__ == "__main__":
    unittest.main()
