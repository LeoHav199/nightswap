"""Profile icons people can pick instead of the first letter of their name.

Each icon is drawn on a 24-unit grid as stroked lines (no fill), so it takes the colour of its
surroundings: lime on the dark circle. Icons instead of photo uploads: nothing to moderate, and
every seller still gets a recognisable face.
"""

ICONS = {
    "disco": '<circle cx="12" cy="13" r="7"/><path d="M5 13h14M12 6v14M7 8.5c3 1.5 7 1.5 10 0M7 17.5c3-1.5 7-1.5 10 0M12 2v4"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3.5" y="14" width="4" height="6" rx="1.5"/><rect x="16.5" y="14" width="4" height="6" rx="1.5"/>',
    "cocktail": '<path d="M4 5h16l-8 8.5L4 5ZM12 13.5V20M8 20h8M15 3l3 3"/><circle cx="17.5" cy="5.5" r="1.2"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.9L12 3.5Z"/>',
    "ghost": '<path d="M5 20V11a7 7 0 0 1 14 0v9l-2.3-1.6L14.3 20 12 18.4 9.7 20l-2.4-1.6L5 20Z"/><circle cx="9.5" cy="11" r=".6"/><circle cx="14.5" cy="11" r=".6"/>',
    "bolt": '<path d="M13.5 2.5 5 13.5h6.5l-1 8L19 10.5h-6.5l1-8Z"/>',
    "moon": '<path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10Z"/><path d="M17 3.5v3M15.5 5h3"/>',
    "flame": '<path d="M12 21a6 6 0 0 0 6-6c0-4-3-5.5-3.5-9.5C12 7 10.5 9 10.5 11 9 10 8.5 8.5 8.5 8 7 9.5 6 12 6 15a6 6 0 0 0 6 6Z"/><path d="M12 21a2.5 2.5 0 0 1-2.5-2.5c0-1.8 1.5-2.6 2.5-4 1 1.4 2.5 2.2 2.5 4A2.5 2.5 0 0 1 12 21Z"/>',
    "note": '<path d="M9 18V5.5l10-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/><path d="M9 9.5l10-2"/>',
    "crown": '<path d="M3.5 8 7.5 12 12 5l4.5 7 4-4-2 11h-13l-2-11Z"/><path d="M6 19h12"/>',
    "heart": '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20Z"/>',
    "sparkle": '<path d="M12 3c.7 4.8 2.2 6.3 7 7-4.8.7-6.3 2.2-7 7-.7-4.8-2.2-6.3-7-7 4.8-.7 6.3-2.2 7-7Z"/><path d="M18.5 15.5c.3 1.8.8 2.3 2.5 2.5-1.7.2-2.2.7-2.5 2.5-.3-1.8-.8-2.3-2.5-2.5 1.7-.2 2.2-.7 2.5-2.5Z"/>',
}
