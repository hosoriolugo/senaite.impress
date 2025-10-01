# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

try:
    from bika.lims import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("senaite.impress")


class InfolabsaResultsWithState(BrowserView):
    """
    Renderiza la tabla 'cool' usando templates/results_with_state.pt
    y entrega exactamente las claves que espera el template.
    """
    index = ViewPageTemplateFile("../templates/results_with_state.pt")

    # ------------------------- helpers -------------------------
    def _get(self, obj, name, default=None):
        attr = getattr(obj, name, None)
        if callable(attr):
            try:
                return attr()
            except Exception:
                return default
        return attr if attr is not None else default

    def _num(self, x):
        try:
            return float(x)
        except Exception:
            return None

    def _u(self, v):
        try:
            return unicode(v)
        except Exception:
            try:
                return u"%s" % v
            except Exception:
                return u""

    # ------------------------- data extraction -------------------------
    def analyses(self):
        ctx = self.context
        for g in ('getAnalyses', 'analyses', 'getAnalysis'):
            items = self._get(ctx, g)
            if items:
                try:
                    return list(items)
                except Exception:
                    return items
        return []

    def _status_payload(self, value, low, high, is_critical=False, delta_flag=None):
        """
        Traduce el estado a las 4 claves esperadas por el template:
        - estado_class (CSS)
        - estado_symbol (✓, ⚠, ❗, —)
        - estado_text ("En rango", "Fuera de rango", "Crítico", "No aplica")
        - alert_* se maneja aparte
        """
        # default: no aplica
        estado_class = u''
        estado_symbol = u'—'
        estado_text = u'No aplica'

        v = self._num(value)
        lo = self._num(low)
        hi = self._num(high)

        if is_critical:
            estado_class = u'al-critical'
            estado_symbol = u'❗'
            estado_text = u'Crítico'
        elif v is not None and (lo is not None or hi is not None):
            if lo is not None and v < lo:
                estado_class = u'fr-alert'
                estado_symbol = u'⚠'
                estado_text = u'Fuera de rango'
            elif hi is not None and v > hi:
                estado_class = u'fr-alert'
                estado_symbol = u'⚠'
                estado_text = u'Fuera de rango'
            else:
                estado_class = u'fr-ok'
                estado_symbol = u'✓'
                estado_text = u'En rango'

        # Delta (▲/▼) no cambia estado_symbol, va como alerta
        alert_classes = u''
        alert_text = u''
        alert_title = u''
        if delta_flag:
            alert_classes = u'al-delta'
            # delta_flag podría ser dict {'symbol': u'▲', 'text': u'+22% vs 2025-09-01'}
            sym = delta_flag.get('symbol') or u'▲'
            txt = delta_flag.get('text') or u'Δ fuera de límite'
            alert_text = u'%s %s' % (sym, txt)
            alert_title = delta_flag.get('title') or u'Delta fuera de límite'

        return estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title

    def row(self, a):
        # Nombre del análisis
        name = (
            self._get(a, 'Title') or
            self._get(a, 'title') or
            self._get(a, 'getKeyword') or
            u''
        )

        # Resultado y unidad
        result = (
            self._get(a, 'getFormattedResult') or
            self._get(a, 'getResult') or
            self._get(a, 'Result') or
            u'—'
        )
        unit = (self._get(a, 'getUnit') or self._get(a, 'Unit') or u'')

        # Rango de referencia
        low = self._get(a, 'getLowerLimit')
        high = self._get(a, 'getUpperLimit')
        rr = (self._get(a, 'getReferenceRange') or self._get(a, 'ReferenceRange'))
        if rr:
            ref_range = rr
        elif (low is not None) or (high is not None):
            lo = u'' if low is None else self._u(low)
            hi = u'' if high is None else self._u(high)
            ref_range = (lo + u' – ' + hi).strip()
        else:
            ref_range = u''

        # Flags: crítico / delta (si tus objetos exponen estos getters, úsalos)
        is_critical = bool(self._get(a, 'getCritical', False) or self._get(a, 'isCritical', False))
        delta_flag = None
        try:
            delta_flag = self._get(a, 'getDeltaFlag')  # idealmente un dict
        except Exception:
            delta_flag = None

        estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title =             self._status_payload(result, low, high, is_critical=is_critical, delta_flag=delta_flag)

        return {
            # EXACTAMENTE lo que el template espera:
            'name': name,
            'result': result,
            'unit': unit,
            'ref_range': ref_range,
            'estado_class': estado_class,
            'estado_symbol': estado_symbol,
            'estado_text': estado_text,
            'alert_classes': alert_classes,
            'alert_text': alert_text or u'—',
            'alert_title': alert_title,
        }

    def rows(self):
        return [self.row(a) for a in self.analyses()]

    # ------------------------- rendering -------------------------
    def __call__(self):
        # Si pide JSON, devuelve la misma estructura que usa el template
        if (self.request.get('format', '').lower() == 'json'):
            import json
            data = {'items': self.rows()}
            self.request.response.setHeader('Content-Type', 'application/json; charset=utf-8')
            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        try:
            logger.info("[infolabsa] Render COOL table via results_with_state.pt")
        except Exception:
            pass
        return self.index()


# Compatibilidad por si el ZCML apunta al nombre antiguo
InfolabsaResults = InfolabsaResultsWithState
