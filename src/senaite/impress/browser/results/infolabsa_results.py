# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from DateTime import DateTime  # ← añadido para Delta Check (no afecta lo existente)

try:
    from bika.lims import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("senaite.impress")


def _to_unicode(v):
    try:
        return unicode(v)
    except Exception:
        try:
            return u"%s" % v
        except Exception:
            return u""


class InfolabsaResultsWithState(BrowserView):
    """
    Renderiza la tabla 'cool' usando templates/results_with_state.pt
    y entrega exactamente las claves que espera el template.
    """
    index = ViewPageTemplateFile("../templates/results_with_state.pt")

    # ------------------------- helpers -------------------------
    def _get(self, obj, name, default=None):
        if obj is None:
            return default
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
        return _to_unicode(v)

    def _first_value(self, obj, names):
        """Devuelve el primer getter/attr no vacío encontrado en `obj`."""
        for n in names:
            v = self._get(obj, n)
            if v not in (None, u"", ""):
                return v, n
        return None, None

    # ---------- extracción robusta de Resultado / Unidad ----------
    def _get_result(self, a):
        for g in ("getFormattedResult", "getResult", "Result", "result", "formatted_result"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v
        return u"—"

    def _get_unit(self, a):
        for g in ("getUnit", "Unit", "unit", "getFormattedUnit"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v
        return u""

    def _get_service(self, a):
        """Recupera el Analysis Service si es accesible desde el Analysis."""
        svc, used = self._first_value(a, ("getService", "getAnalysisService", "Service", "service"))
        if svc:
            try:
                title = self._get(svc, "Title") or self._get(svc, "title") or u"(sin título)"
                logger.info("[impress] AS vinculado por %s => %s", used, self._u(title))
            except Exception:
                logger.info("[impress] AS vinculado por %s", used)
        return svc

    def _get_low_high_candidates(self, a_or_svc):
        """
        Devuelve (low, high, source) buscando en múltiples nombres, para
        Analysis o Service indistintamente.
        Incluye LOD/LOQ como último recurso si existen.
        """
        low_names = (
            "getLowerLimit", "getLowerResultLimit", "getLowerRange",
            "getMin", "getMinimum", "LowerLimit", "lower", "lower_limit",
            "getLowerDetectionLimit", "getLowerQuantitationLimit", "getLowerQuantificationLimit",
        )
        high_names = (
            "getUpperLimit", "getUpperResultLimit", "getUpperRange",
            "getMax", "getMaximum", "UpperLimit", "upper", "upper_limit",
            "getUpperDetectionLimit", "getUpperQuantitationLimit", "getUpperQuantificationLimit",
        )
        lo = hi = None
        src = []
        for n in low_names:
            v = self._get(a_or_svc, n)
            if v not in (None, u"", ""):
                lo = v
                src.append(n)
                break
        for n in high_names:
            v = self._get(a_or_svc, n)
            if v not in (None, u"", ""):
                hi = v
                src.append(n)
                break
        return lo, hi, "+".join(src) if src else ""

    # ---------- referencia desde dict/str ----------
    def _ref_range_from_any(self, rr):
        """
        Convierte 'rr' (str/dict/objeto) a texto y (low, high) si es posible.
        Retorna (ref_text, low, high, source).
        """
        if isinstance(rr, dict):
            # Claves típicas en SENAITE: min/max (+ warn_min/warn_max, operators…)
            lo = rr.get("lower", rr.get("min"))
            hi = rr.get("upper", rr.get("max"))
            text = rr.get("text") or rr.get("label") or u""
            if not text:
                lo_t = u"" if lo in (None, u"", "") else self._u(lo)
                hi_t = u"" if hi in (None, u"", "") else self._u(hi)
                text = (lo_t + (u" – " if lo_t or hi_t else u"") + hi_t).strip()
            return text, lo, hi, "dict(min/max)"
        # string/objeto
        if rr not in (None, u"", ""):
            return self._u(rr), None, None, "string"
        return u"", None, None, ""

    # ---------- computo de rango con búsquedas encadenadas ----------
    def _compute_ref_range(self, a):
        """
        Busca el rango de referencia en Analysis y, si hace falta, en el Service.
        Devuelve (ref_text, low, high, path_used).
        """
        path_used = []

        # 0) Nombre para logging
        try:
            an_name = (self._get(a, "Title") or self._get(a, "title") or self._get(a, "getKeyword") or u"(analito)")
        except Exception:
            an_name = u"(analito)"

        # 1) Métodos "ricos" en Analysis
        for g in ("getResultsRange", "getReferenceResultsRange", "getReferenceRange", "getResultsRangeDict", "getRange"):
            rr = self._get(a, g)
            if rr not in (None, u"", ""):
                ref_text, lo, hi, src = self._ref_range_from_any(rr)
                path_used.append("Analysis.%s:%s" % (g, src))
                if ref_text or (lo is not None or hi is not None):
                    logger.info("[impress] %s: rango por %s", self._u(an_name), path_used[-1])
                    # si no hay lo/hi dentro del dict/string, intenta low/high del Analysis
                    if lo is None and hi is None:
                        lo2, hi2, alt = self._get_low_high_candidates(a)
                        if lo2 is not None or hi2 is not None:
                            path_used.append("Analysis.low/high(%s)" % alt)
                            lo, hi = lo2, hi2
                    return ref_text, lo, hi, " -> ".join(path_used))

        # 2) Fallback explícito a low/high en Analysis (incluye LOQ/LOD si existieran)
        lo, hi, alt = self._get_low_high_candidates(a)
        if lo is not None or hi is not None:
            lo_t = u"" if lo is None else self._u(lo)
            hi_t = u"" if hi is None else self._u(hi)
            txt = (lo_t + (u" – " if lo_t or hi_t else u"") + hi_t).strip()
            path_used.append("Analysis.low/high(%s)" % alt)
            logger.info("[impress] %s: rango por %s", self._u(an_name), path_used[-1])
            return txt, lo, hi, " -> ".join(path_used))

        # 3) Intentar en el Analysis Service
        svc = self._get_service(a)
        if svc:
            for g in ("getResultsRange", "getReferenceResultsRange", "getReferenceRange", "getRange"):
                rr = self._get(svc, g)
                if rr not in (None, u"", ""):
                    ref_text, lo, hi, src = self._ref_range_from_any(rr)
                    path_used.append("Service.%s:%s" % (g, src))
                    if ref_text or (lo is not None or hi is not None):
                        logger.info("[impress] %s: rango por %s", self._u(an_name), path_used[-1])
                        if lo is None and hi is None:
                            lo2, hi2, alt2 = self._get_low_high_candidates(svc)
                            if lo2 is not None or hi2 is not None:
                                path_used.append("Service.low/high(%s)" % alt2)
                                lo, hi = lo2, hi2
                        return ref_text, lo, hi, " -> ".join(path_used))

            # 4) Fallback a low/high en Service (incluye LOQ/LOD)
            lo, hi, alt = self._get_low_high_candidates(svc)
            if lo is not None or hi is not None:
                lo_t = u"" if lo is None else self._u(lo)
                hi_t = u"" if hi is None else self._u(hi)
                txt = (lo_t + (u" – " if lo_t or hi_t else u"") + hi_t).strip()
                path_used.append("Service.low/high(%s)" % alt)
                logger.info("[impress] %s: rango por %s", self._u(an_name), path_used[-1])
                return txt, lo, hi, " -> ".join(path_used))

        # 5) Sin rango
        logger.info("[impress] %s: sin rango detectable (Analysis ni Service)", self._u(an_name))
        return u"", None, None, ""

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

        alert_classes = u''
        alert_text = u''
        alert_title = u''
        if delta_flag:
            try:
                alert_classes = u'al-delta'
                sym = delta_flag.get('symbol') or u'▲'
                txt = delta_flag.get('text') or u'Δ fuera de límite'
                alert_text = u'%s %s' % (sym, txt)
                alert_title = delta_flag.get('title') or u'Delta fuera de límite'
            except Exception:
                pass

        return estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title

    def row(self, a):
        # Nombre del análisis
        name = (
            self._get(a, 'Title') or
            self._get(a, 'title') or
            self._get(a, 'getKeyword') or
            u''
        )

        # Resultado y unidad (robustos)
        result = self._get_result(a)
        unit = self._get_unit(a)

        # Rango de referencia (más robusto + logging de la ruta)
        ref_text, low, high, path = self._compute_ref_range(a)

        # Flags: crítico / delta (si tus objetos exponen estos getters, úsalos)
        is_critical = bool(self._get(a, 'getCritical', False) or self._get(a, 'isCritical', False))
        delta_flag = None
        try:
            delta_flag = self._get(a, 'getDeltaFlag')  # idealmente un dict
        except Exception:
            delta_flag = None

        estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title = \
            self._status_payload(result, low, high, is_critical=is_critical, delta_flag=delta_flag)

        # Log extra si no hay rango
        try:
            if not ref_text and low is None and high is None:
                logger.info("[impress] Sin rango para '%s' (uid=%r). Revisar Specifications/DynamicSpecs.",
                            self._u(name), getattr(a, 'UID', lambda: None)())
            else:
                logger.info("[impress] '%s' -> ref='%s' (lo=%r, hi=%r) via %s",
                            self._u(name), self._u(ref_text), low, high, path or "n/a")
        except Exception:
            pass

        return {
            'name': name,
            'result': result,
            'unit': unit,
            'ref_range': ref_text,
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


# ======================================================================
# === COMPLEMENTO: Vista para Mini-Panel Delta Check (no altera lo de arriba)
# ======================================================================

class InfolabsaDeltaCheck(BrowserView):
    """Devuelve {'header': {...}, 'rows': [...]} para el mini-panel de Delta Check."""

    # ---------- helpers ----------
    def _spark_svg(self, points):
        """
        Genera un pequeño sparkline SVG.
        points: lista [(DateTime ISO string, float|None), ...] ordenables por tiempo.
        """
        if not points:
            return u""
        vals = [p[1] for p in points if p[1] is not None]
        if not vals:
            return u""
        W, H, pad = 140, 36, 6
        y_min, y_max = min(vals), max(vals)

        def nx(i):
            n = max(1, len(points) - 1)
            return pad + (W - 2 * pad) * (float(i) / float(n))

        def ny(y):
            if y_max == y_min:
                return H / 2.0
            return pad + (H - 2 * pad) * (1.0 - ((y - y_min) / (y_max - y_min)))

        path = []
        for i, (_, y) in enumerate(points):
            if y is None:
                continue
            path.append(u"{},{}".format(int(nx(i)), int(ny(float(y)))))
        d = u"M " + u" L ".join(path) if path else u""
        return u"""<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <path d="{d}" fill="none" stroke="currentColor" stroke-width="2"/>
</svg>""".format(W=W, H=H, d=d)

    def _pick_window_months(self, series):
        """
        Elige 6m si hay ≥3 mediciones en 6 meses; si no, usa 12m.
        series: [{'date': DateTime, 'value': float, ...}, ...]
        """
        now = DateTime()
        sixm = [p for p in series if (now - p['date']).days <= 31 * 6]
        return 6 if len(sixm) >= 3 else 12

    def _delta_row(self, analito):
        """
        Construye una fila delta con última y previa medición, Δ% y sparkline.
        analito: {'name', 'unit', 'series':[{'sid','date','value'}...]}
        """
        unit = analito.get('unit', u'')
        series = list(analito.get('series', [])) or []
        if not series:
            return None

        window_m = self._pick_window_months(series)
        now = DateTime()
        win = [p for p in series if (now - p['date']).days <= 31 * window_m]
        if len(win) < 2:
            return None

        win.sort(key=lambda p: p['date'])
        last = win[-1]
        prev = win[-2]
        last_v = last.get('value')
        prev_v = prev.get('value')

        delta_abs = None
        delta_pct = None
        if last_v is not None and prev_v not in (None, 0):
            try:
                delta_abs = float(last_v) - float(prev_v)
                delta_pct = (delta_abs / float(prev_v)) * 100.0
            except Exception:
                delta_abs = None
                delta_pct = None

        arrow = u"→"
        if delta_abs is not None:
            arrow = u"↑" if delta_abs > 0 else (u"↓" if delta_abs < 0 else u"→")

        rcv_note = u""
        rcv_flag = u""

        points = [(p['date'].ISO(), p.get('value')) for p in win]
        spark = self._spark_svg(points)

        def _fmt(v, nd=3):
            try:
                return round(float(v), nd)
            except Exception:
                return v

        row = {
            'name': analito.get('name', u'Analito'),
            'unit': unit,
            'window_months': window_m,
            'last_value': _fmt(last_v, 3),
            'last_sid': last.get('sid'),
            'last_date': last.get('date').strftime('%d/%m/%Y'),
            'prev_value': _fmt(prev_v, 3),
            'prev_sid': prev.get('sid'),
            'prev_date': prev.get('date').strftime('%d/%m/%Y'),
            'delta_abs': (_fmt(delta_abs, 2) if delta_abs is not None else None),
            'delta_pct': (round(delta_pct, 1) if delta_pct is not None else None),
            'arrow': arrow,
            'rcv_note': rcv_note,
            'rcv_flag': rcv_flag,
            'spark_svg': spark,
        }
        return row

    # ---------------- DEMO: sustituye por extracción real de SENAITE 2.6 ----------------
    def _fetch_series_for_ar(self, ar):
        return [
            {'name': 'Glucosa', 'unit': 'mg/dL', 'series': [
                {'sid': 'AR001', 'date': DateTime() - 120, 'value': 88.0},
                {'sid': 'AR045', 'date': DateTime() - 60,  'value': 92.0},
                {'sid': 'AR082', 'date': DateTime() - 5,   'value': 110.0},
            ]},
            {'name': 'Creatinina', 'unit': 'mg/dL', 'series': [
                {'sid': 'AR010', 'date': DateTime() - 300, 'value': 0.85},
                {'sid': 'AR077', 'date': DateTime() - 40,  'value': 1.05},
            ]},
        ]

    # ---------------- salida para el template ----------------
    def __call__(self, ar=None):
        ar_obj = ar or getattr(self, 'context', None)
        series_by_analyte = self._fetch_series_for_ar(ar_obj)

        rows = []
        c6 = c12 = 0
        for a in series_by_analyte:
            r = self._delta_row(a)
            if not r:
                continue
            rows.append(r)
            if r['window_months'] == 6:
                c6 += 1
            else:
                c12 += 1

        dominant = 6 if c6 >= c12 else 12
        header = {
            'dominant': dominant,
            'count6': c6,
            'count12': c12,
            'total': len(rows),
        }
        return {'header': header, 'rows': rows}
