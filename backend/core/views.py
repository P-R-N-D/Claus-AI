from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import HealthSerializer


@api_view(["GET"])
def health(_request):
    serializer = HealthSerializer(
        {
            "status": "ok",
            "service": "claus-core",
            "backend": "django",
            "api": "drf",
        }
    )
    return Response(serializer.data)
