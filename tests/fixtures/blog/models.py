from django.db import models


class User(models.Model):
    email = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)


class Order(models.Model):
    total = models.IntegerField()


class OrderItem(models.Model):
    order_id = models.IntegerField()

    @staticmethod
    def insert_ordered_item(**kwargs):
        # a DB write hidden inside a model helper — Prism should find this via one-hop follow
        OrderItem.objects.create(**kwargs)
