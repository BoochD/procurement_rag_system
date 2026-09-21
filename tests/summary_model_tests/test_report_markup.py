from summary_model.report_markup import mark_report_text


def test_report_markup_restores_document_and_note_tags():
    report = "<doc>Заявка в план-график</doc>: <note>проверьте источник</note>"

    marked = mark_report_text(report)

    assert "<doc>Заявка в план-график</doc>" in marked
    assert "<note>проверьте источник</note>" in marked
    assert "&lt;doc&gt;" not in marked
    assert "&lt;note&gt;" not in marked
