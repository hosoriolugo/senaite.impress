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
    from bika.lims import logger, api
except Exception:
    import logging
    logger = logging.getLogger("senaite.impress")
    api = None  # fallback protegido


def _to_unicode(v):
    try:
        return unicode(v)
    except Exception:
        try:
            return u"%s" % v
        except Exception:
            return u""


def _to_num(x):
    try:
        if x in (None, u"", ""):
            return None
        if isinstance(x, (int, long, float)):
            return float(x)
        s = _to_unicode(x).replace(",", ".")
        return float(s)
    except Exception:
        return None


class InfolabsaResultsWithState(BrowserView):
    """
    Renderiza la tabla 'cool' usando templates/results_with_state.pt
    VERSION 3: añade estados de workflow, alertas unificadas y rangos por edad/género
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
        """Devuelve (ar, sample, sampletype, client, contact, patient) si existen"""
        ar = getattr(a, "getAnalysisRequest", lambda: None)()
        sample = getattr(ar, "getSample", lambda: None)() if ar else None
        st = getattr(sample, "getSampleType", lambda: None)() if sample else None
        client = getattr(ar, "getClient", lambda: None)() if ar else None
        contact = getattr(ar, "getContact", lambda: None)() if ar else None
        patient = None
        for pa in ("getPatient", "Patient", "getRelatedPatient"):
            if hasattr(ar, pa):
                try:
                    patient = getattr(ar, pa)()
                    if patient:
                        break
                except Exception:
                    pass
        return ar, sample, st, client, contact, patient

    # ---------- extracción robusta de Resultado / Unidad ----------
    def _get_result(self, a):
        for g in ("getFormattedResult", "getResult", "Result", "result", "formatted_result", "getValue"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v
        return u"—"

    def _get_unit(self, a):
        for g in ("getUnit", "Unit", "unit", "getFormattedUnit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v
        return u""

    # ---------- keyword robusto (Service -> Analysis -> Title) ----------
    def _service_or_analysis_keyword(self, a):
        """Keyword robusto: Service.getKeyword() -> Analysis.getKeyword()/Title."""
        svc = self._get_service(a)
        kw = self._get(svc, "getKeyword") if svc else None
        if not kw:
            kw = (self._get(a, "getKeyword") or
                  self._get(a, "Keyword") or
                  self._get(a, "title") or
                  self._get(a, "Title"))
        return (self._u(kw).strip() if kw else None)

    # ---------- NUEVO: claves candidatas para AR.getSpecification ----------
    def _candidate_keys_for_spec(self, a):
        """
        Devuelve lista de claves candidatas para buscar en AR.getSpecification():
        - Service.getKeyword(), Service.Title(), Service.UID()
        - Analysis.getKeyword()/Title(), Analysis.UID()
        (normalizadas a texto unicode, sin espacios extremos)
        """
        keys = []
        svc = self._get_service(a)
        def add(x):
            if x not in (None, u""):
                keys.append(self._u(x).strip())

        if svc:
            add(self._get(svc, "getKeyword"))
            add(self._get(svc, "Title"))
            add(self._get(svc, "UID"))

        add(self._get(a, "getKeyword"))
        add(self._get(a, "Title"))
        add(self._get(a, "UID"))

        # Limpia duplicados conservando orden
        seen = set()
        uniq = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                uniq.append(k)
        return uniq

    # ---------- NUEVO: lookup robusto dentro de AR.getSpecification() ----------
    def _lookup_ar_spec_results_range(self, ar_spec, keys):
        """
        Intenta ar_spec.getResultsRange(<key>) para cada key candidata.
        Si encuentra dict con algo (comment/result/min/max), lo devuelve ya formateado.
        """
        try:
            getter = getattr(ar_spec, "getResultsRange", None)
            if not callable(getter):
                return None
            for k in keys:
                try:
                    rr = getter(k)
                    if isinstance(rr, dict):
                        text, lo, hi, _eq = self._format_results_range(rr)
                        if any(v not in (None, u"", "") for v in (text, lo, hi)):
                            logger.info("[impress] RefRange via AR.getSpecification().getResultsRange(key=%s)", k)
                            return text, lo, hi, k
                except Exception:
                    continue
        except Exception:
            pass
        return None

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

    # ---------- NUEVO: formateador compatible con “Especificación” ----------
    def _format_results_range(self, results_range):
        if not isinstance(results_range, dict):
            return u"", None, None, None
        comment = results_range.get("rangecomment") or results_range.get("comment")
        if comment not in (None, u"", ""):
            return self._u(comment), results_range.get("min"), results_range.get("max"), None
        eq_val = results_range.get("result") or results_range.get("value")
        if eq_val not in (None, u"", ""):
            return u"=" + self._u(eq_val), None, None, self._u(eq_val)
        lo = results_range.get("min")
        hi = results_range.get("max")
        hide_min = results_range.get("hidemin", "") == "on"
        hide_max = results_range.get("hidemax", "") == "on"
        lo_txt = (None if hide_min else lo)
        hi_txt = (None if hide_max else hi)
        text = self._first_text_from_lo_hi(lo_txt, hi_txt)
        return text, lo, hi, None

    # ---------- NUEVO: rangos por edad/género desde Service.getReferenceRanges ----------
    def _age_years(self, patient, ar):
        try:
            if patient and hasattr(patient, "getAge"):
                age = patient.getAge()
                if hasattr(age, "years"):
                    return int(age.years)
                if isinstance(age, (int, long)):
                    return int(age)
        except Exception:
            pass
        try:
            if api and patient:
                for fn in ("getDateOfBirth", "getBirthDate"):
                    dob = getattr(patient, fn, lambda: None)()
                    if dob:
                        today = api.to_datetime(api.date_now())
                        years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                        return max(0, int(years))
        except Exception:
            pass
        try:
            if ar and hasattr(ar, "getAge"):
                age = ar.getAge()
                if hasattr(age, "years"):
                    return int(age.years)
                if isinstance(age, (int, long)):
                    return int(age)
        except Exception:
            pass
        return None

    def _gender_code(self, patient, ar):
        raw = None
        for obj, attr in ((patient, "getGender"), (patient, "getSex"), (ar, "getGender"), (ar, "getSex")):
            if obj and hasattr(obj, attr):
                try:
                    raw = getattr(obj, attr)()
                    if raw:
                        break
                except Exception:
                    pass
        if not raw:
            return None
        s = self._u(raw).strip().lower()
        if s in ("m", "male", "masculino", "hombre"):
            return "male"
        if s in ("f", "female", "femenino", "mujer"):
            return "female"
        return None

    def _extract_service_reference_ranges_by_age_gender(self, a):
        svc = self._get_service(a)
        if not svc:
            return None, None, None
        rows = self._get(svc, "getReferenceRanges") or []
        if not rows:
            return None, None, None

        ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
        yrs = self._age_years(patient, ar)
        gcode = self._gender_code(patient, ar)

        def _ok(row):
            try:
                g = self._u(row.get("Gender", "")).strip().lower()
                if g and gcode and g != gcode:
                    return False
                amin = row.get("AgeMin")
                amax = row.get("AgeMax")
                if yrs is not None:
                    if amin not in (None, u"", "") and yrs < int(amin):
                        return False
                    if amax not in (None, u"", "") and yrs > int(amax):
                        return False
                return True
            except Exception:
                return True

        for rr in rows:
            if isinstance(rr, dict) and _ok(rr):
                lo = _to_num(rr.get("Min"))
                hi = _to_num(rr.get("Max"))
                if lo is not None or hi is not None:
                    txt = self._first_text_from_lo_hi(lo, hi)
                    return txt, lo, hi
        return None, None, None

    # ---------- 1) DINÁMICAS ----------
    def _extract_dynamic_specs_minmax(self, a, keyword):
        try:
            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
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
            keyword = self._service_or_analysis_keyword(a)
            if not keyword:
                return None, None, None

            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)

            candidates = []
            for holder, label in (
                (ar, "AR"),
                (client, "Client"),
                (contact, "Contact"),
                (st, "SampleType"),
                (self._get_service(a), "Service"),
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
                        if (k or u"").strip() == keyword:
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
                    logger.info("[impress] RefRange via AnalysisSpecifications (%s)", origin)
                    return lo, hi, u"spec"
        except Exception:
            pass
        return None, None, None

    # ---------- 3) REFERENCE DEFINITIONS (tu versión corregida) ----------
    def _extract_refdef_minmax(self, a):
        try:
            service = self._get_service(a)
            if not service:
                return None, None, None

            keyword = self._get(service, "getKeyword")
            title = self._get(service, "Title")
            service_uid = self._get(service, "UID")

            portal = self.context.portal_url.getPortalObject()
            catalog = getToolByName(portal, "portal_catalog")
            brains = catalog.searchResults(
                portal_type=["ReferenceDefinition", "BikaReferenceDefinition"],
                sort_on="created",
                sort_order="descending",
            )

            for brain in brains:
                try:
                    obj = brain.getObject()
                    rows = None
                    for getter_name in ("getReferenceValues", "getResultsRange", "ReferenceValues",
                                        "reference_values", "getValues", "results_range"):
                        fn = getattr(obj, getter_name, None)
                        if fn:
                            rows = fn() if callable(fn) else fn
                            if rows:
                                break
                    if not rows:
                        continue
                    if isinstance(rows, dict):
                        rows = [rows]
                    for row in rows:
                        try:
                            row_keyword = None
                            row_service = None
                            row_service_uid = None
                            if isinstance(row, dict):
                                row_keyword = row.get("keyword") or row.get("Keyword")
                                row_service = row.get("Service") or row.get("service")
                                if row_service and hasattr(row_service, "getKeyword"):
                                    row_keyword = row_service.getKeyword()
                                    if hasattr(row_service, "UID"):
                                        row_service_uid = row_service.UID()
                                elif isinstance(row_service, (str, unicode)):
                                    row_keyword = row_service
                            else:
                                for gk in ("getKeyword", "Keyword", "keyword", "getServiceKeyword"):
                                    gv = getattr(row, gk, None)
                                    row_keyword = gv() if callable(gv) else gv
                                    if row_keyword:
                                        break
                                for gs in ("getService", "Service", "service"):
                                    sv = getattr(row, gs, None)
                                    row_service = sv() if callable(sv) else sv
                                    if row_service:
                                        try:
                                            if hasattr(row_service, "getKeyword"):
                                                row_keyword = row_service.getKeyword()
                                            if hasattr(row_service, "UID"):
                                                row_service_uid = row_service.UID()
                                        except Exception:
                                            pass
                                        break

                            is_match = False
                            if row_service_uid and service_uid and row_service_uid == service_uid:
                                is_match = True
                            elif row_keyword:
                                if keyword and _to_unicode(row_keyword).strip().lower() == _to_unicode(keyword).strip().lower():
                                    is_match = True
                                elif title and _to_unicode(row_keyword).strip().lower() == _to_unicode(title).strip().lower():
                                    is_match = True
                            if not is_match:
                                continue

                            lo = hi = None
                            if isinstance(row, dict):
                                lo = (row.get("min") or row.get("Min") or row.get("minimum") or row.get("Minimum"))
                                hi = (row.get("max") or row.get("Max") or row.get("maximum") or row.get("Maximum"))
                            else:
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
                                return lo, hi, u"refdef"
                        except Exception:
                            continue
                except Exception:
                    continue
            return None, None, None
        except Exception as e:
            logger.exception("[impress] _extract_refdef_minmax error: %s", e)
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
                    lo = (row.get("min") or row.get("Min") or row.get("minimum") or row.get("Minimum"))
                    hi = (row.get("max") or row.get("Max") or row.get("maximum") or row.get("Maximum"))
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
                    return lo, hi, u"service.refvalues"
        except Exception:
            pass
        return None, None, None

    # ---------- PRIORIDAD para SENAITE 2.6 (ajustada para igualar “Especificación”) ----------
    def _compute_ref_range(self, a):
        """Devuelve (ref_text, low, high, src) usando prioridad razonable en 2.6"""

        # 0) Service.getReferenceRanges() por edad/género (muy usado)
        txt_ag, lo_ag, hi_ag = self._extract_service_reference_ranges_by_age_gender(a)
        if lo_ag is not None or hi_ag is not None:
            return (self._first_text_from_lo_hi(lo_ag, hi_ag), lo_ag, hi_ag, u"svc.refranges")

        # 1) Analysis.getResultsRange() canónico (formateo completo)
        try:
            results_range = self._get(a, "getResultsRange")
            if results_range and isinstance(results_range, dict):
                text, lo, hi, _eq = self._format_results_range(results_range)
                if any(v not in (None, u"", "") for v in (text, lo, hi)):
                    return text, lo, hi, u"analysis.getResultsRange"
        except Exception:
            pass

        # 2) Analysis.getSpecification() -> getResultsRange()
        try:
            spec = self._get(a, "getSpecification")
            if spec:
                results_range = self._get(spec, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    text, lo, hi, _eq = self._format_results_range(results_range)
                    if any(v not in (None, u"", "") for v in (text, lo, hi)):
                        return text, lo, hi, u"analysis.spec.resultsrange"
                lo = self._get(spec, "min") or self._get(spec, "Min")
                hi = self._get(spec, "max") or self._get(spec, "Max")
                if lo is not None or hi is not None:
                    text = self._first_text_from_lo_hi(lo, hi)
                    return text, lo, hi, u"analysis.spec.direct"
        except Exception:
            pass

        # 3) Service.getResultsRange() (formateo completo)
        try:
            service = self._get_service(a)
            if service:
                results_range = self._get(service, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    text, lo, hi, _eq = self._format_results_range(results_range)
                    if any(v not in (None, u"", "") for v in (text, lo, hi)):
                        return text, lo, hi, u"service.getResultsRange"
        except Exception:
            pass

        # 4) **AR.getSpecification() con claves candidatas**
        try:
            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
            if ar:
                ar_spec = self._get(ar, "getSpecification")
                if ar_spec:
                    keys = self._candidate_keys_for_spec(a)
                    hit = self._lookup_ar_spec_results_range(ar_spec, keys)
                    if hit:
                        text, lo, hi, k = hit
                        return text, lo, hi, u"ar.spec.key:%s" % k
        except Exception:
            pass

        # 5) dynamic/spec/refdef/analysis/service/refvalues (tus caminos)
        service = self._get_service(a)
        kw_any = self._service_or_analysis_keyword(a)
        if kw_any:
            dlo, dhi, dsrc = self._extract_dynamic_specs_minmax(a, kw_any)
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

        # Nada encontrado
        try:
            keyword = (kw_any if (kw_any not in (None, u"")) else
                       (self._get(service, "getKeyword") if service else "UNKNOWN"))
            title = self._get(a, "Title") or "UNKNOWN"
            uid = self._get(a, "UID")
            logger.warning("[impress] NO RANGO para '%s' (kw=%s, uid=%s)", title, keyword, uid)
        except Exception:
            pass
        return u"", None, None, u"not_found"

    # ---------- NUEVO: extraer 'result' (=) si existe, para exponer ref_eq ----------
    def _extract_ref_eq(self, a):
        try:
            rr = self._get(a, "getResultsRange")
            if isinstance(rr, dict):
                val = rr.get("result") or rr.get("value")
                if val not in (None, u"", ""):
                    return self._u(val)
        except Exception:
            pass
        try:
            spec = self._get(a, "getSpecification")
            rr = spec and self._get(spec, "getResultsRange")
            if isinstance(rr, dict):
                val = rr.get("result") or rr.get("value")
                if val not in (None, u"", ""):
                    return self._u(val)
        except Exception:
            pass
        try:
            svc = self._get_service(a)
            rr = svc and self._get(svc, "getResultsRange")
            if isinstance(rr, dict):
                val = rr.get("result") or rr.get("value")
                if val not in (None, u"", ""):
                    return self._u(val)
        except Exception:
            pass
        try:
            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
            ar_spec = ar and self._get(ar, "getSpecification")
            if ar_spec:
                keys = self._candidate_keys_for_spec(a)
                # Reusar lookup y solo extraer el "eq" del dict si lo hay
                getter = getattr(ar_spec, "getResultsRange", None)
                if callable(getter):
                    for k in keys:
                        try:
                            rr = getter(k)
                            if isinstance(rr, dict):
                                val = rr.get("result") or rr.get("value")
                                if val not in (None, u"", ""):
                                    return self._u(val)
                        except Exception:
                            continue
        except Exception:
            pass
        return None

    # ---------- estado de workflow del análisis ----------
    def _workflow_state(self, a):
        if api:
            try:
                st = api.get_workflow_status_of(a)
                if st:
                    return self._u(st)
            except Exception:
                pass
        for name in ("review_state", "getReviewState", "workflow_state"):
            v = self._get(a, name)
            if v:
                return self._u(v)
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
        estado_class = u''
        estado_symbol = u'—'
        estado_text = u'No aplica'

        v = _to_num(value)
        lo = _to_num(low)
        hi = _to_num(high)

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

        if is_critical:
            alert_text = (alert_text + (u'; ' if alert_text else u'') + u'Crítico').strip('; ')

        if not alert_text and estado_text == u'Fuera de rango':
            alert_text = u'Fuera de rango'

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

        # USAR LA PRIORIDAD ACTUALIZADA
        ref_text, low, high, ref_src = self._compute_ref_range(a)

        # Estado (workflow del análisis)
        wf_state = self._workflow_state(a) or u''

        is_critical = bool(self._get(a, 'getCritical', False) or self._get(a, 'isCritical', False))
        try:
            delta_flag = self._get(a, 'getDeltaFlag')
        except Exception:
            delta_flag = None

        estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title = \
            self._status_payload(result, low, high, is_critical=is_critical, delta_flag=delta_flag)

        # Unifica alertas en una sola salida visible
        alerts = alert_text or u'—'

        # Si no hay texto de rango pero sí low/high, constrúyelo
        if not ref_text and (low is not None or high is not None):
            ref_text = self._first_text_from_lo_hi(low, high)

        # NUEVO: exponer ref_eq si existe (para plantillas que lo lean)
        ref_eq = self._extract_ref_eq(a)

        return {
            # Display
            'name': name,
            'result': result,
            'unit': unit,

            # Rango de referencia
            'ref_range': ref_text or u'',
            'ref_low': low,
            'ref_high': high,
            'ref_src': ref_src or u'',
            'ref_eq': ref_eq,

            # Alias por compatibilidad con plantillas
            'reference_range': (ref_text or u''),
            'range_text': (ref_text or u''),
            'range': (ref_text or u''),

            'reference_low': low,
            'reference_high': high,

            # Estado “clínico”
            'estado_class': estado_class,
            'estado_symbol': estado_symbol,
            'estado_text': estado_text,

            # Estado de workflow
            'state': wf_state,
            'state_text': wf_state,
            'status': wf_state,
            'status_text': wf_state,

            # Alertas combinadas
            'alert_classes': alert_classes,
            'alert_text': alerts,
            'alert_title': alert_title,
            'alerts': alerts,
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
