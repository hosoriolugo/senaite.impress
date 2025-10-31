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
        # Python 2 long; en Py3 no existe, pero no rompe
        try:
            long  # noqa
        except Exception:
            long = int  # type: ignore
        if isinstance(x, (int, long, float)):
            return float(x)
        s = _to_unicode(x).replace(",", ".").strip()
        # Aceptar prefijos comparativos comunes
        if s and s[0] in (u"<", u">", u"≤", u"≥"):
            s = s[1:].strip()
        return float(s)
    except Exception:
        return None


# ------------------------- logging seguro en Py2 -------------------------

def _uformat(fmt, *args):
    """Interpolación segura en Unicode (evita UnicodeDecodeError en Py2 logging)."""
    ufmt = _to_unicode(fmt)
    if not args:
        return ufmt
    uargs = tuple(_to_unicode(a) for a in args)
    try:
        return ufmt % uargs
    except Exception:
        # Si el % falla por tipos raros, devolvemos fallback simple
        return ufmt + u" " + u" ".join(uargs)


def log_info(fmt, *args):
    try:
        logger.info(_uformat(fmt, *args))
    except Exception:
        pass


def log_warn(fmt, *args):
    try:
        logger.warning(_uformat(fmt, *args))
    except Exception:
        pass


def log_exc(fmt, *args):
    try:
        logger.exception(_uformat(fmt, *args))
    except Exception:
        pass


# ------------------------- helpers de presentación -------------------------

def _pretty_src(ref_src, debug=False):
    """Mapea el origen técnico a una etiqueta amable; oculta 'not_found' para usuario."""
    if not ref_src:
        return u""
    s = _to_unicode(ref_src or u"").strip().lower()
    if s in (u"not_found", u"unknown", u""):
        return u"" if not debug else u"origen: " + _to_unicode(ref_src)
    mapa = {
        u"patient.pipeline": u"ajustado por edad/género",
        u"linked.dx": u"especificación dinámica",
        u"linked.at": u"especificación",
        u"linked.spec": u"especificación",
        u"svc.refranges": u"servicio (edad/género)",
        u"analysis.getresultsrange": u"análisis",
        u"analysis.spec.resultsrange": u"especificación",
        u"analysis.spec.direct": u"especificación",
        u"service.getresultsrange": u"servicio",
        u"ar.spec.keyword": u"spec del AR",
        u"dynamic": u"especificación dinámica",
        u"spec": u"especificación",
        u"refdef": u"definición de referencia",
        u"analysis": u"análisis",
        u"service": u"servicio",
        u"service.refvalues": u"valores de referencia",
    }
    return mapa.get(s, u"" if not debug else u"origen: " + _to_unicode(ref_src))


def _format_ref_text_with_unit(ref_text, unit):
    """Devuelve el rango con unidad, sin duplicar ni dejar espacios raros."""
    t = _to_unicode(ref_text or u"").strip()
    u_ = _to_unicode(unit or u"").strip()
    if not t:
        return u""
    if u_ and not (t.endswith(u_) or t.endswith(u" " + u_)):
        return (t + u" " + u_).strip()
    return t


def _sanitize_unit_for_flags(unit):
    """
    Evita que 'L'/'H' en la unidad se confundan con banderas visuales (Low/High).
    Insertamos un "word joiner" U+2060 después de L/H cuando forman parte de la unidad.
    No altera cálculos; solo presentación.
    """
    if not unit:
        return unit
    u = _to_unicode(unit)
    # Casos típicos: /L, /H, por si hay equipos que marcan H (high) en unidades raras
    u = u.replace(u"/L", u"/L\u2060").replace(u"/H", u"/H\u2060")
    # También "por L" y variantes con espacios finos
    u = u.replace(u" L", u" L\u2060")
    return u


# ------------------------- helpers de fecha/hora para tendencia -------------------------

