import io

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage


class WebPConvertingStorage(FileSystemStorage):
    """Converts JPEG/PNG/etc. image uploads to WebP on save.
    Falls back to the original format if the file is not a
    recognised image (e.g. a PDF accidentally sent to an ImageField).
    """

    QUALITY = 82  # 0-100; good balance between size and visual quality

    def _save(self, name, content):
        try:
            from PIL import Image

            img = Image.open(content)
            img.load()  # force full decode before rewinding content
            # WebP supports RGBA (transparency); convert other modes safely
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if img.mode in ("P", "LA") else "RGB")
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=self.QUALITY, method=4)
            out.seek(0)
            base = name.rsplit(".", 1)[0] if "." in name else name
            return super()._save(base + ".webp", ContentFile(out.read()))
        except Exception:
            # Not a recognised image (PDF, corrupt file, etc.) — save as-is
            try:
                content.seek(0)
            except Exception:
                pass
            return super()._save(name, content)


webp_storage = WebPConvertingStorage()
