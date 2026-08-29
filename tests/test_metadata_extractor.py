"""Tests for the rules-based metadata extractor using realistic scraped content."""
import pytest

from backend.metadata_extractor import (
    extract_metadata_rules,
    find_missing_fields,
)

SLOVAK_TITLE = "slovak republic scholarship talented students"
SLOVAK_CONTENT = (
    "The Slovak Republic is now accepting applications for its Talented Foreign "
    "Students scholarship for the 2026/2027 academic year. Selected candidates can "
    "get up to \u20ac500 per month. The deadline has been extended to 8 September 2026 "
    "at 23:59 CEST. Accepted test dates from 1 January 2021 up to the application "
    "deadline. Applications must be submitted exclusively online via the official "
    "portal at scholarships.portalvs.sk."
)

HONJO_TITLE = "honjo foundation scholarship"
HONJO_CONTENT = (
    "The Honjo Foundation Scholarship to Study in Japan is offered for the fall and "
    "spring semesters. The scholarship is offered by the Honjo International "
    "Scholarship Foundation and opened to foreign students who will attend Japanese "
    "universities. Application deadline: October 31, 2026."
)

FULLY_FUNDED_CONTENT = (
    "Fully Funded Scholarships in Germany for international students. The DAAD "
    "scholarship is funded by the German Academic Exchange Service. Study in Germany "
    "at any public university. Application deadline: 15 March 2027."
)

INTERNSHIP_CONTENT = (
    "UNICEF Internship Programme 2026. The internship is offered by UNICEF and takes "
    "place at UNICEF headquarters. Apply by 30 November 2026."
)

NO_YEAR_DEADLINE_CONTENT = (
    "The Chevening Scholarship application window. Applications close on 15 March. "
    "Chevening is funded by the Foreign, Commonwealth & Development Office."
)


class TestDeadlineExtraction:
    def test_explicit_date_with_extension(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["deadline"] == "2026-09-08"

    def test_us_style_date(self):
        meta = extract_metadata_rules(HONJO_TITLE, HONJO_CONTENT)
        assert meta["deadline"] == "2026-10-31"

    def test_ignores_historical_dates_near_keywords(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["deadline"] != "2021-01-01"

    def test_no_year_resolves_to_future(self):
        meta = extract_metadata_rules("some fellowship", NO_YEAR_DEADLINE_CONTENT)
        assert meta["deadline"] is not None
        year, month = meta["deadline"].split("-")[0:2]
        assert int(year) >= 2026
        assert month == "03"

    def test_no_deadline_returns_none(self):
        meta = extract_metadata_rules("t", "An opportunity with no dates mentioned.")
        assert meta["deadline"] is None


class TestOrganizationExtraction:
    def test_offered_by_pattern(self):
        meta = extract_metadata_rules(HONJO_TITLE, HONJO_CONTENT)
        assert meta["organization"] == "Honjo International Scholarship Foundation"

    def test_funded_by_pattern(self):
        meta = extract_metadata_rules(
            "daad scholarship", FULLY_FUNDED_CONTENT
        )
        assert meta["organization"] == "German Academic Exchange Service"

    def test_title_heuristic_strips_stopwords(self):
        meta = extract_metadata_rules(
            "how to prepare chevening scholarship application",
            "General advice text without explicit funder mentions.",
        )
        assert meta["organization"] == "Chevening"

    def test_title_heuristic_basic(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["organization"] == "Slovak Republic"


class TestLocationExtraction:
    def test_study_in_pattern(self):
        meta = extract_metadata_rules(HONJO_TITLE, HONJO_CONTENT)
        assert meta["location"] == "Japan"

    def test_title_country_match_with_alias(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["location"] == "Slovakia"

    def test_content_head_match(self):
        meta = extract_metadata_rules(
            "some program", FULLY_FUNDED_CONTENT
        )
        assert meta["location"] == "Germany"


class TestTypeExtraction:
    def test_scholarship_category(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["type"] == "Scholarship"

    def test_fully_funded_combined(self):
        meta = extract_metadata_rules("x", FULLY_FUNDED_CONTENT)
        assert meta["type"] == "Fully Funded Scholarship"

    def test_internship_priority_over_scholarship(self):
        meta = extract_metadata_rules("x", INTERNSHIP_CONTENT)
        assert meta["type"] == "Internship"


class TestMissingFields:
    def test_all_present(self):
        meta = {
            "deadline": "2026-09-08",
            "organization": "X",
            "location": "Japan",
            "type": "Scholarship",
        }
        assert find_missing_fields(meta) == []

    def test_partial(self):
        meta = {"deadline": "2026-09-08", "organization": None}
        missing = find_missing_fields(meta)
        assert set(missing) == {"organization", "location", "type"}

    def test_none_metadata(self):
        assert find_missing_fields(None) == [
            "deadline", "organization", "location", "type",
        ]


class TestRealWorldSamples:
    """End-to-end sanity checks against actual scraped_data.txt entries."""

    def test_slovak_full_extraction(self):
        meta = extract_metadata_rules(SLOVAK_TITLE, SLOVAK_CONTENT)
        assert meta["deadline"] == "2026-09-08"
        assert meta["location"] == "Slovakia"
        assert meta["type"] == "Scholarship"
        assert meta["organization"]

    @pytest.mark.parametrize(
        "title,content",
        [
            ("bourses sbw berlin", "Les candidatures pour le programme de bourses SBW Berlin sont ouvertes."),
        ],
    )
    def test_never_crashes_on_edge_input(self, title, content):
        meta = extract_metadata_rules(title, content)
        assert set(meta.keys()) == {"deadline", "organization", "location", "type"}
