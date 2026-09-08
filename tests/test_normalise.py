"""Tests for hierarchy extraction.

The failure this guards against is quiet: a normaliser that flattens structure produces
plausible-looking text, stores it happily, and only shows up as bad retrieval scores on
Day 41 with no obvious cause. So the structure itself is asserted, not just the text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redline.normalise import (  # noqa: E402
    Section, _assign_parents, extract_ref, from_html, from_xml,
)


# --- reference detection -----------------------------------------------------
# Most hierarchy bugs are really ref-detection bugs, so these get tested directly.

def test_decimal_refs():
    assert extract_ref("3.2.1 Access Control") == "3.2.1"
    assert extract_ref("§ 5.4 Encryption") == "5.4"


def test_roman_and_lettered_refs():
    assert extract_ref("II.A. Scope") == "II.A"
    assert extract_ref("(a) recording of the period of each use") == "a"


def test_named_refs():
    assert extract_ref("Article 12 Record-keeping") == "12"
    assert extract_ref("Sec. 7 Definitions") == "7"


def test_single_number_refs():
    """Regression, Day 33: "3." returned None because the pattern demanded a decimal."""
    assert extract_ref("3. Information Security") == "3"
    assert extract_ref("7) Definitions") == "7"


def test_headings_without_refs():
    assert extract_ref("Executive Summary") is None
    assert extract_ref("") is None
    assert extract_ref(None) is None


# Regressions from the first real run against Federal Register documents. Each of these
# produced a WRONG ref, which is worse than no ref: a bad label silently corrupts the
# hierarchy, while a missing one only leaves a section unlabelled.

def test_words_are_not_roman_numerals():
    """"List of Subjects" yielded "Li"; "CFTC Appendix" yielded "C"."""
    assert extract_ref("List of Subjects") is None
    assert extract_ref("CFTC Appendix to Form PF") is None
    assert extract_ref("Civil Money Penalties") is None


def test_numbers_embedded_in_titles_are_not_refs():
    """"310-TELEMARKETING SALES RULE" yielded "310"; "54 PART 54" yielded "54"."""
    assert extract_ref("310-TELEMARKETING SALES RULE") is None
    assert extract_ref("2026 Annual Report") is None


def test_genuine_roman_refs_still_work():
    assert extract_ref("I. Discussion") == "I"
    assert extract_ref("III. Procedural and Other Matters") == "III"


# --- parent assignment -------------------------------------------------------

def test_parents_follow_nesting():
    secs = [
        Section(body="top", level=0, ordinal=0),
        Section(body="child", level=1, ordinal=1),
        Section(body="grandchild", level=2, ordinal=2),
        Section(body="second child", level=1, ordinal=3),
    ]
    _assign_parents(secs)
    assert [s.parent_ordinal for s in secs] == [None, 0, 1, 0]


def test_skipped_levels_attach_to_nearest_ancestor():
    """Real documents jump from level 0 to level 2. The parent must still be right."""
    secs = [
        Section(body="a", level=0, ordinal=0),
        Section(body="b", level=2, ordinal=1),
        Section(body="c", level=1, ordinal=2),
    ]
    _assign_parents(secs)
    assert [s.parent_ordinal for s in secs] == [None, 0, 0]


# --- XML ---------------------------------------------------------------------

# Shaped after a REAL Federal Register document, captured with scripts/inspect_xml.py.
# The earlier fixture in this file used SOURCE="HD1"/"HD2" and passed happily while the
# production parser produced a completely flat hierarchy — a fixture that does not look
# like the real input tests nothing.
XML = b"""<RULE>
 <PREAMB>
  <AGENCY>FEDERAL TRADE COMMISSION</AGENCY>
  <SUBJECT>Telemarketing Sales Rule Fees</SUBJECT>
  <HD SOURCE="HED">AGENCY:</HD><P>Federal Trade Commission.</P>
  <HD SOURCE="HED">ACTION:</HD><P>Final rule.</P>
 </PREAMB>
 <SUPLINF>
  <HD SOURCE="HED">SUPPLEMENTARY INFORMATION:</HD>
  <P>To comply with the Do-Not-Call Registry Fee Extension Act.</P>
  <HD SOURCE="HD1">Administrative Procedure Act</HD>
  <P>Under the Administrative Procedure Act, an agency may proceed.</P>
  <PART>
   <HD SOURCE="HED">PART 310-TELEMARKETING SALES RULE</HD>
   <REGTEXT TITLE="16" PART="310">
    <AMDPAR>1. The authority citation for part 310 continues to read.</AMDPAR>
   </REGTEXT>
   <SECTION>
    <SECTNO>&#167; 310.8</SECTNO>
    <SUBJECT>[Amended]</SUBJECT>
    <P>Removing "$82" and adding "$85" in its place.</P>
   </SECTION>
  </PART>
 </SUPLINF>
