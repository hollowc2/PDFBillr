"""Brand defaults shared by the app, the PDF templates and the data model."""

# Ledger blue: the app's brand colour and the default invoice accent.
DEFAULT_ACCENT_COLOR = "#2743A6"

# PDF paper sizes offered to users; values are CSS @page size keywords.
PAGE_SIZES = ("A4", "Letter")
DEFAULT_PAGE_SIZE = "A4"
PAGE_SIZE_OPTIONS = (
    ("A4", "A4 (210 × 297 mm)"),
    ("Letter", "US Letter (8.5 × 11 in)"),
)


def normalize_page_size(value) -> str:
    """Map user input to a supported @page size, defaulting to A4."""
    text = str(value or "").strip().lower()
    for size in PAGE_SIZES:
        if size.lower() == text:
            return size
    return DEFAULT_PAGE_SIZE


def account_page_size(user) -> str:
    """The account's default paper size, for invoices created without a form."""
    defaults = getattr(user, "business_defaults", None) if user is not None else None
    return normalize_page_size(getattr(defaults, "default_page_size", None))
