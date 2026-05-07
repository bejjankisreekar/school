from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.customers"
    label = "customers"
    verbose_name = "Customers (Schools/Tenants)"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from apps.core.subscription_access import invalidate_school_feature_cache

        def _invalidate_subscription(sender, instance, **kwargs):
            sid = getattr(instance, "school_id", None)
            if sid:
                invalidate_school_feature_cache(int(sid))

        from apps.customers.models import SchoolFeatureAddon, SchoolSubscription

        post_save.connect(_invalidate_subscription, sender=SchoolSubscription)
        post_delete.connect(_invalidate_subscription, sender=SchoolSubscription)
        post_save.connect(_invalidate_subscription, sender=SchoolFeatureAddon)
