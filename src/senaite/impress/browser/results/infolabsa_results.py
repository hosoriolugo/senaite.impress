# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from DateTime import DateTime
from Products.CMFCore.utils import getToolByName

try:
    unicode
except NameError:
    unicode = str

try:
    from bika.lims import logger
except Exception:
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
        if not obj:
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
            if x in (None, u"", ""):
                return None
            return float(x)
        except Exception:
            return None

    def _u(self, v):
        return _to_unicode(v)

    def _first_text_from_lo_hi(self, lo, hi):
        lo_t = u"" if lo in (None, u"", "") else self._u(lo)
        hi_t = u"" if hi in (None, u"", "") else self._u(hi)
        return (lo_t + (u" - " if lo_t or hi_t else u"") + hi_t).strip()

    def _get_service(self, a):
        try:
            return getattr(a, "getService", lambda: None)()
        except Exception:
            return None

    def _get_ar_ctx(self, a):
        """Devuelve (ar, sample, sampletype, client, contact) si existen"""
        ar = getattr(a, "getAnalysisRequest", lambda: None)()
        sample = getattr(ar, "getSample", lambda: None)() if ar else None
        st = getattr(sample, "getSampleType", lambda: None)() if sample else None
        client = getattr(ar, "getClient", lambda: None)() if ar else None
        contact = getattr(ar, "getContact", lambda: None)() if ar else None
        return ar, sample, st, client, contact

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

    # ---------- low/high genéricos ----------
    def _get_low_high_candidates(self, obj):
        """Intenta leer low/high de muchos alias habituales."""
        if not obj:
            return None, None
        low_names = (
            "getLowerLimit", "getLowerResultLimit", "getLowerRange",
            "getMin", "getMinimum", "LowerLimit", "lower", "lower_limit",
            "getMinValue", "MinValue", "minValue",
            "getLowerNormal", "LowerNormal", "LowerNormalLimit",
            "getLowerDetectionLimit", "getLowerQuantitationLimit", "getLowerQuantificationLimit",
        )
        high_names = (
            "getUpperLimit", "getUpperResultLimit", "getUpperRange",
            "getMax", "getMaximum", "UpperLimit", "upper", "upper_limit",
            "getMaxValue", "MaxValue", "maxValue",
            "getUpperNormal", "UpperNormal", "UpperNormalLimit",
            "getUpperDetectionLimit", "getUpperQuantitationLimit", "getUpperQuantificationLimit",
        )
        low = high = None
        for n in low_names:
            v = self._get(obj, n)
            if v not in (None, u"", ""):
                low = v
                break
        for n in high_names:
            v = self._get(obj, n)
            if v not in (None, u"", ""):
                high = v
                break
        return low, high

    def _ref_range_from_any(self, rr):
        """
        Convierte 'rr' (str/dict/objeto) a (texto, low, high).
        """
        if isinstance(rr, dict):
            text = rr.get("text") or rr.get("label") or u""
            lo = rr.get("lower", rr.get("min"))
            hi = rr.get("upper", rr.get("max"))
            if not text:
                text = self._first_text_from_lo_hi(lo, hi)
            return text, lo, hi
        if rr not in (None, u"", ""):
            return self._u(rr), None, None
        return u"", None, None

    # ---------- 1) DINÁMICAS ----------
    def _extract_dynamic_specs_minmax(self, a, keyword):
        try:
            ar, sample, st, client, contact = self._get_ar_ctx(a)
            candidates = []
            for holder, origin in (
                (a, "Analysis"),
                (ar, "AR"),
                (client, "Client"),
                (contact, "Contact"),
                (st, "SampleType"),
            ):
                if not holder:
                    continue
                for name in (
                    "getDynamicAnalysisSpecifications",
                    "getDynamicSpecifications",
                    "getPatientDynamicSpecifications",
                    "getApplicableDynamicSpecifications",
                ):
                    fn = getattr(holder, name, None)
                    if callable(fn):
                        try:
                            candidates.append((origin + "." + name, fn()))
                        except Exception:
                            pass

            def _match_spec(container):
                if not container:
                    return None
                if isinstance(container, dict) and container.get(keyword):
                    return container.get(keyword)
                if isinstance(container, (list, tuple)):
                    for row in container:
                        k = None
                        if isinstance(row, dict):
                            k = (row.get("keyword") or row.get("service") or row.get("Service"))
                            if hasattr(k, "getKeyword"):
                                k = k.getKeyword()
                        else:
                            for gk in ("getKeyword", "Keyword", "keyword", "getServiceKeyword"):
                                gv = getattr(row, gk, None)
                                k = gv() if callable(gv) else None
                                if k:
                                    break
                        if k == keyword:
                            return row
                return None

            for origin, container in candidates:
                spec = _match_spec(container)
                if not spec:
                    continue

                def _read(spec, x):
                    if isinstance(spec, dict):
                        return spec.get(x) or spec.get(x.capitalize())
                    v = getattr(spec, x, None)
                    return v() if callable(v) else v

                lo = _read(spec, "min") or _read(spec, "minimum")
                hi = _read(spec, "max") or _read(spec, "maximum")
                if lo is not None or hi is not None:
                    logger.info("[impress] RefRange via DynamicSpecifications (%s) %s", origin, keyword)
                    return lo, hi, u"dynamic"
        except Exception:
            pass
        return None, None, None

    # ---------- 2) ANALYSIS SPECIFICATIONS ----------
    def _extract_specs_minmax_for_analysis(self, a):
        try:
            service = self._get_service(a)
            keyword = getattr(service, "getKeyword", lambda: None)() if service else None
            if not keyword:
                return None, None, None
            ar, sample, st, client, contact = self._get_ar_ctx(a)

            candidates = []
            for holder, label in (
                (ar, "AR"),
                (client, "Client"),
                (contact, "Contact"),
                (st, "SampleType"),
                (service, "Service"),
            ):
                if not holder:
                    continue
                for name in (
                    "getAnalysisSpecifications",
                    "getSpecifications",
                    "getApplicableSpecifications",
                    "getActiveAnalysisSpecifications",
                    "getAnalysisSpecificationsFor",
                    "getSpecificationsFor",
                ):
                    fn = getattr(holder, name, None)
                    if callable(fn):
                        try:
                            if name.endswith("For"):
                                ctx = ar or st or sample
                                if ctx is not None:
                                    candidates.append((label + "." + name, fn(ctx)))
                            else:
                                candidates.append((label + "." + name, fn()))
                        except Exception:
                            pass

            def _match(container):
                if isinstance(container, dict):
                    return container.get(keyword)
                if isinstance(container, (list, tuple)):
                    for row in container:
                        k = None
                        if isinstance(row, dict):
                            k = row.get("keyword")
                            if not k:
                                svc = row.get("Service", row.get("service"))
                                if hasattr(svc, "getKeyword"):
                                    k = svc.getKeyword()
                                elif isinstance(svc, (str, unicode)):
                                    k = svc
                        else:
                            for gk in ("getKeyword", "Keyword", "keyword", "ServiceKeyword", "getServiceKeyword"):
                                gv = getattr(row, gk, None)
                                k = gv() if callable(gv) else None
                                if k:
                                    break
                        if k == keyword:
                            return row
                return None

            for origin, container in candidates:
                spec = _match(container)
                if not spec:
                    continue

                def _read(x):
                    if isinstance(spec, dict):
                        return spec.get(x) or spec.get(x.capitalize())
                    v = getattr(spec, x, None)
                    return v() if callable(v) else v

                lo = _read("min") or _read("minimum")
                hi = _read("max") or _read("maximum")
                if lo is not None or hi is not None:
                    logger.info("[impress] RefRange via AnalysisSpecifications (%s) %s", origin, keyword)
                    return lo, hi, u"spec"
        except Exception:
            pass
        return None, None, None

    # ---------- 3) REFERENCE DEFINITIONS ----------
    def _extract_refdef_minmax(self, a):
        try:
            service = self._get_service(a)
            if not service:
                return None, None, None
            keyword = getattr(service, "getKeyword", lambda: None)()
            title = getattr(service, "Title", lambda: None)() or getattr(service, "title", lambda: None)()

            portal = self.context.portal_url.getPortalObject()
            catalog = getToolByName(portal, "portal_catalog")
            brains = catalog.searchResults(portal_type=("ReferenceDefinition", "BikaReferenceDefinition"))
            for b in brains:
                obj = b.getObject()
                rows = None
                for g in ("getReferenceValues", "ReferenceValues", "reference_values", "getValues"):
                    fn = getattr(obj, g, None)
                    rows = fn() if callable(fn) else getattr(obj, g, None)
                    if rows:
                        break
                if not rows:
                    continue

                def _row_to_match_row(row):
                    k = None
                    lo = hi = None
                    if isinstance(row, dict):
                        k = row.get("keyword") or row.get("Keyword")
                        if not k:
                            svc = row.get("Service") or row.get("service")
                            if hasattr(svc, "getKeyword"):
                                k = svc.getKeyword()
                            elif isinstance(svc, (str, unicode)):
                                k = svc
                        lo = (row.get("min") or row.get("Min") or
                              row.get("minimum") or row.get("Minimum"))
                        hi = (row.get("max") or row.get("Max") or
                              row.get("maximum") or row.get("Maximum"))
                    else:
                        for gk in ("getKeyword", "Keyword", "getServiceKeyword"):
                            gv = getattr(row, gk, None)
                            k = gv() if callable(gv) else None
                            if k:
                                break
                        if not k:
                            svc = None
                            for gs in ("getService", "Service", "service"):
                                sv = getattr(row, gs, None)
                                svc = sv() if callable(sv) else sv
                                if svc:
                                    break
                            if svc:
                                k = getattr(svc, "getKeyword", lambda: None)()
                                if not k:
                                    t = getattr(svc, "Title", lambda: None)()
                                    k = t() if callable(t) else t
                        for gl in ("getMin", "getMinimum", "Min", "Minimum", "min", "minimum"):
                            lv = getattr(row, gl, None)
                            lo = lv() if callable(lv) else (lo or lv)
                        for gh in ("getMax", "getMaximum", "Max", "Maximum", "max", "maximum"):
                            hv = getattr(row, gh, None)
                            hi = hv() if callable(hv) else (hi or hv)
                    return k, lo, hi

                hit = None
                for row in rows:
                    k, lo, hi = _row_to_match_row(row)
                    if not k:
                        continue
                    if k == keyword or (title and k == title):
                        hit = (lo, hi)
                        break

                if hit:
                    lo, hi = hit
                    if lo is not None or hi is not None:
                        logger.info("[impress] RefRange via ReferenceDefinitions %s", b.getURL())
                        return lo, hi, u"refdef"
        except Exception:
            pass
        return None, None, None

    # ---------- 4) LÍMITES del ANÁLISIS o del SERVICIO ----------
    def _extract_analysis_or_service_minmax(self, a):
        try:
            lo, hi = self._get_low_high_candidates(a)
            if lo is not None or hi is not None:
                return lo, hi, u"analysis"
            svc = self._get_service(a)
            if svc:
                lo2, hi2 = self._get_low_high_candidates(svc)
                if lo2 is not None or hi2 is not None:
                    return lo2, hi2, u"service"
        except Exception:
            pass
        return None, None, None

    # ---------- 4.b) Fallback: ReferenceValues en el Servicio ----------
    def _extract_service_refvalues(self, a):
        """
        Si el Servicio guarda Reference Values (lista/dict) con min/max, tómalo.
        """
        try:
            svc = self._get_service(a)
            if not svc:
                return None, None, None
            rows = None
            for g in ("getReferenceValues", "ReferenceValues", "reference_values", "getValues"):
                fn = getattr(svc, g, None)
                rows = fn() if callable(fn) else getattr(svc, g, None)
                if rows:
                    break
            if not rows:
                return None, None, None

            def _read_row(row):
                lo = hi = None
                if isinstance(row, dict):
                    lo = (row.get("min") or row.get("Min") or
                          row.get("minimum") or row.get("Minimum"))
                    hi = (row.get("max") or row.get("Max") or
                          row.get("maximum") or row.get("Maximum"))
                else:
                    for gl in ("getMin", "getMinimum", "Min", "Minimum", "min", "minimum", "getMinValue"):
                        lv = getattr(row, gl, None)
                        lo = lv() if callable(lv) else (lo or lv)
                    for gh in ("getMax", "getMaximum", "Max", "Maximum", "max", "maximum", "getMaxValue"):
                        hv = getattr(row, gh, None)
                        hi = hv() if callable(hv) else (hi or hv)
                return lo, hi

            for row in rows:
                lo, hi = _read_row(row)
                if lo is not None or hi is not None:
                    logger.info("[impress] RefRange via Service.ReferenceValues")
                    return lo, hi, u"service.refvalues"
        except Exception:
            pass
        return None, None, None

    # ---------- PRIORIDAD CORREGIDA PARA SENAITE 2.6+ ----------
    def _compute_ref_range(self, a):
        """
        Devuelve (ref_text, low, high, src) usando prioridad CORRECTA para SENAITE 2.6+
        """
        # ===== PRIORIDAD 1: Analysis.getResultsRange() - CANÓNICO =====
        try:
            results_range = self._get(a, "getResultsRange")
            if results_range and isinstance(results_range, dict):
                lo = results_range.get('min')
                hi = results_range.get('max')
                hide_min = results_range.get('hidemin', '') == 'on'
                hide_max = results_range.get('hidemax', '') == 'on'
                
                if not hide_min and not hide_max and (lo is not None or hi is not None):
                    text = self._first_text_from_lo_hi(lo, hi)
                    logger.info("[impress] RefRange via Analysis.getResultsRange() [CANÓNICO]")
                    return text, lo, hi, u"analysis.getResultsRange"
        except Exception as e:
            logger.debug("[impress] Error en Analysis.getResultsRange: %s", e)

        # ===== PRIORIDAD 2: Analysis.getSpecification() =====
        try:
            spec = self._get(a, "getSpecification")
            if spec:
                # Intentar getResultsRange del spec
                results_range = self._get(spec, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    lo = results_range.get('min')
                    hi = results_range.get('max')
                    if lo is not None or hi is not None:
                        text = self._first_text_from_lo_hi(lo, hi)
                        logger.info("[impress] RefRange via Analysis.getSpecification().getResultsRange()")
                        return text, lo, hi, u"analysis.spec.resultsrange"
                
                # Fallback: min/max directos
                lo = self._get(spec, "min") or self._get(spec, "Min")
                hi = self._get(spec, "max") or self._get(spec, "Max")
                if lo is not None or hi is not None:
                    text = self._first_text_from_lo_hi(lo, hi)
                    logger.info("[impress] RefRange via Analysis.getSpecification() directo")
                    return text, lo, hi, u"analysis.spec.direct"
        except Exception as e:
            logger.debug("[impress] Error en Analysis.getSpecification: %s", e)

        # ===== PRIORIDAD 3: Service.getResultsRange() =====
        try:
            service = self._get_service(a)
            if service:
                results_range = self._get(service, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    lo = results_range.get('min')
                    hi = results_range.get('max')
                    hide_min = results_range.get('hidemin', '') == 'on'
                    hide_max = results_range.get('hidemax', '') == 'on'
                    
                    if not hide_min and not hide_max and (lo is not None or hi is not None):
                        text = self._first_text_from_lo_hi(lo, hi)
                        logger.info("[impress] RefRange via Service.getResultsRange()")
                        return text, lo, hi, u"service.getResultsRange"
        except Exception as e:
            logger.debug("[impress] Error en Service.getResultsRange: %s", e)

        # ===== PRIORIDAD 4: AR.getSpecification() con keyword =====
        try:
            ar, sample, st, client, contact = self._get_ar_ctx(a)
            if ar:
                ar_spec = self._get(ar, "getSpecification")
                if ar_spec:
                    service = self._get_service(a)
                    keyword = self._get(service, "getKeyword") if service else None
                    
                    if keyword:
                        try:
                            results_range = ar_spec.getResultsRange(keyword)
                            if results_range and isinstance(results_range, dict):
                                lo = results_range.get('min')
                                hi = results_range.get('max')
                                if lo is not None or hi is not None:
                                    text = self._first_text_from_lo_hi(lo, hi)
                                    logger.info("[impress] RefRange via AR.getSpecification().getResultsRange(keyword)")
                                    return text, lo, hi, u"ar.spec.keyword"
                        except Exception:
                            pass
        except Exception as e:
            logger.debug("[impress] Error en AR.getSpecification: %s", e)

        # ===== PRIORIDAD 5-9: Mantener tus métodos existentes =====
        # 5) Dinámicas
        service = self._get_service(a)
        kw = self._get(service, "getKeyword") if service else None
        if kw:
            dlo, dhi, dsrc = self._extract_dynamic_specs_minmax(a, kw)
            if dlo is not None or dhi is not None:
                txt = self._first_text_from_lo_hi(dlo, dhi)
                return txt, dlo, dhi, dsrc

        # 6) Analysis Specifications
        slo, shi, ssrc = self._extract_specs_minmax_for_analysis(a)
        if slo is not None or shi is not None:
            txt = self._first_text_from_lo_hi(slo, shi)
            return txt, slo, shi, ssrc

        # 7) Reference Definitions
        rlo, rhi, rsrc = self._extract_refdef_minmax(a)
        if rlo is not None or rhi is not None:
            txt = self._first_text_from_lo_hi(rlo, rhi)
            return txt, rlo, rhi, rsrc

        # 8) Análisis / Servicio (campos directos)
        alo, ahi, asrc = self._extract_analysis_or_service_minmax(a)
        if alo is not None or ahi is not None:
            txt = self._first_text_from_lo_hi(alo, ahi)
            return txt, alo, ahi, asrc

        # 9) Service.ReferenceValues
        sv_lo, sv_hi, sv_src = self._extract_service_refvalues(a)
        if sv_lo is not None or sv_hi is not None:
            txt = self._first_text_from_lo_hi(sv_lo, sv_hi)
            return txt, sv_lo, sv_hi, sv_src

        # SIN RANGO
        try:
            keyword = self._get(service, "getKeyword") if service else "UNKNOWN"
            title = self._get(a, "Title") or "UNKNOWN"
            uid = self._get(a, "UID")
            logger.warning("[impress] NO RANGO para '%s' (kw=%s, uid=%s)", title, keyword, uid)
        except Exception:
            pass

        return u"", None, None, u"not_found"

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
        estado_class = u''
        estado_symbol = u'—'
        estado_text = u'No aplica'

        v = self._num(value)
        lo = self._num(low)
        hi = self._num(high)

        if is_critical:
            estado_class = u'al-critical'
            estado_symbol = u'●'
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
        name = (
            self._get(a, 'Title') or
            self._get(a, 'title') or
            self._get(a, 'getKeyword') or
            u''
        )

        result = self._get_result(a)
        unit = self._get_unit(a)

        # USAR LA PRIORIDAD CORREGIDA
        ref_text, low, high, ref_src = self._compute_ref_range(a)

        is_critical = bool(self._get(a, 'getCritical', False) or self._get(a, 'isCritical', False))
        try:
            delta_flag = self._get(a, 'getDeltaFlag')
        except Exception:
            delta_flag = None

        estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title = \
            self._status_payload(result, low, high, is_critical=is_critical, delta_flag=delta_flag)

        return {
            'name': name,
            'result': result,
            'unit': unit,
            'ref_range': ref_text or u'',
            'ref_low': low,
            'ref_high': high,
            'ref_src': ref_src or u'',
            # Alias
            'reference_range': (ref_text or u''),
            'range_text': (ref_text or u''),
            'range': (ref_text or u''),
            'reference_low': low,
            'reference_high': high,
            # Estado
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
        if (self.request.get('format', '').lower() == 'json'):
            import json
            data = {'items': self.rows()}
            self.request.response.setHeader('Content-Type', 'application/json; charset=utf-8')
            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        logger.info("[infolabsa] Render COOL table via results_with_state.pt")
        return self.index()


# Compatibilidad
InfolabsaResults = InfolabsaResultsWithState


# ======================================================================
# === VISTA DELTA CHECK (COMPATIBLE CON RANGOS)
# ======================================================================

class InfolabsaDeltaCheck(BrowserView):

    def _spark_svg(self, points):
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
        now = DateTime()
        sixm = [p for p in series if (now - p['date']).days <= 31 * 6]
        return 6 if len(sixm) >= 3 else 12

    def _delta_row(self, analito):
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

        arrow = u"↔"
        if delta_abs is not None:
            arrow = u"↑" if delta_abs > 0 else (u"↓" if delta_abs < 0 else u"↔")

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
            'rcv_note': u'',
            'rcv_flag': u'',
            'spark_svg': spark,
        }
        return row

    def _fetch_series_for_ar(self, ar):
        """
        REEMPLAZAR con tu lógica real de búsqueda histórica
        Esta es solo una DEMO
        """
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
