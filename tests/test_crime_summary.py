"""Tests for SOR crime string summarization (Reports mode)."""
from __future__ import annotations

import re
import unittest

from scraper.crime_summary import summarize_crime


class CrimeSummaryTests(unittest.TestCase):
    def test_fl_boilerplate_preferred_example(self):
        """CHRISTOPHER SINGH-style FL dump keeps victim-age lewd details."""
        raw = (
            "Commission of OR Attempt, Solicit, or Conspire to Commit; "
            "Chapter 794; Sexual Battery *Excluding subsections 794.011(10); "
            "s. 800.04(4)(b); Lewd/lascivious offenses committed upon or in the "
            "presence of persons less than 16 years of age, where the victim is "
            "under 12 or the court finds the use of force or coercion.; "
            "s. 800.04(5)(c)1; Lewd/lascivious offenses committed upon or in the "
            "presence of persons less than 16 years of age, where the court finds "
            "molestation involving unclothed genitals.; s. 800.04(5)(d)"
        )
        out = summarize_crime(raw)
        self.assertIn("Sexual battery", out)
        self.assertIn("under 12", out.lower())
        self.assertIn("unclothed genitals", out.lower())
        self.assertNotIn("lewd", out.lower())
        self.assertNotIn("lascivious", out.lower())
        self.assertNotIn("Commission of", out)
        self.assertNotIn("800.04", out)
        self.assertNotIn("(", out)
        self.assertNotIn(")", out)
        self.assertEqual(
            out,
            "Sexual battery · Victim under 12/force · Unclothed genitals",
        )

    def test_fl_short_codes(self):
        raw = (
            "11/19/2025; SEX BAT/ WPN. OR FORCE; F.S. 794.011(3); 1909371; "
            "Miami-Dade, FL; Guilty/convict; Commission of OR Attempt, Solicit, "
            "or Conspire to Commit; Chapter 794; Sexual Battery "
            "*Excluding subsections 794.011(10)"
        )
        out = summarize_crime(raw)
        self.assertIn("Sexual battery", out)
        self.assertNotIn("Commission of", out)
        self.assertNotIn("794.011", out)
        self.assertNotIn("11/19", out)

    def test_empty(self):
        self.assertEqual(summarize_crime(""), "")
        self.assertEqual(summarize_crime(None), "")

    def test_strips_city_state_and_county(self):
        raw = (
            "SEX BAT/ WPN. OR FORCE; F.S. 794.011(3); Miami-Dade, FL; "
            "Guilty/convict; Sexual Battery"
        )
        out = summarize_crime(raw)
        self.assertIn("Sexual battery", out)
        self.assertNotIn("Miami", out)
        self.assertNotIn("FL", out)
        self.assertNotIn("Guilty", out)

    def test_strips_address_trail(self):
        raw = "Sexual battery; residence at 123 Main St, Springfield, IL 62701"
        out = summarize_crime(raw, max_len=80)
        self.assertIn("Sexual battery", out)
        self.assertNotIn("Main St", out)
        self.assertNotIn("Springfield", out)

    def test_federal_case_docket_not_shown(self):
        """David Jesus Barcenas-style FL dump: real charges only, no docket scrap."""
        raw = (
            "01/10/2012; SEX OFFENSE, FEDERAL (POSSESSION OF CHILD PORNOGRAPHY); "
            "1:0:11-60222-CR-WILLIAMS-01; Federal, FL; Guilty/convict; "
            "Commission of OR Attempt, Solicit, or Conspire to Commit; "
            "Chapter 794; Sexual Battery *Excluding subsections 794.011(10)"
        )
        out = summarize_crime(raw)
        self.assertIn("Possession of child pornography", out)
        self.assertIn("Sexual battery", out)
        self.assertNotIn("60222", out)
        self.assertNotIn("Williams", out)
        self.assertNotIn("1:0", out)
        self.assertNotIn("Federal", out)
        self.assertNotIn("Guilty", out)

    def test_fail_to_register_not_dropped_as_statute(self):
        raw = (
            "02/01/2007; Sex Offender Fail Comply Registration; F.s. 943.0435(9); "
            "0512631; Hillsborough, FL; Guilty/convict"
        )
        out = summarize_crime(raw)
        self.assertEqual(out, "Fail to register")
        self.assertNotIn("0512631", out)
        self.assertNotIn("943", out)

    def test_child_porn_counts_clean(self):
        raw = (
            "SOLICIT, POSSESS, CONTROL, OR INTENTIONALLY VIEW CHILD PORNOGRAPHY "
            "827.071(5) (PRINCIPAL (10 COUNTS)); 1508477; Pinellas, FL"
        )
        out = summarize_crime(raw)
        self.assertIn("Possession of child pornography", out)
        self.assertIn("10 counts", out)
        self.assertNotIn("827", out)
        self.assertNotIn("1508477", out)

    def test_no_parentheses_in_output(self):
        samples = [
            "SEX BAT/ WPN. OR FORCE; F.S. 794.011(3); Sexual Battery",
            "SEX OFFENSE, FEDERAL (POSSESSION OF CHILD PORNOGRAPHY)",
            "SOLICIT, POSSESS CHILD PORNOGRAPHY 827.071(5) (PRINCIPAL (10 COUNTS))",
            "Communicate with minor for immoral purposes",
        ]
        for raw in samples:
            out = summarize_crime(raw)
            self.assertNotIn("(", out, msg=raw)
            self.assertNotIn(")", out, msg=raw)

    def test_lewd_under_12_not_dropped(self):
        raw = (
            "Lewd/lascivious offenses committed upon or in the presence of persons "
            "less than 16 years of age, where the victim is under 12 or the court "
            "finds the use of force or coercion."
        )
        out = summarize_crime(raw)
        self.assertEqual(out, "Victim under 12/force")
        self.assertNotIn("lewd", out.lower())
        self.assertNotIn("lascivious", out.lower())

    def test_icrimewatch_description_label_not_shown(self):
        """Bare field labels must not appear as the crime summary."""
        self.assertEqual(summarize_crime("• Description:"), "")
        self.assertEqual(summarize_crime("Description:"), "")
        self.assertEqual(summarize_crime("Description"), "")
        out = summarize_crime(
            "• Description: 76-4-401 - ENTICING A MINOR/2ND DEGREE FELONY"
        )
        self.assertIn("Enticing", out)
        self.assertNotIn("Description", out)

    def test_nv_multicolumn_dump_not_name_or_court(self):
        """ALAWI-style NV dump must not summarize to person name / city."""
        raw = (
            "06/05/2009; 288 (A) LEWD OR LASCIVIOUS ACTS W/ CHILD UNDER 14; "
            "SAN DIEGO SUPERIOR COURT; EHAB ABDALLAH ALAWI; SAN DIEGO, CA; "
            "DEPT OF CORRECTIONS"
        )
        out = summarize_crime(raw)
        self.assertEqual(out, "Victim under 14")
        self.assertNotIn("Alawi", out)
        self.assertNotIn("San Diego", out)
        self.assertNotIn("Ehab", out)
        self.assertNotIn("lewd", out.lower())

    def test_ca_pc288_clean_phrase(self):
        out = summarize_crime("288 (A) LEWD OR LASCIVIOUS ACTS W/ CHILD UNDER 14")
        self.assertEqual(out, "Victim under 14")

    def test_co_crs_statute_not_eaten_as_date(self):
        """ANTONIO JACOB CHAVARRIA: 18-3-402 must not become '1 — b — SEX ASSAULT…'."""
        raw = (
            "18-3-402(1)(b) — SEX ASSAULT - VIC INCAPABLE APPRAIS COND - ATTEMPT"
        )
        out = summarize_crime(raw)
        self.assertNotIn("1 — b", out)
        self.assertNotIn("1 - b", out)
        self.assertNotIn("VIC INCAPABLE", out.upper())
        self.assertNotRegex(out, r"^\d")
        self.assertIn("sexual assault", out.lower())
        self.assertIn("incapable", out.lower())
        self.assertIn("attempt", out.lower())
        self.assertIn("appraising condition", out.lower())
        self.assertNotIn("18-3-402", out)
        self.assertNotIn("(", out)
        self.assertNotIn(")", out)
        self.assertEqual(
            out,
            "Attempted sexual assault — victim incapable of appraising condition",
        )

    def test_always_regular_case_not_all_caps(self):
        """LUIS ELADIO ALMONTE-style: never leave ALL CAPS on the card."""
        raw = (
            "01/17/2006; SEX OFFENSE, OTHER STATE (SEXUAL MISCONDUCT); 18866C-2005; "
            "Bronx, NY; Guilty/convict; Commission of OR Attempt, Solicit, or "
            "Conspire to Commit; Chapter 794; Sexual Battery *Excluding subsections "
            "794.011(10)"
        )
        out = summarize_crime(raw)
        self.assertTrue(out)
        # No long run of 4+ consecutive uppercase letters (ALL CAPS words)
        self.assertIsNone(
            re.search(r"\b[A-Z]{4,}\b", out),
            msg=f"still has ALL CAPS token: {out!r}",
        )
        self.assertIn("sexual", out.lower())
        self.assertNotEqual(out, out.upper())

    def test_to_regular_case_helper(self):
        from scraper.crime_summary_clause import to_regular_case

        self.assertEqual(
            to_regular_case("SEX OFFENSE, OTHER STATE — SEXUAL MISCONDUCT"),
            "Sex Offense, Other State — Sexual Misconduct",
        )
        self.assertEqual(to_regular_case("Sexual battery"), "Sexual battery")

    def test_fl_cf_case_number_not_in_description(self):
        """ROGELIO DELEON: 23-CF-017184 must not appear as '23-Cf' on cards."""
        raw = (
            "10/15/2024; Lewd or lascivious conduct victim under 16 years old "
            "by offender 18 years or older; F.S. 800.04(6)(b (2 Counts); "
            "23-CF-017184; Lee, FL; Guilty/convict; Lewd or lascivious conduct "
            "victim under 16 years old by offender 18 years or older; "
            "F.S. 800.04(6)(b; 2317184; Commission of OR Attempt, Solicit, or "
            "Conspire to Commit"
        )
        out = summarize_crime(raw)
        self.assertIn("under 16", out.lower())
        self.assertNotIn("23-cf", out.lower())
        self.assertNotIn("23-Cf", out)
        self.assertNotIn("017184", out)
        self.assertNotIn("2317184", out)
        self.assertNotIn("800.04", out)
        # Export card path must not fall back to raw title-cased dump
        from gui_app.shared.export_card_fields import crime

        card = crime(
            {
                "crime": raw,
                "first_name": "ROGELIO",
                "last_name": "DELEON",
            }
        )
        self.assertNotIn("23-cf", card.lower())
        self.assertNotIn("23-Cf", card)
        self.assertNotIn("cf-", card.lower())

    def test_francisco_alvarado_ca_pc_statutes_not_exported(self):
        """VA multi-state dump: CA 647.6 PC / 314.1 PC and VA 18.2-* must not ship."""
        raw = (
            "18.2-472.1 - VIOLENT SEX OFFENDER FAIL TO REGISTER; "
            "18.2-370 - TAKING INDECENT LIBERTIES WITH CHILDREN; "
            "647.6 PC - ANNOY / MOLEST CHILDREN; "
            "314.1 PC - INDECENT EXPOSURE"
        )
        out = summarize_crime(raw)
        self.assertEqual(
            out,
            "Fail to register · Indecent liberties · Annoy/molest children · Indecent exposure",
        )
        self.assertNotIn("647", out)
        self.assertNotIn("314", out)
        self.assertNotIn("18.2", out)
        self.assertNotRegex(out, r"(?i)\bpc\b")
        self.assertNotRegex(out, r"\d")

    def test_bare_offense_code_token_stripped(self):
        """TX-style alphanumeric booking/offense codes must not appear on cards."""
        out = summarize_crime("361411a2 SEXUAL ASSAULT OF A CHILD")
        self.assertEqual(out, "Sexual assault")
        self.assertNotIn("361411", out)
        out2 = summarize_crime("21.11 INDECENCY WITH A CHILD")
        self.assertEqual(out2, "Indecency with a child")
        self.assertNotIn("21.11", out2)


if __name__ == "__main__":
    unittest.main()
