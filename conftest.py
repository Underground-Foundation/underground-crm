import pytest

@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """
    Automatically enables database access for all tests.
    This resolves pytest-django's strict restriction which ordinarily
    blocks DB access for unittest.TestCase classes unless marked.
    """
    pass
