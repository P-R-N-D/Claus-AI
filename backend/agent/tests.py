import json

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from config.asgi import application


async def asgi_get(path: str) -> tuple[int, dict[str, str], bytes]:
    communicator = ApplicationCommunicator(
        application,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
    )
    await communicator.send_input({"type": "http.request", "body": b""})
    start = await communicator.receive_output()
    body = bytearray()
    while True:
        message = await communicator.receive_output()
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await communicator.wait()
    return start["status"], dict(start["headers"]), bytes(body)


class AsgiRoutingTests(SimpleTestCase):
    async def test_integrated_routing_contract(self):
        core_status, _, core_body = await asgi_get("/core/health/")
        agent_status, _, agent_body = await asgi_get("/agent/health/")
        openapi_status, _, _ = await asgi_get("/agent/openapi.json")
        legacy_status, _, _ = await asgi_get("/api/health/")

        self.assertEqual(core_status, 200)
        self.assertEqual(
            json.loads(core_body),
            {
                "status": "ok",
                "service": "claus-core",
                "backend": "django",
                "api": "drf",
            },
        )
        self.assertEqual(agent_status, 200)
        self.assertEqual(
            json.loads(agent_body),
            {
                "status": "ok",
                "service": "claus-agent",
                "backend": "fastapi",
            },
        )
        self.assertEqual(openapi_status, 200)
        self.assertEqual(legacy_status, 404)
