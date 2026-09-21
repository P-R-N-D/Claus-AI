from django.test import TestCase


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
