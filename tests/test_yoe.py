import pytest

from enrich import extract_yoe
from fetchers import html_to_text


@pytest.mark.parametrize("text,expected", [
    ("3+ years of experience building web services.", (3, None)),
    ("3-5 years of experience with Python.", (3, 5)),
    ("3–5 years of professional experience.", (3, 5)),
    ("2 to 4 years of experience in backend development.", (2, 4)),
    ("Three or more years of experience shipping software.", (3, None)),
    ("A minimum of 4 years of experience in software engineering.", (4, None)),
    ("At least 5 years of industry experience.", (5, None)),
    ("(6+ years) of professional experience", (6, None)),
    ("5+ years of experience overall, 2+ years with React.", (5, None)),
    ("Bachelor's degree or 4 year degree and 2+ years of experience.", (2, None)),
    ("10+ years of experience leading teams.", (10, None)),
    ("We were founded 10 years ago and serve 5 million users.", (None, None)),
    ("Strong experience with distributed systems.", (None, None)),
    ("", (None, None)),
])
def test_extract_yoe(text, expected):
    assert extract_yoe(text) == expected


def test_preferred_section_is_ignored():
    text = ("Minimum qualifications:\n4+ years of experience with Python\n"
            "Preferred qualifications:\n8+ years of experience\n")
    assert extract_yoe(text) == (4, None)


def test_required_section_after_preferred_counts_again():
    text = ("Preferred qualifications:\n8+ years of experience\n"
            "Minimum qualifications:\n4+ years of experience with Python\n")
    assert extract_yoe(text) == (4, None)


def test_inline_nice_to_have_is_ignored():
    assert extract_yoe("Nice to have: 8+ years of experience with Go.") == (None, None)


def test_html_to_text_handles_escaped_greenhouse_content():
    raw = ("&lt;h3&gt;Requirements&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;5+ years of experience"
           "&amp;nbsp;with Python&lt;/li&gt;&lt;li&gt;AT&amp;amp;T scale&lt;/li&gt;&lt;/ul&gt;")
    text = html_to_text(raw)
    assert text.splitlines() == ["Requirements", "5+ years of experience with Python",
                                 "AT&T scale"]
    assert extract_yoe(text) == (5, None)
