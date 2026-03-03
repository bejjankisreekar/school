from io import BytesIO

from django.http import HttpResponse
from django.template.loader import render_to_string


def render_pdf_bytes(template_name: str, context: dict) -> bytes | None:
    """
    Render a Django template to PDF bytes using xhtml2pdf.

    Returns None if generation fails (caller should handle errors).
    """
    html = render_to_string(template_name, context)
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return None

    result = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result, encoding="utf-8")
    if pisa_status.err:
        return None
    return result.getvalue()


def pdf_response(pdf_bytes: bytes, filename: str) -> HttpResponse:
    """
    Wrap raw PDF bytes in an HttpResponse with download headers.
    """
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

