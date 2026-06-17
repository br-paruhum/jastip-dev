from django.db import migrations


HOUSE_PROMOS = [
    {
        "title": "Travelling soon?",
        "body": "Earn from your spare luggage — list a travel plan and let buyers order through you.",
        "url": "/how-to/",
        "cta_label": "See how it works",
        "badge": "For travelers",
        "order": 1,
    },
    {
        "title": "New to ProxyBuying?",
        "body": "Learn how proxy purchasing works, step by step, before you place your first order.",
        "url": "/how-to/",
        "cta_label": "Learn more",
        "badge": "Guide",
        "order": 2,
    },
]


def seed_promos(apps, schema_editor):
    Promo = apps.get_model("pages", "Promo")
    if Promo.objects.exists():
        return
    for data in HOUSE_PROMOS:
        Promo.objects.create(**data)


def unseed_promos(apps, schema_editor):
    Promo = apps.get_model("pages", "Promo")
    Promo.objects.filter(title__in=[p["title"] for p in HOUSE_PROMOS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0002_promo"),
    ]

    operations = [
        migrations.RunPython(seed_promos, unseed_promos),
    ]