def _as_DateTime_any(x):
    """
    Convierte múltiples tipos a DateTime (con hora:min:seg) sin perder resolución.
    Acepta: Zope DateTime, datetime.*, num timestamp (segundos), str ISO, dict{'date'...'time'...}
    """
    if x is None or x == u"":
        return None
    try:
        if isinstance(x, DateTime):
            return x
    except Exception:
        pass
    # Zope DateTime admite construir desde ISO YYYY-MM-DD HH:MM:SS
    try:
        import datetime as _dt
        # python datetime
        if isinstance(x, _dt.datetime):
            return DateTime(x.year, x.month, x.day, x.hour, x.minute, x.second)
        if isinstance(x, _dt.date):
            # fecha sin hora -> dejamos 00:00:00 para conservar orden relativo
            return DateTime(x.year, x.month, x.day, 0, 0, 0)
    except Exception:
        pass
    # timestamp numérico
    try:
        if isinstance(x, (int, float)):
            return DateTime(x)
    except Exception:
        pass
    # dict con claves comunes
    if isinstance(x, dict):
        for key in (u"datetime", u"dt", u"x", u"date", u"sampled", u"verified", u"created"):
            v = x.get(key)
            if v:
                dt = _as_DateTime_any(v)
                if dt:
                    # ¿hay 'time' separado?
                    t = x.get(u"time")
                    if isinstance(t, (str, unicode)) and t and ":" in t:
                        try:
                            parts = [int(p) for p in t.split(":")]
                            while len(parts) < 3:
                                parts.append(0)
                            h, m, s = parts[:3]
                            return DateTime(dt.year(), dt.month(), dt.day(), h, m, s)
                        except Exception:
                            pass
                    return dt
    # cadena
    try:
        import re as _re
        s = _to_unicode(x).strip()
        if not s:
            return None
        # si solo trae fecha YYYY-MM-DD, no perdemos, ponemos 00:00:00
        if _re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            y, m, d = [int(p) for p in s.split("-")]
            return DateTime(y, m, d, 0, 0, 0)
        # Dejar que DateTime parsee cadenas variadas (incluye zona)
        return DateTime(s)
    except Exception:
        return None


