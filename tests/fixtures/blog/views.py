import threading

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import User, Order, OrderItem
from .services import PaymentService, CheckoutService


def notify(user):
    pass


class CreateOrder(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order = Order.objects.create(total=100)          # E3: direct write
        OrderItem.insert_ordered_item(order_id=order.id)  # E3: write via model helper (follow)
        PaymentService().charge(100)                      # E4: external via service (follow)
        user = User.objects.get(id=request.user.id)       # E3: read + E6: PII source (User)
        _ = user.email                                    # E6: sensitive field read
        threading.Thread(target=notify, args=(user,)).start()  # E5: async dispatch
        return order


class PublicStats(APIView):
    # no permission_classes → should resolve to DRF default (AllowAny / open)
    def get(self, request):
        return Stats.objects.all()                        # E3: read, open endpoint


class OpenWrite(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return Order.objects.create(total=0)              # unauthenticated DB write (🔴)


class Checkout(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order = Order.objects.get(id=request.data["id"])  # var typed as Order
        order.total = 0
        order.save()                                      # → Order:write (not <instance>)
        CheckoutService().complete(order)                 # depth-2: → _notify() → webhook
        return order
