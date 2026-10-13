from app.core.config import Settings


def test_managed_database_urls_get_the_psycopg_driver():
    for given in ("postgres://u:p@db.example:25060/mda?sslmode=require",
                  "postgresql://u:p@db.example:25060/mda?sslmode=require"):
        s = Settings(database_url=given)
        assert s.database_url == "postgresql+psycopg://u:p@db.example:25060/mda?sslmode=require"
    assert Settings(database_url="postgresql+psycopg://x@h/d").database_url == "postgresql+psycopg://x@h/d"
