# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.views.generic.detail import DetailView

from django_xhtml2pdf.views import PdfMixin

from part.models import Part


class InvenTreePDFMixin(PdfMixin):
    """
    Custom mixin for PDF rendering.
    Extends the PdfMixin class provided by django-xhtml2pdf
    """
    
    # Currently does not really "do" anything...
    pass


class PDFTestView(PdfMixin, DetailView):

    model = Part
    template_name = 'report/pdf_test.html'

    def get_context_data(self, **kwargs):

        context = super().get_context_data()

        context['part'] = self.get_object()

        return context