import io
import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from apps.blog.models import Post
from apps.trips.models import BuyRequest, Message, Payment, RequestItem

FIELDS = [
    (BuyRequest, ["custom_fare_proof", "reshipment_cost_proof", "reshipment_proof"]),
    (RequestItem, ["photo", "purchase_photo"]),
    (Payment, ["proof"]),
    (Message, ["photo"]),
    (Post, ["cover_image"]),
]

QUALITY = 82


def convert_field(obj, field_name):
    from PIL import Image

    field = getattr(obj, field_name)
    if not field or not field.name:
        return False
    if field.name.endswith(".webp"):
        return False

    try:
        field.open("rb")
        data = field.read()
        field.close()
    except Exception as e:
        print(f"  SKIP (read error): {field.name} — {e}")
        return False

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if img.mode in ("P", "LA") else "RGB")
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=QUALITY, method=4)
        out.seek(0)
    except Exception as e:
        print(f"  SKIP (convert error): {field.name} — {e}")
        return False

    old_path = field.path
    base = field.name.rsplit(".", 1)[0]
    new_name = base + ".webp"

    try:
        field.save(new_name.split("/")[-1], ContentFile(out.read()), save=False)
        setattr(obj, field_name, new_name)
        obj.save(update_fields=[field_name])
        if os.path.exists(old_path) and old_path != field.path:
            os.remove(old_path)
        print(f"  OK: {old_path.split('media/')[-1]} → {new_name}")
        return True
    except Exception as e:
        print(f"  SKIP (save error): {field.name} — {e}")
        return False


class Command(BaseCommand):
    help = "Convert all existing non-WebP image uploads to WebP in-place."

    def handle(self, *args, **options):
        total, converted = 0, 0
        for Model, fields in FIELDS:
            for obj in Model.objects.all():
                for field_name in fields:
                    total += 1
                    if convert_field(obj, field_name):
                        converted += 1
        self.stdout.write(f"\nDone: {converted}/{total} images converted to WebP.")