def _sort_points_by_fulltime(points):
    """
    Orden estable por fecha+hora+seg, sin colapsar puntos del mismo día.
    Si varios puntos comparten el mismo segundo, se añade un micro-desplazamiento estable.
    """
    enriched = []
    for idx, p in enumerate(points):
        dt = None
        if isinstance(p, dict):
            # Campos de fecha más habituales
            for k in (u"datetime", u"dt", u"x", u"date", u"sampled", u"verified", u"created"):
                if k in p and p.get(k) not in (None, u"", ""):
                    dt = _as_DateTime_any(p.get(k))
                    if dt:
                        break
        if dt is None:
            # punto simple (num/string)
            dt = _as_DateTime_any(p)
        # fallback duro: si no hay nada, lo mandamos al final respetando orden
        if dt is None:
            key = (0, idx)
        else:
            try:
                key = (float(dt), idx)
            except Exception:
                key = (0, idx)
        enriched.append((key, p))
    enriched.sort(key=lambda x: x[0])
    return [p for _, p in enriched]


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

    # ---------- formateador ResultsRange ----------
    def _format_results_range(self, results_range):
        if not isinstance(results_range, dict):
            return u"", None, None, None

        u_ = self._u

        comment = (results_range.get("rangecomment")
                   or results_range.get("comment")
                   or results_range.get("RangeComment"))
        if comment not in (None, u"", ""):
            lo = (results_range.get("min") or results_range.get("Min")
                  or results_range.get("lower") or results_range.get("Lower")
                  or results_range.get("LowerLimit"))
            hi = (results_range.get("max") or results_range.get("Max")
                  or results_range.get("upper") or results_range.get("Upper")
                  or results_range.get("UpperLimit"))
            return u_(comment), lo, hi, None

        eq_val = (results_range.get("result") or results_range.get("value")
                  or results_range.get("Result") or results_range.get("Value"))
        if eq_val not in (None, u"", ""):
            return u"=" + u_(eq_val), None, None, u_(eq_val)

        lo = (results_range.get("min") or results_range.get("Min")
              or results_range.get("lower") or results_range.get("Lower")
              or results_range.get("LowerLimit"))
        hi = (results_range.get("max") or results_range.get("Max")
              or results_range.get("upper") or results_range.get("Upper")
              or results_range.get("UpperLimit"))

        hide_min = (results_range.get("hidemin") == "on"
                    or results_range.get("hide_min") == "on")
        hide_max = (results_range.get("hidemax") == "on"
                    or results_range.get("hide_max") == "on")

        lo_txt = (None if hide_min else lo)
        hi_txt = (None if hide_max else hi)
        text = self._first_text_from_lo_hi(lo_txt, hi_txt)
        return text, lo, hi, None

    # ---------- pipeline senaite.patient ----------
    def _get_patient_results_range(self, a):
        if api:
            for fname in ("get_results_range", "getResultsRangeFor"):
                try:
                    fn = getattr(api, fname, None)
                    if callable(fn):
                        rr = fn(a)
                        if isinstance(rr, dict):
                            text, lo, hi, _eq = self._format_results_range(rr)
                            if any(v not in (None, u"", "") for v in (text, lo, hi)):
                                return text, lo, hi
                except Exception:
                    pass

        for mname in ("getPatientResultsRange", "getDynamicResultsRange", "getFinalResultsRange"):
            try:
                rr = getattr(a, mname)()
                if isinstance(rr, dict):
                    text, lo, hi, _eq = self._format_results_range(rr)
                    if any(v not in (None, u"", "") for v in (text, lo, hi)):
                        return text, lo, hi
            except Exception:
                pass

        try:
            rr = getattr(a, "getResultsRange", None)
            rr = rr() if callable(rr) else None
            if isinstance(rr, dict):
                text, lo, hi, _eq = self._format_results_range(rr)
                if any(v not in (None, u"", "") for v in (text, lo, hi)):
                    return text, lo, hi
        except Exception:
            pass

        return u"", None, None

    # ---------- helpers para spec enlazada ----------
    def _age_days(self, patient, ar):
        try:
            if patient:
                for fn in ("getBirthDate", "getDateOfBirth"):
                    dob = getattr(patient, fn, lambda: None)()
                    if dob:
                        try:
                            dob = dob.asdatetime().date()
                        except Exception:
                            dob = getattr(dob, "date", lambda: dob)()
                        import datetime
                        today = datetime.date.today()
                        return (today - dob).days
        except Exception:
            pass
        yrs = self._age_years(patient, ar)
        try:
            if yrs is not None:
                return int(yrs) * 365
        except Exception:
            pass
        return None

    def _analysis_keyword(self, a):
        for name in ("getKeyword", "getId", "getServiceKeyword"):
            fn = getattr(a, name, None)
            if callable(fn):
                try:
                    v = fn()
                    if v:
                        return self._u(v)
                except Exception:
                    pass
        return u""

    def _patient_gender_MF(self, patient, ar):
        for obj, attr in ((patient, "getGender"), (patient, "getSex"), (ar, "getGender"), (ar, "getSex")):
            if obj and hasattr(obj, attr):
                try:
                    raw = getattr(obj, attr)()
                    if raw:
                        s = self._u(raw).strip().lower()
                        if s in ("m", "male", "masculino", "hombre"):
                            return u"M"
                        if s in ("f", "female", "femenino", "mujer"):
                            return u"F"
                        break
                except Exception:
                    pass
        return None

    def _get_aspec(self, analysis):
        fn = getattr(analysis, "getAnalysisSpec", None)
        if callable(fn):
            try:
                try:
                    return fn(create=False)
                except TypeError:
                    return fn()
            except Exception:
                pass
        try:
            schema = getattr(analysis, "Schema", lambda: None)()
        except Exception:
            schema = None
        if schema and "AnalysisSpec" in schema:
            try:
                return schema["AnalysisSpec"].get(analysis)
            except Exception:
                pass
        return None

    def _current_spec_linked(self, analysis):
        for kind, getter in (("dx", "getDynamicAnalysisSpec"), ("at", "getSpecification")):
            fn = getattr(analysis, getter, None)
            if callable(fn):
                try:
                    obj = fn()
                    if obj:
                        return kind, obj
                except Exception:
                    pass
        aspec = self._get_aspec(analysis)
        if aspec:
            for kind, getter in (("dx", "getDynamicAnalysisSpec"), ("at", "getSpecification")):
                fn = getattr(aspec, getter, None)
                if callable(fn):
                    try:
                        obj = fn()
                        if obj:
                            return kind, obj
                    except Exception:
                        pass
        return None, None

    def _rows_from_dx(self, dx):
        for attr in ("getRows", "getData", "get_data", "rows", "data"):
            val = getattr(dx, attr, None)
            if callable(val):
                try:
                    rows = val()
                except Exception:
                    rows = None
            else:
                rows = val
            if rows:
                return rows
        return []

    def _resolve_dx_row_for_analysis(self, dx, analysis, patient, ar):
        rows = self._rows_from_dx(dx)
        if not rows:
            return None

        kw = self._analysis_keyword(analysis).strip().upper()
        gender_MF = self._patient_gender_MF(patient, ar)
        age_days = self._age_days(patient, ar)

        client_uid = None
        if ar and getattr(ar, "aq_parent", None) and hasattr(ar.aq_parent, "UID"):
            try:
                client_uid = ar.aq_parent.UID()
            except Exception:
                pass
        sampletype_uid = self._get(analysis, "getSampleTypeUID")
        method_uid = self._get(analysis, "getMethodUID")

        def N(x):
            if x is None:
                return None
            try:
                return self._u(x).strip().upper()
            except Exception:
                return x

        candidates = []
        for r in rows:
            r_kw = N(r.get("Keyword") or r.get("keyword") or r.get("service_keyword"))
            if not r_kw or r_kw != kw:
                continue

            r_gender = r.get("gender")
            if r_gender:
                r_gender = N(r_gender)
                if gender_MF and r_gender not in (gender_MF, u"*", u"ANY", u"ALL"):
                    continue

            ok_age = True
            if age_days is not None:
                amin = _to_num(r.get("age_min_days") or r.get("age_min"))
                amax = _to_num(r.get("age_max_days") or r.get("age_max"))
                if amin is not None and age_days < amin:
                    ok_age = False
                if amax is not None and age_days > amax:
                    ok_age = False
            if not ok_age:
                continue

            def match_uid(field, given):
                rv = r.get(field) or r.get(field.capitalize()) or r.get(field.replace("_uid", "").title()+"UID")
                if not rv or not given:
                    return True
                return N(rv) == N(given)

            if not match_uid("client_uid", client_uid):
                continue
            if not match_uid("sampletype_uid", sampletype_uid):
                continue
            if not match_uid("method_uid", method_uid):
                continue

            candidates.append(r)

        return candidates[0] if candidates else None

    def _dict_from_dx_row(self, row):
        if not row:
            return None
        return {
            "unit": self._u(row.get("unit")) if row.get("unit") else None,
            "min": _to_num(row.get("min")),
            "max": _to_num(row.get("max")),
            "warn_low": _to_num(row.get("warn_low")),
            "warn_high": _to_num(row.get("warn_high")),
            "panic_low": _to_num(row.get("panic_low")),
            "panic_high": _to_num(row.get("panic_high")),
            "target": _to_num(row.get("target")),
            "notes": self._u(row.get("notes") or u"") or None,
            "_source": "dx",
        }

    def _dict_from_at(self, spec_at):
        getv = lambda o, n: getattr(o, "get" + n, lambda: None)()
        return {
            "unit": self._u(getv(spec_at, "Unit") or u"") or None,
            "min": _to_num(getv(spec_at, "Min")),
            "max": _to_num(getv(spec_at, "Max")),
            "warn_low": _to_num(getv(spec_at, "WarnLow")),
            "warn_high": _to_num(getv(spec_at, "WarnHigh")),
            "panic_low": _to_num(getv(spec_at, "PanicLow")),
            "panic_high": _to_num(getv(spec_at, "PanicHigh")),
            "target": _to_num(getv(spec_at, "Target")),
            "notes": self._u(getv(spec_at, "Notes") or u"") or None,
            "_source": "at",
        }

    def _compute_from_linked_spec(self, a):
        kind, obj = self._current_spec_linked(a)
        if not obj:
            return u"", None, None, None

        ar, sample, st, client, contact, patient = self._get_ar_ctx(a)

        if kind == "dx":
            row = self._resolve_dx_row_for_analysis(obj, a, patient, ar)
            dd = self._dict_from_dx_row(row)
            if dd:
                txt = self._first_text_from_lo_hi(dd.get("min"), dd.get("max"))
                unit = dd.get("unit")
                if unit:
                    txt = (txt + u" " + unit).strip()
                return txt, dd.get("min"), dd.get("max"), u"linked.dx"

        if kind == "at":
            dd = self._dict_from_at(obj)
            if dd and any(dd.get(k) is not None for k in ("min", "max", "target", "panic_low", "panic_high")):
                txt = self._first_text_from_lo_hi(dd.get("min"), dd.get("max"))
                unit = dd.get("unit")
                if not txt and dd.get("target") is not None:
                    txt = u"≈%s" % (self._u(int(dd.get("target"))) if dd.get("target") is not None else u"")
                if not txt and (dd.get("panic_low") is not None or dd.get("panic_high") is not None):
                    lo = dd.get("panic_low"); hi = dd.get("panic_high")
                    if lo is not None and hi is not None:
                        txt = self._first_text_from_lo_hi(lo, hi)
                    elif lo is not None:
                        txt = u"≥%s" % self._u(int(lo))
                    elif hi is not None:
                        txt = u"≤%s" % self._u(int(hi))
                if unit and txt:
                    txt = (txt + u" " + unit).strip()
                return txt, dd.get("min"), dd.get("max"), u"linked.at"

        return u"", None, None, None

    # ---------- rangos por edad/género desde Service.getReferenceRanges ----------
    def _age_years(self, patient, ar):
        try:
            if patient and hasattr(patient, "getAge"):
                age = patient.getAge()
                if hasattr(age, "years"):
                    return int(age.years)
                try:
                    long  # noqa
                except Exception:
                    long = int  # type: ignore
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
                try:
                    long  # noqa
                except Exception:
                    long = int  # type: ignore
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

    # ---------- 1) dinÁMICAS ----------
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
                    log_info(u"[impress] RefRange via DynamicSpecifications (%s) %s", origin, keyword)
                    return lo, hi, u"dynamic"
        except Exception:
            pass
        return None, None, None

    # ---------- 2) ANALYSIS SPECIFICATIONS ----------
    def _extract_specs_minmax_for_analysis(self, a):
        try:
            service = self._get_service(a)
            keyword = (getattr(service, "getKeyword", lambda: None)() if service else None)                       or self._get(a, "getKeyword")                       or self._get(a, "getServiceKeyword")
            if not keyword:
                return None, None, None
            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)

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
                    log_info(u"[impress] RefRange via AnalysisSpecifications (%s)", origin)
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
            log_exc(u"[impress] _extract_refdef_minmax error: %s", e)
            return None, None, None

    # ---------- 4) límites del análisis o del servicio ----------
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

    # ---------- prioridad SENAITE 2.6 ----------
    def _compute_ref_range(self, a):
        try:
            text_p, lo_p, hi_p = self._get_patient_results_range(a)
            if lo_p is not None or hi_p is not None or (text_p and text_p != u""):
                return text_p, lo_p, hi_p, u"patient.pipeline"
        except Exception:
            pass

        try:
            txt_l, lo_l, hi_l, src_l = self._compute_from_linked_spec(a)
            if lo_l is not None or hi_l is not None or (txt_l and txt_l != u""):
                return txt_l, lo_l, hi_l, src_l or u"linked.spec"
        except Exception:
            pass

        txt_ag, lo_ag, hi_ag = self._extract_service_reference_ranges_by_age_gender(a)
        if lo_ag is not None or hi_ag is not None:
            return (self._first_text_from_lo_hi(lo_ag, hi_ag), lo_ag, hi_ag, u"svc.refranges")

        try:
            results_range = self._get(a, "getResultsRange")
            if results_range and isinstance(results_range, dict):
                text, lo, hi, _eq = self._format_results_range(results_range)
                if any(v not in (None, u"", "") for v in (text, lo, hi)):
                    return text, lo, hi, u"analysis.getResultsRange"
        except Exception:
            pass

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

        try:
            ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
            if ar:
                ar_spec = self._get(ar, "getSpecification")
                if ar_spec:
                    service = self._get_service(a)
                    keyword = (self._get(service, "getKeyword") if service else None)                               or self._get(a, "getKeyword")                               or self._get(a, "getServiceKeyword")
                    if keyword and hasattr(ar_spec, "getResultsRange"):
                        rr = ar_spec.getResultsRange(keyword)
                        if rr and isinstance(rr, dict):
                            text, lo, hi, _eq = self._format_results_range(rr)
                            if any(v not in (None, u"", "") for v in (text, lo, hi)):
                                return text, lo, hi, u"ar.spec.keyword"
        except Exception:
            pass

        service = self._get_service(a)
        kw = (self._get(service, "getKeyword") if service else None)              or self._get(a, "getKeyword")              or self._get(a, "getServiceKeyword")
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
            keyword = ((self._get(service, "getKeyword") if service else None)
                       or self._get(a, "getKeyword") or "UNKNOWN")
            title = self._get(a, "Title") or "UNKNOWN"
            uid = self._get(a, "UID")
            log_warn(u"[impress] NO RANGO para '%s' (kw=%s, uid=%s)", title, keyword, uid)
        except Exception:
            pass
        return u"", None, None, u"not_found"

    # ---------- ref_eq (=) si existe ----------
    def _extract_ref_eq(self, a):
        try:
            if api and hasattr(api, "get_results_range"):
                rr = api.get_results_range(a)
                if isinstance(rr, dict):
                    v = rr.get("result") or rr.get("value")
                    if v not in (None, u"", ""):
                        return self._u(v)
        except Exception:
            pass
        for mname in ("getPatientResultsRange", "getDynamicResultsRange", "getFinalResultsRange", "getResultsRange"):
            try:
                rr = getattr(a, mname)() if hasattr(a, mname) else None
                if isinstance(rr, dict):
                    v = rr.get("result") or rr.get("value")
                    if v not in (None, u"", ""):
                        return self._u(v)
            except Exception:
                pass

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
                svc = self._get_service(a)
                keyword = self._get(svc, "getKeyword") if svc else None
                if keyword and hasattr(ar_spec, "getResultsRange"):
                    rr = ar_spec.getResultsRange(keyword)
                    if isinstance(rr, dict):
                        val = rr.get("result") or rr.get("value")
                        if val not in (None, u"", ""):
                            return self._u(val)
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

    def _workflow_viz(self, wf_state):
        """
        Mapea el estado interno a etiqueta/icono/clase UI.
        No cambia texto fuente; solo la presentación.
        """
        s = (self._u(wf_state) or u"").strip().lower()
        # Etiqueta, Icono, Clase
        mapping = {
            # preliminares / recepción / asignación
            "to_be_verified": (u"Preliminar", u"○", u"wf-pre"),
            "assigned":       (u"Preliminar", u"○", u"wf-pre"),
            "sample_received":(u"Preliminar", u"○", u"wf-pre"),
            # verificado
            "verified":       (u"Validado",   u"✓", u"wf-ok"),
            # publicado/final
            "published":      (u"Final",      u"■", u"wf-final"),
            "final":          (u"Final",      u"■", u"wf-final"),
            # en proceso / acciones pendientes
            "retest":         (u"En proceso", u"…", u"wf-proc"),
            "attachment_due": (u"En proceso", u"…", u"wf-proc"),
            "awaiting":       (u"En proceso", u"…", u"wf-proc"),
            # retractado
            "retracted":      (u"Retractado", u"↩︎", u"wf-ret"),
            # cancelado / inválido / rechazado
            "cancelled":      (u"Anulado",    u"×", u"wf-cancel"),
            "rejected":       (u"Anulado",    u"×", u"wf-cancel"),
            "invalid":        (u"Anulado",    u"×", u"wf-cancel"),
        }
        # default
        return mapping.get(s, (wf_state or u"", u"•", u"wf-unk"))

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

        # Anexar (sin romper) alertas adicionales si el análisis trae flags conocidos
        extra = self._extra_alerts_from_flags(value)
        if extra:
            alert_text = (alert_text + (u'; ' if alert_text else u'') + extra).strip('; ')

        return estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title

    def _extra_alerts_from_flags(self, value):
        """
        Gancho suave para sumar alertas tipo ND/LOQ/LOD, sin interferir con lo existente.
        Si no aplica, retorna ''.
        """
        txt = self._u(value or u"").strip().upper()
        if not txt:
            return u""
        # Casos típicos de guías: ND (no detectable), <LOQ, <LOD
        if txt in (u"ND", u"NO DETECTABLE"):
            return u"ND"
        if txt.startswith(u"<LOQ") or u" LOQ" in txt:
            return u"<LOQ"
        if txt.startswith(u"<LOD") or u" LOD" in txt:
            return u"<LOD"
        return u""

    # --------- tendencia (con fallback a catálogo) ----------
    def _trend_points(self, a):
        """
        VERSIÓN CORREGIDA - Maneja correctamente múltiples estudios el mismo día
        """
        providers = ("getTrendData", "getHistoricalResults", "getResultsHistory", "getTrendPoints")
        pts = []
        for name in providers:
            fn = getattr(a, name, None)
            if callable(fn):
                try:
                    cand = fn()
                    if cand:
                        try:
                            pts = list(cand)
                        except Exception:
                            pts = cand
                        break
                except Exception:
                    continue

        def _normalize(points):
            pts_sorted = _sort_points_by_fulltime(points or [])
            norm = []
            seen_timestamps = set()
            
            for p in pts_sorted:
                if isinstance(p, dict):
                    d = dict(p)
                else:
                    try:
                        x, y = p[0], p[1]
                    except Exception:
                        x, y = (p, None)
                    d = {'x': x, 'y': y}

                # Obtener timestamp más preciso posible
                dt = None
                for field in ['ms', 'x', 'date', 'datetime', 'sampled', 'verified']:
                    if field in d:
                        dt = _as_DateTime_any(d[field])
                        if dt:
                            break
                
                if dt is None:
                    continue

                # Crear timestamp único incluyendo hora, minuto y segundo
                timestamp_key = (
                    dt.year(), dt.month(), dt.day(), 
                    dt.hour(), dt.minute(), dt.second()
                )
                
                # Si ya existe este timestamp, agregar un segundo artificial
                original_timestamp = timestamp_key
                counter = 0
                while timestamp_key in seen_timestamps:
                    counter += 1
                    # Agregar segundos artificiales manteniendo el orden
                    timestamp_key = original_timestamp[:-1] + (original_timestamp[-1] + counter,)
                
                seen_timestamps.add(timestamp_key)
                
                # Convertir a milisegundos para el gráfico
                base_ms = int(float(dt) * 1000.0)
                unique_ms = base_ms + (counter * 1000)  # Agregar milisegundos para diferenciar

                # Normalizar valor Y
                y_val = d.get('y') or d.get('value')
                if y_val is None and 'result' in d:
                    y_val = d.get('result')
                
                y_val = _to_num(y_val)
                if y_val is None:
                    continue

                d['ms'] = unique_ms
                d['x'] = unique_ms
                d['y'] = y_val
                d.setdefault('sid', d.get('sid') or d.get('sample_id') or d.get('id') or '')
                norm.append(d)
            
            return norm

        norm = _normalize(pts)

        # RECONSTRUCCIÓN DESDE CATÁLOGO - VERSIÓN CORREGIDA
        if len(norm) < 2:
            try:
                ar, sample, st, client, contact, patient = self._get_ar_ctx(a)
                
                # Obtener paciente UID de forma más robusta
                patient_uid = None
                if patient and hasattr(patient, "UID"):
                    try:
                        patient_uid = patient.UID()
                    except Exception:
                        pass
                
                if not patient_uid and ar:
                    for attr in ["getPatientUID", "PatientUID", "patient_uid"]:
                        try:
                            patient_uid = getattr(ar, attr, lambda: None)()
                            if patient_uid:
                                break
                        except Exception:
                            pass

                # Obtener keyword del servicio de forma más precisa
                svc = self._get_service(a)
                keyword = None
                if svc:
                    for attr in ["getKeyword", "Keyword", "keyword"]:
                        try:
                            keyword = getattr(svc, attr, lambda: None)()
                            if keyword:
                                break
                        except Exception:
                            pass
                
                if not keyword:
                    for attr in ["getKeyword", "Keyword", "keyword", "getServiceKeyword"]:
                        try:
                            keyword = getattr(a, attr, lambda: None)()
                            if keyword:
                                break
                        except Exception:
                            pass
                
                keyword = self._u(keyword).strip().upper() if keyword else u""

                if patient_uid and keyword:
                    portal = self.context.portal_url.getPortalObject()
                    catalog = getToolByName(portal, "portal_catalog")
                    
                    # Buscar por paciente y ordenar por fecha de muestreo
                    query = {
                        "portal_type": "AnalysisRequest",
                        "getPatientUID": patient_uid,
                        "sort_on": "getDateSampled",  # Usar fecha de muestreo, no creación
                        "sort_order": "ascending",
                    }
                    
                    # En Senaite 2.6, algunos campos pueden tener nombres diferentes
                    try:
                        ar_brains = catalog(**query)
                    except Exception:
                        # Fallback: buscar sin filtro de paciente UID si falla
                        query.pop("getPatientUID", None)
                        ar_brains = catalog(**query)

                    fb_pts = []
                    for br in ar_brains:
                        try:
                            ar_obj = br.getObject()
                            
                            # Verificar que este AR pertenece al paciente
                            current_patient_uid = None
                            for attr in ["getPatientUID", "PatientUID", "patient_uid"]:
                                try:
                                    current_patient_uid = getattr(ar_obj, attr, lambda: None)()
                                    if current_patient_uid == patient_uid:
                                        break
                                except Exception:
                                    pass
                            
                            if current_patient_uid != patient_uid:
                                continue

                            # Obtener análisis del AR
                            ana_list = []
                            for g in ("getAnalyses", "analyses", "getAnalysis"):
                                v = getattr(ar_obj, g, None)
                                if callable(v):
                                    try:
                                        ana_list = v(full_objects=True)
                                    except TypeError:
                                        ana_list = v()
                                    if ana_list:
                                        break
                            
                            if not ana_list:
                                continue

                            for an in ana_list:
                                try:
                                    # Verificar keyword del análisis
                                    current_keyword = None
                                    current_svc = self._get_service(an)
                                    if current_svc:
                                        for attr in ["getKeyword", "Keyword", "keyword"]:
                                            try:
                                                current_keyword = getattr(current_svc, attr, lambda: None)()
                                                if current_keyword:
                                                    break
                                            except Exception:
                                                pass
                                    
                                    if not current_keyword:
                                        for attr in ["getKeyword", "Keyword", "keyword", "getServiceKeyword"]:
                                            try:
                                                current_keyword = getattr(an, attr, lambda: None)()
                                                if current_keyword:
                                                    break
                                            except Exception:
                                                pass
                                    
                                    current_keyword = self._u(current_keyword).strip().upper() if current_keyword else u""
                                    if current_keyword != keyword:
                                        continue

                                    # Obtener resultado numérico
                                    res = self._get_result(an)
                                    y = _to_num(res)
                                    if y is None:
                                        continue

                                    # Obtener fecha de muestreo más precisa
                                    dt = (self._get(an, "getDateSampled") or
                                          self._get(ar_obj, "getDateSampled") or
                                          self._get(an, "getDateVerified") or
                                          self._get(ar_obj, "getDatePublished") or
                                          self._get(an, "creation_date") or
                                          self._get(ar_obj, "creation_date"))
                                    
                                    dt = _as_DateTime_any(dt)
                                    if not dt:
                                        continue
                                        
                                    ms = int(float(dt) * 1000.0)
                                    sid = self._get(ar_obj, "getId") or self._get(ar_obj, "getSampleID") or u""
                                    
                                    fb_pts.append({
                                        'x': ms, 
                                        'y': y, 
                                        'sid': sid,
                                        'date': dt,
                                        'ar_id': ar_obj.getId()
                                    })
                                    
                                except Exception:
                                    continue
                        except Exception:
                            continue

                    # Aplicar normalización a los puntos reconstruidos
                    norm_fb = _normalize(fb_pts)
                    if len(norm_fb) >= 2:
                        log_info(u"[infolabsa] Tendencia reconstruida: %s puntos para %s", len(norm_fb), keyword)
                        return norm_fb
                        
            except Exception as e:
                log_exc(u"[infolabsa] Error en reconstrucción de tendencia: %s", e)

        return norm

    def row(self, a):
        name = (
            self._get(a, 'Title') or
            self._get(a, 'title') or
            self._get(a, 'getKeyword') or
            u''
        )

        result = self._get_result(a)

        unit_raw = self._get_unit(a)
        unit = _sanitize_unit_for_flags(unit_raw)  # <- evita "L" naranja en mg/L

        # Prioridad actualizada para rangos
        ref_text, low, high, ref_src = self._compute_ref_range(a)

        try:
            log_info(u"[impress] RefRange SRC=%s → %s (lo=%s hi=%s)", ref_src, ref_text or u"", low, high)
        except Exception:
            pass

        wf_state = self._workflow_state(a) or u''
        wf_label, wf_icon, wf_class = self._workflow_viz(wf_state)  # <- Estado profesional de workflow

        is_critical = bool(self._get(a, 'getCritical', False) or self._get(a, 'isCritical', False))
        try:
            delta_flag = self._get(a, 'getDeltaFlag')
        except Exception:
            delta_flag = None

        estado_class, estado_symbol, estado_text, alert_classes, alert_text, alert_title =             self._status_payload(result, low, high, is_critical=is_critical, delta_flag=delta_flag)

        alerts = alert_text or u'—'

        if not ref_text and (low is not None or high is not None):
            ref_text = self._first_text_from_lo_hi(low, high)

        ref_eq = self._extract_ref_eq(a)

        # --- saneo visual de origen y rango con unidad ---
        ref_src_label = _pretty_src(ref_src, debug=bool(self.request.get('debug_refsrc')))
        ref_src_display = ref_src_label
        reference_range_with_unit = _format_ref_text_with_unit(ref_text, unit)

        # Tendencia: construir pares XY y criterio robusto
        tpoints = self._trend_points(a)
        xy_points = [[p['x'], p['y']] for p in tpoints if p.get('y') is not None]

        # Mostrar gráfico SOLO si hay al menos 2 puntos numéricos
        can_plot_trend = bool(len(xy_points) >= 2)

        return {
            # Display
            'name': name,
            'result': result,
            'unit': unit,           # <- segura para pintar
            'unit_raw': unit_raw,   # <- original por si se necesita

            # Rango de referencia
            'ref_range': ref_text or u'',
            'ref_low': low,
            'ref_high': high,
            'ref_src_raw': ref_src or u'',
            'ref_src': ref_src_display or u'',
            'ref_src_label': ref_src_label,
            'ref_eq': ref_eq,

            # Alias por compatibilidad con plantillas
            'reference_range': (ref_text or u''),
            'reference_range_with_unit': reference_range_with_unit,
            'range_text': (ref_text or u''),
            'range': (ref_text or u''),

            'reference_low': low,
            'reference_high': high,

            # Estado clínico (rango)
            'estado_class': estado_class,
            'estado_symbol': estado_symbol,
            'estado_text': estado_text,

            # Estado de workflow (para columna Estado)
            'state': wf_state,            # crudo
            'state_text': wf_state,
            'status': wf_state,
            'status_text': wf_state,

            # Etiquetas "bonitas" del workflow (usar estas en la columna Estado)
            'state_label': wf_label,
            'state_icon': wf_icon,
            'state_class': wf_class,

            # Alias histórico (si alguna plantilla usa 'estado' para mostrar algo)
            'estado': estado_text,

            # Alertas combinadas
            'alert_classes': alert_classes,
            'alert_text': alerts,
            'alert_title': alert_title,
            'alerts': alerts,
            'alert': alerts,

            # Tendencia
            'trend_points': tpoints,      # puntos normalizados
            'trend_xy': xy_points,        # pares [ms, y] listos para Highcharts
            'can_plot_trend': can_plot_trend,
        }

    def rows(self):
        return [self.row(a) for a in self.analyses()]

    # ------------------------- rendering -------------------------
    def __call__(self):
        # Decide JSON output if caller explicitly asks (?format=json),
        # negotiates via Accept header, or is an XHR request.
        try:
            fmt = (self.request.get('format', u'') or u'').lower()
        except Exception:
            fmt = u''
        try:
            accept = (self.request.getHeader('Accept') or u'').lower()
        except Exception:
            accept = u''
        try:
            xrw = (self.request.getHeader('X-Requested-With') or u'').lower()
        except Exception:
            xrw = u''

        wants_json = (fmt == u'json') or (u'application/json' in accept) or (xrw == u'xmlhttprequest')

        if wants_json:
            try:
                import json
                data = {'items': self.rows()}
                self.request.response.setHeader('Content-Type', 'application/json; charset=utf-8')
                return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            except Exception as exc:
                try:
                    # Fallback: JSON de error controlado (no romper al cliente)
                    import json
                    self.request.response.setStatus(500)
                    self.request.response.setHeader('Content-Type', 'application/json; charset=utf-8')
                    return json.dumps({'ok': False, 'error': _uformat(u'%s', exc)}, ensure_ascii=False)
                except Exception:
                    pass  # si hasta aquí falla, caer al HTML

        log_info(u"[infolabsa] Render COOL table via results_with_state.pt")
        return self.index()



# Compatibilidad
InfolabsaResults = InfolabsaResultsWithState
