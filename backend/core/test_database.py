from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.database import database_config


class DatabaseConfigTests(SimpleTestCase):
    def test_missing_url_uses_sqlite(self):
        sqlite_name = Path("/tmp/claus.sqlite3")

        config = database_config(None, sqlite_name)

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], sqlite_name)

    def test_postgresql_url_is_parsed_and_query_is_preserved(self):
        config = database_config(
            "postgresql://user%40name:p%40ss@db.example.test:6543/"
            "claus%2Ddb?sslmode=require&channel_binding=require",
            Path("unused"),
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "claus-db")
        self.assertEqual(config["USER"], "user@name")
        self.assertEqual(config["PASSWORD"], "p@ss")
        self.assertEqual(config["HOST"], "db.example.test")
        self.assertEqual(config["PORT"], "6543")
        self.assertEqual(
            config["OPTIONS"],
            {"sslmode": "require", "channel_binding": "require"},
        )

    def test_postgres_alias_and_default_port_are_supported(self):
        config = database_config(
            "postgres://user:password@db.example.test/claus", Path("unused")
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["PORT"], "")

    def test_invalid_urls_fail_instead_of_falling_back(self):
        invalid_urls = (
            "mysql://user:password@db.example.test/claus",
            "postgresql:///claus",
            "postgresql://db.example.test",
            "postgresql://db.example.test/claus#fragment",
            "postgresql://db.example.test/claus?sslmode=require&sslmode=verify-full",
            "postgresql://db.example.test:invalid/claus",
            "postgresql://db.example.test/claus?invalid-query",
            "postgresql://user%ZZ:password@db.example.test/claus",
            "postgresql://db.example.test/claus?sslmode=%ZZ",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(ImproperlyConfigured):
                    database_config(url, Path("unused"))
