from django.test import SimpleTestCase, TestCase


class HealthTests(TestCase):
    def test_health(self):
        response = self.client.get("/core/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "claus-core",
                "backend": "django",
                "api": "drf",
            },
        )


class CorsPreflightTests(SimpleTestCase):
    def preflight(self, origin):
        return self.client.options(
            "/core/health/",
            headers={"origin": origin, "access-control-request-method": "GET"},
        )

    def test_allowed_origin_receives_cors_headers(self):
        response = self.preflight("http://127.0.0.1:3000")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:3000")
        self.assertIn("GET", response.headers["Access-Control-Allow-Methods"].split(", "))
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)

    def test_disallowed_origin_gets_no_cors_headers(self):
        response = self.preflight("http://evil.example")

        # django-cors-headers answers every preflight; only the CORS headers differ.
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertNotIn("Access-Control-Allow-Methods", response.headers)
