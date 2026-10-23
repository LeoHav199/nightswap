"""Read the QR codes and barcodes printed on an uploaded ticket.

The same ticket can be saved as a different file (re-exported PDF, a
screenshot), so comparing file hashes alone doesn't catch a ticket being
sold twice. Comparing the code inside the ticket does.
"""
import hashlib

# OpenCV and NumPy are imported inside the functions: they're heavy, and loading them before the
# web server starts its worker processes can crash those workers.

MAX_PDF_PAGES = 6


def _images(data, ext):
    import cv2
    import numpy as np
    if ext == "pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            for i in range(min(len(pdf), MAX_PDF_PAGES)):
                bitmap = pdf[i].render(scale=2.5, grayscale=True)
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


def code_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()
