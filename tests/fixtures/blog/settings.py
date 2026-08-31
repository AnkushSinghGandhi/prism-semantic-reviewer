# Fixture settings — Pryti only parses this (never imports/runs it).
# REST_FRAMEWORK is present but sets no DEFAULT_PERMISSION_CLASSES, so DRF's default is AllowAny:
# views without an explicit permission_classes should resolve to "open".
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
}
