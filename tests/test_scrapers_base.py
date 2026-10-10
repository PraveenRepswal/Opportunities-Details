"""Tests for shared scraper utilities and backward compatibility with scraper classes."""
from scrapers.base import (
    normalize_url,
    slugify_links,
    jaccard,
    extract_content_and_name,
    deduplicate_urls,
)
from scrapers.greatyop import GreatYopScraper
from scrapers.scholars4dev import Scholars4Dev
from scrapers.scholarshipscorner import ScholarshipsCorner
from scrapers.youthop import YouthOP
from scrapers.opportunitiescorner import OpportunitiesCorners
from scrapers.opportunitiesforyouth import OpportunitiesForYouth


def test_normalize_url():
    assert normalize_url("HTTPS://Example.COM/Path/To/Page/") == "https://example.com/path/to/page"
    assert normalize_url("http://example.com/item") == "http://example.com/item"


def test_slugify_links():
    assert slugify_links("https://example.com/post/awesome-internship-2026") == "awesome-internship"
    assert slugify_links("https://example.com/post/fellowship-program-2026-05-12") == "fellowship-program"
    assert slugify_links("https://example.com/post/simple-scholarship") == "simple-scholarship"


def test_jaccard():
    tokens_a = {"erasmus", "mundus", "scholarship"}
    tokens_b = {"erasmus", "mundus", "fellowship"}
    # intersection: {"erasmus", "mundus"} (2)
    # union: {"erasmus", "mundus", "scholarship", "fellowship"} (4)
    assert jaccard(tokens_a, tokens_b) == 0.5
    assert jaccard(set(), set()) == 0.0


def test_deduplicate_urls():
    links = [
        "https://example.com/opportunities/scholarship-2026/",
        "https://example.com/opportunities/scholarship-2026",
        "https://example.com/opportunities/scholarship-2025",
        "https://example.com/opportunities/unrelated-fellowship",
    ]
    unique, duplicates = deduplicate_urls(links, threshold=0.7)
    assert "https://example.com/opportunities/scholarship-2026" in unique
    assert len(unique) == 2
    assert len(duplicates) == 2


def test_extract_content_and_name():
    html_page = "<html><body><h1>Tech Fellowship</h1><p>This is a great global fellowship for developers.</p></body></html>"
    result = extract_content_and_name(html_page, "https://example.com/tech-fellowship-2026")
    assert result is not None
    assert result["name"] == "tech fellowship"
    assert "great global fellowship" in result["content"]


def test_scraper_classes_backward_compatibility():
    classes = [
        GreatYopScraper,
        Scholars4Dev,
        ScholarshipsCorner,
        YouthOP,
        OpportunitiesCorners,
        OpportunitiesForYouth,
    ]
    test_link = "https://example.com/some-scholarship-2026/"
    for cls in classes:
        # Verify staticmethod backward compatibility
        assert cls.normalize_url(test_link) == "https://example.com/some-scholarship-2026"
        assert cls.slugify_links(test_link) == "some-scholarship"
        assert cls.jaccard({"a"}, {"a"}) == 1.0


def test_is_english_content():
    from backend.text_utils import is_english_content

    # French scholarship content
    fr_title = "10 Bourses Etudes Entierement Financees"
    fr_content = "Cette bourse d'etudes s'adresse aux etudiants internationaux pour l'annee universitaire 2026."
    assert is_english_content(fr_title, fr_content) is False

    # English scholarship content
    en_title = "Top 10 Fully Funded Scholarships"
    en_content = "Here is the list of top 10 fully funded scholarships for undergraduate and graduate studies."
    assert is_english_content(en_title, en_content) is True

    # International title in English
    intl_title = "DAAD Postdoctoral Fellowship Germany"
    intl_content = "The German Academic Exchange Service provides monthly stipends and travel allowances."
    assert is_english_content(intl_title, intl_content) is True

    # Empty content should not crash
    assert is_english_content("", "") is True

