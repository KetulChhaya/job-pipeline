import pytest

from filters import classify_location


@pytest.mark.parametrize("location,country,expected", [
    ("Bengaluru, Karnataka, IN", "", "non_us"),
    ("Remote, Israel", "", "non_us"),
    ("London or Remote", "", "non_us"),
    ("Remote - Portugal", "", "non_us"),
    ("Romania Remote (2 Locations)", "", "non_us"),
    ("Toronto, ON, Canada", "", "non_us"),
    ("Toronto, CA", "", "non_us"),
    ("Berlin, DE", "", "non_us"),
    ("Remote, Global", "", "ambiguous"),
    ("Remote", "", "ambiguous"),
    ("", "", "ambiguous"),
    ("USAVAReston (2 Locations)", "", "ambiguous"),
    ("Seattle, WA", "", "us"),
    ("Remote (US)", "", "us"),
    ("San Francisco, Remote", "", "us"),
    ("New York", "", "us"),
    ("Albuquerque, New Mexico", "", "us"),
    ("Dublin, CA", "", "us"),
    ("Vancouver, Washington, United States of America", "", "us"),
    ("Vancouver, BC", "", "non_us"),
    ("Portland, OR", "", "us"),
    ("Menlo Park, CA; New York, NY", "", "us"),
    ("San Francisco, CA; London, UK", "", "us"),
    ("US, CA, Santa Clara, US CA Santa Clara", "", "us"),
    ("Remote (Canada or US)", "", "us"),
    ("Texas, USA - Remote, Texas USA   Remote", "", "us"),
    ("O'Fallon, Missouri, OFallon Missouri", "", "us"),
    ("Toronto Canada, San Francisco, Remote in US, Remote in Canada", "", "us"),
    ("US, Canada", "", "us"),
    ("Remote - US & Canada", "", "us"),
    ("Remote (United States, Canada)", "", "us"),
    ("New York, NY, Toronto, ON", "", "us"),
    ("Athens, Georgia", "", "us"),
    ("Tel Aviv, IL", "", "non_us"),
    ("Durham, England", "", "non_us"),
    # structured country always wins over the text
    ("Remote, Remote", "US | EU", "us"),
    ("Remote", "Germany", "non_us"),
    ("Remote", "Israel", "non_us"),
    ("Tel Aviv", "us", "us"),
    ("Anywhere", "United States of America", "us"),
    ("Santa Clara", "in", "non_us"),
])
def test_classify_location(prof, location, country, expected):
    assert classify_location(location, country, prof) == expected
