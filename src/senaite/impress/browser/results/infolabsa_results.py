# -*- coding: utf-8 -*-
from Products.Five import BrowserView


class InfolabsaResults(BrowserView):
"""Vista auxiliar para la tabla de resultados con estado/alertas.
Se invoca como @@infolabsa-results-with-state sobre un AnalysisRequest (AR).
"""


def _get(self, obj, name, default=None):
attr = getattr(obj, name, None)
if callable(attr):
try:
return attr()
except Exception:
return default
return attr if attr is not None else default


def analyses(self):
"""Lista de análisis del AR."""
ctx = self.context
getters = ['getAnalyses', 'analyses', 'getAnalysis']
for g in getters:
items = self._get(ctx, g, None)
if items:
try:
return list(items)
except Exception:
return items
return []


def _num(self, x):
try:
return float(x)
except Exception:
return None


def row(self, a):
"""Normaliza campos y calcula estado/alertas por fila."""
title = (self._get(a, 'Title') or self._get(a, 'title') or self._get(a, 'getKeyword') or u'')
result = (self._get(a, 'getFormattedResult') or self._get(a, 'getResult') or self._get(a, 'Result') or u'—')
unit = (self._get(a, 'getUnit') or self._get(a, 'Unit') or u'')


# Rango de referencia
low = self._get(a, 'getLowerLimit')
high = self._get(a, 'getUpperLimit')
rr = (self._get(a, 'getReferenceRange') or self._get(a, 'ReferenceRange'))
if rr:
ref_range = rr
elif (low is not None) or (high is not None):
try:
lo = u'' if low is None else unicode(low)
except Exception:
lo = u''
try:
hi = u'' if high is None else unicode(high)
except Exception:
hi = u''
ref_range = (lo + u' – ' + hi).strip()
else:
ref_range = u''
}
