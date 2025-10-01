# -*- coding: utf-8 -*-
from Products.Five import BrowserView

try:
    # Prefer SENAITE/BIKA logger if available
    from bika.lims import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("senaite.impress")


class InfolabsaResultsWithState(BrowserView):
    """
    Vista auxiliar para la tabla de resultados con estado/alertas.
    Se invoca como @@infolabsa-results-with-state sobre un AnalysisRequest (AR).
    """

    # ------------------------- helpers -------------------------
    def _get(self, obj, name, default=None):
        """Obtiene atributo o método (sin args) del objeto.
        Si es callable, lo invoca y devuelve su retorno; maneja errores y None.
        """
        attr = getattr(obj, name, None)
        if callable(attr):
            try:
                return attr()
            except Exception:
                return default
        return attr if attr is not None else default

    def _num(self, x):
        """Convierte a float si es posible; de lo contrario None."""
        try:
            return float(x)
        except Exception:
            return None

    def _to_unicode(self, value):
        try:
            return unicode(value)  # noqa: F821  # Python 2.x
        except Exception:
            try:
                return u"%s" % value
            except Exception:
                return u""

    # ------------------------- data extraction -------------------------
    def analyses(self):
        """Devuelve la lista de análisis del AR actual (context)."""
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

    def row(self, a):
        """Normaliza campos y calcula estado/alertas por fila."""
        title = (
            self._get(a, 'Title') or
            self._get(a, 'title') or
            self._get(a, 'getKeyword') or
            u''
        )

        result = (
            self._get(a, 'getFormattedResult') or
            self._get(a, 'getResult') or
            self._get(a, 'Result') or
            u'—'
        )

        unit = (
            self._get(a, 'getUnit') or
            self._get(a, 'Unit') or
            u''
        )

        # Rango de referencia
        low = self._get(a, 'getLowerLimit')
        high = self._get(a, 'getUpperLimit')
        rr = (self._get(a, 'getReferenceRange') or self._get(a, 'ReferenceRange'))

        if rr:
            ref_range = rr
        elif (low is not None) or (high is not None):
            try:
                lo = u'' if low is None else self._to_unicode(low)
            except Exception:
                lo = u''
            try:
                hi = u'' if high is None else self._to_unicode(high)
            except Exception:
                hi = u''
            ref_range = (lo + u' – ' + hi).strip()
        else:
            ref_range = u''

        # Estado básico (numérico fuera de rango)
        val = self._num(result)
        lo_num = self._num(low)
        hi_num = self._num(high)

        status = u'normal'
        if val is not None:
            if lo_num is not None and val < lo_num:
                status = u'bajo'
            if hi_num is not None and val > hi_num:
                status = u'alto'

        return {
            'title': title,
            'result': result,
            'unit': unit,
            'reference_range': ref_range,
            'status': status,
        }

    def rows(self):
        return [self.row(a) for a in self.analyses()]

    # ------------------------- rendering -------------------------
    def __call__(self):
        """Por defecto no rompe el render. Si ?format=json, devuelve JSON."""
        fmt = self.request.get('format', '').lower()
        if fmt == 'json':
            try:
                import json
            except Exception:
                json = None
            data = {'items': self.rows()}
            if json is not None:
                self.request.response.setHeader('Content-Type', 'application/json; charset=utf-8')
                return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            # Fallback a string si json no está disponible
            return self._to_unicode(data)
        # Retorno mínimo para incluir como vista sin plantilla
        try:
            logger.info("[infolabsa] @@infolabsa-results-with-state render OK (html)")
        except Exception:
            pass
        return u""


# Compatibilidad hacia atrás por si el ZCML registra otra clase
InfolabsaResults = InfolabsaResultsWithState
