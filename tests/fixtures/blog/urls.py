from django.urls import path
from .views import CreateOrder, PublicStats, OpenWrite, Checkout

urlpatterns = [
    path("api/orders", CreateOrder.as_view(), name="create-order"),
    path("api/stats", PublicStats.as_view(), name="public-stats"),
    path("api/open-write", OpenWrite.as_view(), name="open-write"),
    path("api/checkout", Checkout.as_view(), name="checkout"),
]
