"""Tests for the hybrid metadata extractor (rules + SLM profiling)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.metadata_extractor import (
    extract_metadata_rules,
    find_missing_fields,
    build_fallback_profile,
    format_profile_for_vectorstore,
    is_slm_available,
    extract_profile_with_llamacpp,
    get_or_extract_profile,
)
from backend.schemas import OpportunityProfile
from backend.text_utils import (
    clean_opportunity_title,
    extract_lead_summary,
    normalize_text,
    split_into_sentences,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


class TestOpportunityProfileSchema:
    def test_valid_profile_creation(self):
        prof = OpportunityProfile(
            title="DAAD Postgraduate Studies",
            organization="DAAD",
            location="Germany",
            deadline="2026-10-15",
            type="Fully Funded Scholarship",
            eligibility="Graduates with a bachelor's degree",
            benefits="€934/month stipend, travel, insurance",
            summary="Scholarship for postgraduate study in Germany.",
        )
        assert prof.title == "DAAD Postgraduate Studies"
        assert prof.deadline == "2026-10-15"
        assert "€934" in prof.benefits

    def test_schema_json_generation(self):
        schema = OpportunityProfile.model_json_schema()
        assert schema["type"] == "object"
        assert "title" in schema["properties"]
        assert "summary" in schema["properties"]
        assert "deadline" in schema["properties"]


class TestFallbackProfile:
    def test_build_fallback_profile_extracts_summary_and_metadata(self):
        raw_text = (
            "The CERN Summer Student Programme offers students an exceptional chance to join researchers. "
            "It runs for 8 to 13 weeks in Geneva. Applications are open to physics students worldwide. "
            "Youth Opportunities invites you to join our channel: https://m.me/youthop"
        )
        metadata = {
            "organization": "CERN",
            "location": "Geneva, Switzerland",
            "deadline": "2026-01-31",
            "type": "Internship",
        }
        prof = build_fallback_profile("CERN Summer Programme", raw_text, metadata)

        assert prof.title == "CERN Summer Programme"
        assert prof.organization == "CERN"
        assert prof.location == "Geneva, Switzerland"
        assert prof.deadline == "2026-01-31"
        assert "CERN Summer Student Programme" in prof.summary
        assert "m.me" not in prof.summary  # Promotional noise stripped


class TestFormatProfileForVectorstore:
    def test_format_contains_all_dense_fields(self):
        prof = OpportunityProfile(
            title="Max Planck Fellowship",
            organization="Max Planck Institute",
            location="Germany",
            deadline="2026-11-01",
            type="Fellowship",
            eligibility="PhD holders in natural sciences",
            benefits="Full salary according to TVöD",
            summary="Postdoctoral fellowship in scientific research.",
        )
        text = format_profile_for_vectorstore(prof, url="https://example.com/apply")

        assert "Title: Max Planck Fellowship" in text
        assert "Organization: Max Planck Institute" in text
        assert "Location: Germany" in text
        assert "Deadline: 2026-11-01" in text
        assert "Link: https://example.com/apply" in text
        assert "Summary: Postdoctoral fellowship" in text
        assert "Eligibility: PhD holders" in text
        assert "Benefits: Full salary" in text


@pytest.mark.anyio
class TestSLMExtractorAPI:
    async def test_is_slm_available_returns_false_when_offline(self):
        offline = await is_slm_available("http://127.0.0.1:59999")
        assert offline is False

    async def test_extract_with_llamacpp_parses_json_response(self):
        mock_response_data = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "title": "Stanford Knight-Hennessy Scholars",
                            "organization": "Stanford University",
                            "location": "United States",
                            "deadline": "2026-10-08",
                            "type": "Fully Funded Scholarship",
                            "eligibility": "Applying to a full-time graduate degree at Stanford",
                            "benefits": "Full tuition, living stipend, and academic expenses",
                            "summary": "Multidisciplinary scholarship program for graduate students at Stanford.",
                        })
                    }
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            prof = await extract_profile_with_llamacpp("Stanford Scholars", "raw content", server_url="http://localhost:8080")
            assert prof is not None
            assert prof.title == "Stanford Knight-Hennessy Scholars"
            assert prof.organization == "Stanford University"
            assert prof.deadline == "2026-10-08"

    async def test_get_or_extract_profile_falls_back_when_server_offline(self):
        raw_item = {
            "name": "Fallback Test Grant",
            "content": "This is a grant for climate researchers. It provides up to $50,000 for field experiments.",
            "metadata": {"organization": "Earth Foundation", "location": "Global", "type": "Grant"},
        }
        with patch("backend.metadata_extractor.is_slm_available", new_callable=AsyncMock, return_value=False):
            prof = await get_or_extract_profile(raw_item, backend="llamacpp")
            assert prof is not None
            assert prof.title == "Fallback Test Grant"
            assert prof.organization == "Earth Foundation"
            assert "grant for climate researchers" in prof.summary.lower()


class TestDatabaseProfileJSON:
    def test_db_profile_roundtrip(self, tmp_path):
        import sqlite3
        from backend.database import _ensure_opportunities_schema, _parse_opportunity_row

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)

        profile_data = {
            "title": "DB Test Scholarship",
            "organization": "Test Org",
            "location": "France",
            "deadline": "2026-12-01",
            "type": "Scholarship",
            "summary": "A test scholarship in France.",
        }

        cursor.execute(
            """
            INSERT INTO opportunities (title, content, source, url, content_hash, created_at, profile_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Test Title", "Test Content", "portal", "https://example.com", "hash123", "2026-09-28", json.dumps(profile_data)),
        )

        cursor.execute("SELECT id, title, content, source, url, created_at, metadata_json, deadline, profile_json FROM opportunities WHERE id = 1")
        row = cursor.fetchone()
        parsed = _parse_opportunity_row(row)

        assert parsed["profile"] is not None
        assert parsed["profile"]["title"] == "DB Test Scholarship"
        assert parsed["profile"]["location"] == "France"

    def test_update_profile_and_metadata_syncs_fields(self, tmp_path, monkeypatch):
        test_db = tmp_path / "test_opps.db"
        monkeypatch.setattr("backend.database.DB_PATH", str(test_db))

        from backend.database import (
            _ensure_opportunities_schema,
            _parse_opportunity_row,
            update_opportunity_profile_and_metadata,
            get_db_connection,
        )

        with get_db_connection() as conn:
            cursor = conn.cursor()
            _ensure_opportunities_schema(cursor)
            cursor.execute(
                """
                INSERT INTO opportunities (title, content, source, url, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("Sync Test Opp", "Content for sync test", "test", "http://test", "synchash999", "2026-09-29"),
            )
            opp_id = cursor.lastrowid
            conn.commit()

        new_profile = {
            "title": "Sync Test Opp",
            "organization": "European Commission",
            "location": "Brussels, Belgium",
            "deadline": "2026-11-15",
            "type": "Fellowship",
            "eligibility": "PhD researchers",
            "benefits": "€4,000/month",
            "summary": "Marie Curie Fellowship.",
        }

        success = update_opportunity_profile_and_metadata(opp_id, new_profile)
        assert success is True

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, title, content, source, url, created_at, metadata_json, deadline, profile_json FROM opportunities WHERE id = ?",
                (opp_id,),
            )
            row = cursor.fetchone()
            parsed = _parse_opportunity_row(row)

            # Verify profile_json was populated
            assert parsed["profile"] is not None
            assert parsed["profile"]["organization"] == "European Commission"
            # Verify metadata_json was synchronized with the same fields!
            assert parsed["metadata"] is not None
            assert parsed["metadata"]["organization"] == "European Commission"
            assert parsed["metadata"]["location"] == "Brussels, Belgium"
            assert row["deadline"] == "2026-11-15"


class TestTextUtils:
    def test_normalize_text_smart_punctuation_and_mojibake(self):
        raw = "McDonald\u2019s announced \u201cShe Leads\u201d fellowship (2027\u20132028)\u00a0in Africa."
        normalized = normalize_text(raw)
        assert normalized == "McDonald's announced \"She Leads\" fellowship (2027-2028) in Africa."

        corrupted = "McDonald\ufffds Chicago Grant for 2027\ufffd2028."
        cleaned = normalize_text(corrupted)
        assert cleaned == "McDonald's Chicago Grant for 2027-2028."

    def test_split_into_sentences_preserves_abbreviations(self):
        text = (
            "The U.S. Department of State and Dr. Smith announced the fellowship in Geneva, Switzerland. "
            "Applications for the Ph.D. program (stipend $1.5M total) close on Jan. 15, 2027. "
            "Visit website for details."
        )
        sents = split_into_sentences(text)
        assert len(sents) == 3
        assert sents[0] == "The U.S. Department of State and Dr. Smith announced the fellowship in Geneva, Switzerland."
        assert sents[1] == "Applications for the Ph.D. program (stipend $1.5M total) close on Jan. 15, 2027."
        assert sents[2] == "Visit website for details."

    def test_clean_opportunity_title_preserves_acronyms_and_mixed_case(self):
        assert (
            clean_opportunity_title("CALL FOR CERN SUMMER STUDENT PROGRAMME IN SWITZERLAND")
            == "CERN Summer Student Programme in Switzerland"
        )
        assert (
            clean_opportunity_title("The International Physics Competition (IPhyC) 2026")
            == "The International Physics Competition (IPHYC) 2026"
        )
        assert (
            clean_opportunity_title("Youth Leadership Certification USA 2027")
            == "Youth Leadership Certification USA 2027"
        )
        assert (
            clean_opportunity_title("McDonald's Community Development Grants")
            == "McDonald's Community Development Grants"
        )

    def test_extract_lead_summary_sentence_bounded(self):
        raw = (
            "McDonald's Chicago Community Impact Grants Program 2027 is now open for applications. "
            "Through this grant cycle, McDonald's is committing $4 million to support local nonprofits. "
            "Selected organizations can receive project-based grants ranging from $50,000 to $200,000. "
            "Youth Opportunities invites you to join our channel: https://m.me/youthop"
        )
        summary = extract_lead_summary(raw, max_sentences=2, max_chars=350)
        assert summary.endswith((".", "!", "?"))
        assert "https://m.me" not in summary
        assert "McDonald's Chicago Community Impact Grants Program 2027 is now open for applications." in summary
        assert "committing $4 million" in summary