</RULE>"""


def test_xml_hierarchy_comes_from_nesting_not_attributes():
    """Regression, Day 33: SOURCE="HED" on 10 of 11 headings meant the old parser
    produced a uniformly flat tree while reporting success."""
    secs = from_xml(XML)
    levels = [s.level for s in secs]
    assert max(levels) > 0, f"hierarchy is flat: {levels}"
    assert len(set(levels)) >= 3, f"expected at least 3 distinct depths, got {sorted(set(levels))}"


def test_xml_subsection_sits_below_its_container_title():
    """SOURCE="HD1" is a subsection of the SUPLINF whose title is SOURCE="HED"."""
    secs = {s.heading: s for s in from_xml(XML) if s.heading}
    suplinf = secs["SUPPLEMENTARY INFORMATION:"]
    apa = secs["Administrative Procedure Act"]
    assert apa.level > suplinf.level
    assert apa.parent_ordinal == suplinf.ordinal


def test_xml_deeper_containers_produce_deeper_sections():
    secs = {s.heading: s for s in from_xml(XML) if s.heading}
    assert secs["PART 310-TELEMARKETING SALES RULE"].level > secs["AGENCY:"].level


def test_section_number_and_title_are_joined():
    """SECTNO and SUBJECT arrive as separate elements but form one heading."""
    headings = [s.heading for s in from_xml(XML) if s.heading]
    assert any(h.startswith("\u00a7 310.8") and "[Amended]" in h for h in headings)


def test_amendatory_paragraphs_are_captured_as_body():
    body = " ".join(s.body for s in from_xml(XML))
    assert "authority citation for part 310" in body


def test_paragraphs_are_not_stored_twice():
    """Taking text from containers AND their children duplicates every paragraph."""
    body = " ".join(s.body for s in from_xml(XML))
    assert body.count("Final rule.") == 1
    assert body.count("Do-Not-Call Registry") == 1


def test_levels_are_normalised_to_start_at_zero():
    """Absolute container depth is an artefact of the dialect; only relative depth means
    anything downstream."""
    assert min(s.level for s in from_xml(XML)) == 0


def test_malformed_xml_recovers_rather_than_raising():
    """A publisher's broken XML should degrade, not kill an unattended run."""
    secs = from_xml(b'<RULE><SUPLINF><HD SOURCE="HED">Broken</HD><P>text</P>')
    assert len(secs) == 1


# --- HTML --------------------------------------------------------------------

HTML = """<html><body><nav>skip me</nav><main>
<h1>3. Information Security</h1><p>Institutions shall maintain controls.</p>
<h2>3.2 Access Control</h2><p>Access shall be granted on least privilege.</p>
<h3>3.2.1 Review</h3><p>Access shall be reviewed quarterly.</p>
</main><footer>skip me too</footer></body></html>"""


def test_html_hierarchy_from_heading_levels():
    secs = from_html(HTML)
    assert [s.level for s in secs] == [0, 1, 2]
    assert [s.ref for s in secs] == ["3", "3.2", "3.2.1"]
    assert secs[2].parent_ordinal == 1


def test_html_navigation_and_footers_are_dropped():
    body = " ".join(s.body for s in from_html(HTML))
    assert "skip me" not in body
    assert "least privilege" in body
