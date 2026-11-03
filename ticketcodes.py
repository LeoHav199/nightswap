"""Read the QR codes and barcodes printed on an uploaded ticket.

The same ticket can be saved as a different file (re-exported PDF, a
screenshot), so comparing file hashes alone doesn't catch a ticket being
sold twice. Comparing the code inside the ticket does.
"""
import hashlib
import math
import struct

# OpenCV and NumPy are imported inside the functions: they're heavy, and loading them before the
# web server starts its worker processes can crash those workers.

MAX_PDF_PAGES = 10
# #14: a small file can claim to be a gigantic picture ("decompression bomb"); decoding it would use more memory
# than the server has (512 MB on Render's Starter plan). Phone screenshots are around 3-4 million pixels.
MAX_PIXELS = 25_000_000
MAX_SIDE = 10_000
PDF_SCALE = 2.5  # render PDF pages at 2.5x (180 dpi) to read small codes


def image_size(data, ext):
    """(width, height) from a PNG or JPEG header without decoding the picture, or None if it can't be read."""
    if ext == "png":
        if len(data) >= 24 and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        return None
    i = 2  # JPEG: walk the markers up to the frame header (SOFn), which holds the size
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD9:
            i += 1 if marker == 0xFF else 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def check(data, ext):
    """None when the file is safe to decode, otherwise why not: 'too_big' or 'too_many_pages'."""
    if ext == "pdf":
        import pypdfium2 as pdfium
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:
            return None  # unreadable: codes() finds nothing and the upload is judged on the file alone
        try:
            return "too_many_pages" if len(pdf) > MAX_PDF_PAGES else None
        finally:
            pdf.close()
    size = image_size(data, ext)
    if not size or not all(size):
        return "too_big"  # no readable size: don't hand it to the decoder
    width, height = size
    return "too_big" if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS else None


def _images(data, ext):
    import cv2
    import numpy as np
    if check(data, ext):
        return
    if ext == "pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            for i in range(min(len(pdf), MAX_PDF_PAGES)):
                page = pdf[i]
                w, h = page.get_size()  # in points (1/72 inch); scale 1 = 1 pixel per point
                scale = min(PDF_SCALE, math.sqrt(MAX_PIXELS / max(w * h, 1)))
                bitmap = page.render(scale=scale, grayscale=True)
                yield np.ascontiguousarray(bitmap.to_numpy())
        finally:
            pdf.close()
    else:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            yield img


def _decode(img):
    """Returns [(text, format)] where format is 'QR' or an OpenCV barcode type such as 'CODE_128'."""
    import cv2

    found = []
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for candidate in (img, cv2.resize(img, None, fx=0.5, fy=0.5)):
        ok, texts, _, _ = cv2.QRCodeDetector().detectAndDecodeMulti(candidate)
        if ok:
            found += [(t, "QR") for t in texts if t]
        if found:
            break
    if not found and hasattr(cv2, "barcode"):
        try:
            ok, texts, kinds, _ = cv2.barcode.BarcodeDetector().detectAndDecodeWithType(img)
        except Exception:
            ok, texts, kinds = False, [], []
        if ok:
            found += [(t, k) for t, k in zip(texts, kinds) if t]
    return found


def codes(data, ext):
    """Returns the distinct (text, format) codes found in the ticket, in order. Empty if none could be read."""
    seen, out = set(), []
    try:
        for img in _images(data, ext):
            for text, kind in _decode(img):
                if text not in seen:
                    seen.add(text)
                    out.append((text, kind))
    except Exception:
        return []
    return out


PHASH_SIZE = 16          # #29: average hash of a 16x16 grayscale thumbnail = 256 bits
PHASH_MATCH = 6          # at most this many different bits counts as the same picture (re-saved, resized, recompressed)


def phash(data, ext):
    """Perceptual hash of an image (or a PDF's first page) as 64 hex characters, or None. Unlike a file hash it
    survives re-saving, resizing and recompression, so a re-saved screenshot of a listed ticket is still caught."""
    import numpy as np
    import cv2
    try:
        img = next(iter(_images(data, ext)), None)
    except Exception:
        img = None
    if img is None:
        return None
    small = cv2.resize(img, (PHASH_SIZE, PHASH_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    if small.std() < 2:  # an (almost) blank picture: every blank picture would match every other
        return None
    bits = (small > small.mean()).flatten()
    return "%064x" % int("".join("1" if b else "0" for b in bits), 2)


def phash_distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def code_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()
