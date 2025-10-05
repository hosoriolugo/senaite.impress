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
    VERSION 2: Con _extract_refdef_minmax CORREGIDO
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
        """Convierte 'rr' (str/dict/objeto) a (texto, low, high)."""
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

    # ---------- 3) REFERENCE DEFINITIONS - VERSIÓN CORREGIDA ----------
    def _extract_refdef_minmax(self, a):
        """
        Extrae min/max de ReferenceDefinitions - CORREGIDO para SENAITE 2.6
        """
        try:
            service = self._get_service(a)
            if not service:
                logger.warning("[impress] No service found for analysis")
                return None, None, None
            
            # Obtener identificadores del servicio
            keyword = None
            title = None
            service_uid = None
            
            try:
                keyword = getattr(service, "getKeyword", lambda: None)()
                title = getattr(service, "Title", lambda: None)() or getattr(service, "title", lambda: None)()
                service_uid = getattr(service, "UID", lambda: None)()
            except Exception as e:
                logger.warning("[impress] Error obteniendo info del servicio: %s", e)
                return None, None, None
            
            if not keyword and not title:
                logger.warning("[impress] Service sin keyword ni title")
                return None, None, None
            
            logger.info("[impress] Buscando RefDef para keyword='%s', title='%s', uid=%s", 
                       keyword, title, service_uid)
            
            # Buscar ReferenceDefinitions en el catálogo
            portal = self.context.portal_url.getPortalObject()
            catalog = getToolByName(portal, "portal_catalog")
            
            # Buscar todos los ReferenceDefinitions
            brains = catalog.searchResults(
                portal_type=["ReferenceDefinition", "BikaReferenceDefinition"],
                sort_on="created",
                sort_order="descending"
            )
            
            logger.info("[impress] Encontrados %d ReferenceDefinitions en catálogo", len(brains))
            
            for brain in brains:
                try:
                    obj = brain.getObject()
                    refdef_title = getattr(obj, "Title", lambda: "")() or getattr(obj, "title", "")
                    logger.info("[impress] Revisando ReferenceDefinition: '%s'", refdef_title)
                    
                    # Obtener valores de referencia
                    rows = None
                    for getter_name in ("getReferenceValues", "getResultsRange", "ReferenceValues", 
                                       "reference_values", "getValues", "results_range"):
                        fn = getattr(obj, getter_name, None)
                        if fn:
                            try:
                                rows = fn() if callable(fn) else fn
                                if rows:
                                    logger.info("[impress] Valores obtenidos via %s: %d filas", 
                                              getter_name, len(rows) if isinstance(rows, (list, tuple)) else 1)
                                    break
                            except Exception as e:
                                logger.debug("[impress] Error en %s: %s", getter_name, e)
                                continue
                    
                    if not rows:
                        logger.info("[impress] ReferenceDefinition '%s' sin valores", refdef_title)
                        continue
                    
                    # Si rows es un diccionario directo
                    if isinstance(rows, dict):
                        rows = [rows]
                    
                    # Iterar sobre las filas
                    for idx, row in enumerate(rows):
                        try:
                            # Extraer el servicio/keyword de la fila
                            row_keyword = None
                            row_service = None
                            row_service_uid = None
                            
                            if isinstance(row, dict):
                                # Caso diccionario
                                row_keyword = row.get("keyword") or row.get("Keyword")
                                row_service = row.get("Service") or row.get("service")
                                
                                # Si el servicio es un objeto
                                if row_service and hasattr(row_service, "getKeyword"):
                                    try:
                                        row_keyword = row_service.getKeyword()
                                        row_service_uid = row_service.UID()
                                    except:
                                        pass
                                elif isinstance(row_service, (str, unicode)):
                                    row_keyword = row_service
                            else:
                                # Caso objeto
                                for gk in ("getKeyword", "Keyword", "keyword", "getServiceKeyword"):
                                    gv = getattr(row, gk, None)
                                    row_keyword = gv() if callable(gv) else gv
                                    if row_keyword:
                                        break
                                
                                # Intentar obtener el servicio
                                for gs in ("getService", "Service", "service"):
                                    sv = getattr(row, gs, None)
                                    row_service = sv() if callable(sv) else sv
                                    if row_service:
                                        try:
                                            if hasattr(row_service, "getKeyword"):
                                                row_keyword = row_service.getKeyword()
                                            if hasattr(row_service, "UID"):
                                                row_service_uid = row_service.UID()
                                            if hasattr(row_service, "Title"):
                                                row_title = row_service.Title()
                                                if callable(row_title):
                                                    row_title = row_title()
                                                if not row_keyword:
                                                    row_keyword = row_title
                                        except:
                                            pass
                                        break
                            
                            logger.debug("[impress] Fila %d: row_keyword='%s', row_service_uid=%s", 
                                       idx, row_keyword, row_service_uid)
                            
                            # Comparar: keyword, title o UID del servicio
                            is_match = False
                            if row_service_uid and service_uid and row_service_uid == service_uid:
                                is_match = True
                                logger.info("[impress] Match por UID del servicio")
                            elif row_keyword:
                                if keyword and _to_unicode(row_keyword).strip().lower() == _to_unicode(keyword).strip().lower():
                                    is_match = True
                                    logger.info("[impress] Match por keyword: '%s'", keyword)
                                elif title and _to_unicode(row_keyword).strip().lower() == _to_unicode(title).strip().lower():
                                    is_match = True
                                    logger.info("[impress] Match por title: '%s'", title)
                            
                            if not is_match:
                                continue
                            
                            # MATCH ENCONTRADO - Extraer min/max
                            lo = hi = None
                            
                            if isinstance(row, dict):
                                lo = (row.get("min") or row.get("Min") or 
                                      row.get("minimum") or row.get("Minimum"))
                                hi = (row.get("max") or row.get("Max") or 
                                      row.get("maximum") or row.get("Maximum"))
                            else:
                                # Objeto: intentar todos los getters
                                for gl in ("getMin", "getMinimum", "Min", "Minimum", "min", "minimum"):
                                    lv = getattr(row, gl, None)
                                    lo = lv() if callable(lv) else (lo or lv)
                                    if lo is not None:
                                        break
                                
                                for gh in ("getMax", "getMaximum", "Max", "Maximum", "max", "maximum"):
                                    hv = getattr(row, gh, None)
                                    hi = hv() if callable(hv) else (hi or hv)
                                    if hi is not None:
                                        break
                            
                            if lo is not None or hi is not None:
                                logger.info("[impress] ENCONTRADO RefRange en '%s': min=%s, max=%s", 
                                          refdef_title, lo, hi)
                                return lo, hi, u"refdef:%s" % refdef_title
                            else:
                                logger.warning("[impress] Match encontrado pero sin min/max en fila %d", idx)
                        
                        except Exception as e:
                            logger.warning("[impress] Error procesando fila %d: %s", idx, e)
                            continue
                
                except Exception as e:
                    logger.warning("[impress] Error procesando ReferenceDefinition %s: %s", 
                                 brain.getPath(), e)
                    continue
            
            logger.info("[impress] No se encontró match en ningún ReferenceDefinition")
            return None, None, None
        
        except Exception as e:
            logger.exception("[impress] Error crítico en _extract_refdef_minmax: %s", e)
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
        """Devuelve (ref_text, low, high, src) usando prioridad CORRECTA para SENAITE 2.6+"""
        
        # PRIORIDAD 1: Analysis.getResultsRange() - CANÓNICO
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

        # PRIORIDAD 2: Analysis.getSpecification()
        try:
            spec = self._get(a, "getSpecification")
            if spec:
                results_range = self._get(spec, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    lo = results_range.get('min')
                    hi = results_range.get('max')
                    if lo is not None or hi is not None:
                        text = self._first_text_from_lo_hi(lo, hi)
                        logger.info("[impress] RefRange via Analysis.getSpecification().getResultsRange()")
                        return text, lo, hi, u"analysis.spec.resultsrange"
                
                lo = self._get(spec, "min") or self._get(spec, "Min")
                hi = self._get(spec, "max") or self._get(spec, "Max")
                if lo is not None or hi is not None:
                    text = self._first_text_from_lo_hi(lo, hi)
                    logger.info("[impress] RefRange via Analysis.getSpecification() directo")
                    return text, lo, hi, u"analysis.spec.direct"
        except Exception as e:
            logger.debug("[impress] Error en Analysis.getSpecification: %s", e)

        # PRIORIDAD 3: Service.getResultsRange()
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

        # PRIORIDAD 4: AR.getSpecification() con keyword
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

        # PRIORIDAD 5-9: Mantener métodos existentes
        service = self._get_service(a)
        kw = self._get(service, "getKeyword") if service else None
        if kw:
            dlo, dhi, dsrc = self._extract_dynamic_specs_minmax(a, kw)
            if dlo is not None or dhi is not None:
                txt = self._first_text_from_lo_hi(dlo, dhi)
                return txt, dlo, dhi, dsrc

        slo, shi, ssrc = self._extract_specs_minmax_for_analysis(a)
        if slo is not None or shi is not None:
            txt = self._first_text_from_lo_hi(slo, shi)
            return txt, slo, shi, ssrc

        rlo, rhi, rsrc = self._extract_refdef_minmax(a)
        if rlo is not None or rhi is not None:
            txt = self._first_text_from_lo_hi(rlo, rhi)
            return txt, rlo, rhi, rsrc

        alo, ahi, asrc = self._extract_analysis_or_service_minmax(a)
        if alo is not None or ahi is not None:
            txt = self._first_text_from_lo_hi(alo, ahi)
            return txt, alo, ahi, asrc

        sv_lo, sv_hi, sv_src = self._extract_service_refvalues(a)
        if sv_lo is not None or sv_hi is not None:
            txt = self._first_text_from_lo_hi(sv_lo, sv_hi)
            return txt, sv_lo, sv_hi, sv_src

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
