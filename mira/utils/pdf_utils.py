from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4

def export_to_pdf(title: str, items: list, filename: str = "output.pdf"):
    """
    Export structured items to a PDF. Handles list of dicts or list of strings.
    """
    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Heading1"]), Spacer(1, 12)]

    if items and isinstance(items[0], dict):
        keys = list(items[0].keys())
        data = [keys] + [[str(item.get(k, "")) for k in keys] for item in items]
        story.append(Table(data, repeatRows=1))
    else:
        for item in items:
            story.append(Paragraph(f"- {item}", styles["Normal"]))

    doc.build(story)
    return filename
