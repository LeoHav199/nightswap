"""Import events from a web page.

Many venue, festival and ticket-shop pages describe their events for search
engines with schema.org "Event" data inside <script type="application/ld+json">.
This reads that data: name, date and times, venue, city, ticket link and a
short description. Images are deliberately ignored (they belong to the
organiser). Pages without this data need to be added by hand.
"""
import html
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime

MAX_BYTES = 4 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; SpareEventImport/1.0; +https://sparetickets.nl)"
LD_JSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
TAGS = re.compile(r"<[^>]+>")


class ImportError_(Exception):
    pass


def _check_host(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImportError_("Use a normal http(s) link.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise ImportError_("That website couldn't be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImportError_("That address isn't a public website.")


def fetch(url):
    """Returns the page HTML. Only public http(s) sites; follows a few redirects, each checked."""
    for _ in range(4):
        _check_host(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                url = urllib.parse.urljoin(url, exc.headers["Location"])
                continue
            raise ImportError_("The website answered with an error (%s)." % exc.code)
        except Exception:
            raise ImportError_("The website didn't respond.")
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ImportError_("That page is too large.")
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")
    raise ImportError_("Too many redirects.")


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "subEvent", "event", "events"):
            if key in node:
                yield from _walk(node[key])


def _is_event(obj):
    kinds = obj.get("@type", [])
    kinds = kinds if isinstance(kinds, list) else [kinds]
    return any(isinstance(k, str) and (k.endswith("Event") or k == "Festival") for k in kinds)


def _text(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    return " ".join(html.unescape(TAGS.sub(" ", str(value or ""))).split())


def _when(value):
    """ISO date/datetime -> (YYYY-MM-DD, HH:MM or None), in Amsterdam time when a timezone is given."""
    value = _text(value)
    if not value:
        return None, None
    value = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value[:25])
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?", value)
        return (m.group(1), m.group(2)) if m else (None, None)
    if dt.tzinfo:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Europe/Amsterdam"))
        except Exception:
            pass
    has_time = "T" in value or " " in value.strip()
    return dt.date().isoformat(), (dt.strftime("%H:%M") if has_time else None)


PARTY_WORDS = r"\b(party|night|nacht|club|rave|dj|djs|bangers|disco|feest|techno|house|80s|90s|00s|decades|closing|opening|drag)\b"


def _guess_category(obj, name, door=None):
    kinds = obj.get("@type", [])
    kinds = " ".join(kinds if isinstance(kinds, list) else [kinds]).lower()
    text = (name + " " + _text(obj.get("description"))[:300]).lower()
    if "festival" in kinds or "festival" in name.lower():
        return "festival"
    if re.search(r"\b(student|studenten|studentenfeest|erasmus|eur|hogeschool|dispuut|vereniging)\b", text):
        return "student"
    if re.search(PARTY_WORDS, name.lower()) or (door and (door >= "22:30" or door < "05:00")):
        return "club"
    if re.search(r"\b(concert|live|tour)\b", text) or "musicevent" in kinds or (door and "18:00" <= door <= "22:00"):
        return "concert"
    return "club"


def extract(page_html, page_url=""):
    """Returns a list of event dicts found on the page (duplicates removed)."""
    found, seen = [], set()
    for block in LD_JSON.findall(page_html):
        block = block.strip().replace("<!--", "").replace("-->", "")
        block = re.sub(r"^\s*//\s*<!\[CDATA\[|//\s*\]\]>\s*$", "", block)
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for obj in _walk(data):
            if not _is_event(obj):
                continue
            name = _text(obj.get("name"))[:80]
            day, door = _when(obj.get("startDate") or obj.get("doorTime"))
            if not name or not day:
                continue
            _, end = _when(obj.get("endDate"))
            location = obj.get("location") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            if isinstance(location, str):
                venue, address = location, {}
            else:
                venue = _text(location.get("name"))
                address = location.get("address") or {}
            if isinstance(address, str):
                address_text = _text(address)
                city = address_text.split(",")[-1].strip() if address_text else ""
                city = re.sub(r"^\d{4}\s?[A-Z]{2}\s+", "", city)  # drop a Dutch postcode in front
            else:
                city = _text(address.get("addressLocality"))
                address_text = ", ".join(x for x in (_text(address.get("streetAddress")), city) if x)
            offers = obj.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            ticket_url = _text(offers.get("url") if isinstance(offers, dict) else "") or _text(obj.get("url")) or page_url
            if ticket_url and not ticket_url.startswith("http"):
                ticket_url = urllib.parse.urljoin(page_url, ticket_url)
            if re.fullmatch(r"[A-Za-z]{2}|netherlands|nederland|the netherlands", city.strip(), re.I):
                city = ""  # a country, not a city
            key = (day, venue.lower(), name.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "name": name, "venue": venue[:60], "city": city[:60], "address": address_text[:200],
                "event_date": day, "door_time": door, "end_time": end,
                "category": _guess_category(obj, name, door),
                "ticket_url": ticket_url[:500] if ticket_url else None,
                "description": _text(obj.get("description"))[:400] or None,
            })
    return sorted(found, key=lambda e: (e["event_date"], e["door_time"] or ""))
