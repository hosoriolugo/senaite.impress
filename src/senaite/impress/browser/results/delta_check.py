# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from DateTime import DateTime

try:
    from bika.lims import logger
except Exception:
    import logging
    logger = logging.getLogger("senaite.impress")


def _u(x):
    try:
        return x if isinstance(x, unicode) else unicode(x)
    except Exception:
        try:
            return u"%s" % x
        except Exception:
            return u""


def _num(x):
    try:
        if x is None or x == u"":
            return None
        s = _u(x).replace(u",", u"").strip()
        return float(s)
    except Exception:
        return None


def _safe_get(obj, name, default=None):
    attr = getattr(obj, name, None)
    if callable(attr):
        try:
            return attr()
        except Exception:
            return default
    return attr if attr is not None else default


def _as_dt(v):
    """Convierte a Zope DateTime de forma tolerante."""
    try:
        if isinstance(v, DateTime):
            return v
        if isinstance(v, (tuple, list)) and v:
            return _as_dt(v[0])
        return DateTime(v)
    except Exception:
        return DateTime()


def _fmt_date(dt):
    try:
        return _as_dt(dt).strftime("%d/%m/%Y")
    except Exception:
        return u""


class InfolabsaDeltaCheck(BrowserView):
    """
    Devuelve {'period_label': '6/12 meses', 'rows': [...]} usando HOY como corte.
    COMPATIBLE con el sistema de rangos de referencia.
    """

    # ---------- utilidades internas ----------
    def _now(self):
        return DateTime()

    def _patient_keys(self, ar):
        """Devuelve (patient_uid, mrn/ClientPatientID) para búsqueda."""
        patient = _safe_get(ar, "getPatient", None)
        uid = None
        mrn = (_safe_get(ar, "getMedicalRecordNumberValue", None) or
               _safe_get(ar, "getMedicalRecordNumber", None) or
               _safe_get(ar, "getMRN", None) or
               _safe_get(ar, "getClientPatientID", None))
        if patient:
            try:
                uid = patient.UID()
            except Exception:
                uid = None
            if not mrn:
                mrn = (_safe_get(patient, "getMRN", None) or
                       _safe_get(patient, "getMedicalRecordNumber", None) or
                       _safe_get(patient, "getClientPatientID", None))
        mrn = _u(mrn).strip() if mrn else None
        return uid, mrn

    def _pick_period_days(self, ar):
        """Si hay ≥2 AR en 180 días => 6 meses; si no => 12 meses."""
        try:
            pc = getattr(ar, "portal_catalog", None)
            patient = _safe_get(ar, "getPatient", None)
            if not pc or not patient:
                return 365
            try:
                patient_uid = patient.UID()
            except Exception:
                return 365
            now = self._now()
            brains = pc.searchResults(
                portal_type="AnalysisRequest",
                getPatientUID=patient_uid,
                created={"query": [now - 365, now], "range": "min:max"},
            )
            within_180 = [b for b in (brains or []) if (now - b.created) <= 180]
            return 180 if len(within_180) >= 2 else 365
        except Exception:
            return 365

    def _fetch_prev_for_keyword(self, pc, keyword, until_date, current_ar_uid=None,
                                patient_uid=None, mrn=None, debug=None):
        """Encuentra un AR previo con el mismo keyword en la ventana temporal."""
        if not pc or not keyword:
            return None, None, None

        def iter_candidates(days):
            base = dict(
                portal_type="AnalysisRequest",
                sort_on="created",
                sort_order="descending",
                created={"query": [until_date - days, until_date], "range": "min:max"},
            )
            brains = []
            # 1) por UID de paciente
            if patient_uid:
                b1 = pc.searchResults(getPatientUID=patient_uid, **base)
                brains.extend(list(b1 or []))
            # 2) fallback por MRN/ClientPatientID
            if mrn:
                b2 = pc.searchResults(getClientPatientID=mrn, **base)
                if b2:
                    seen = {b.UID for b in brains}
                    brains.extend([b for b in b2 if b.UID not in seen])
            return brains

        try:
            for days in (180, 365):
                for b in iter_candidates(days):
                    try:
                        if current_ar_uid and getattr(b, "UID", None) == current_ar_uid:
                            continue
                        ar_prev = b.getObject()
                        analyses = (_safe_get(ar_prev, "getAnalyses", None) or
                                    _safe_get(ar_prev, "analyses", None) or [])
                        for a in analyses:
                            kw = (_safe_get(a, "getKeyword", None) or
                                  _safe_get(a, "Title", None))
                            if not kw:
                                continue
                            if _u(kw).strip().lower() == _u(keyword).strip().lower():
                                val = (_safe_get(a, "getResult", None) or
                                       _safe_get(a, "Result", None) or
                                       _safe_get(a, "getFormattedResult", None))
                                pid = (_safe_get(ar_prev, "getId", None) or
                                       _safe_get(ar_prev, "id", None))
                                return ar_prev, pid, val
                    except Exception as e:
                        if debug is not None:
                            debug.append(u"Error revisando candidato: {}".format(_u(e)))
                        continue
        except Exception as e:
            if debug is not None:
                debug.append(u"Fallo en _fetch_prev_for_keyword: {}".format(_u(e)))
        return None, None, None

    # ---------- EXTRACCIÓN DE RANGOS (COMPATIBLE CON infolabsa_results.py) ----------
    def _get_ref_range_for_analysis(self, a):
        """
        Extrae rango de referencia del análisis usando la misma lógica que infolabsa_results.py
        Devuelve (low, high) numéricos
        """
        # Prioridad 1: getResultsRange() del Analysis
        try:
            results_range = _safe_get(a, "getResultsRange")
            if results_range and isinstance(results_range, dict):
                lo = results_range.get('min')
                hi = results_range.get('max')
                hide_min = results_range.get('hidemin', '') == 'on'
                hide_max = results_range.get('hidemax', '') == 'on'
                
                if not hide_min and not hide_max and (lo is not None or hi is not None):
                    return _num(lo), _num(hi)
        except Exception:
            pass

        # Prioridad 2: getSpecification() del Analysis
        try:
            spec = _safe_get(a, "getSpecification")
            if spec:
                results_range = _safe_get(spec, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    lo = results_range.get('min')
                    hi = results_range.get('max')
                    if lo is not None or hi is not None:
                        return _num(lo), _num(hi)
                
                lo = _safe_get(spec, "min") or _safe_get(spec, "Min")
                hi = _safe_get(spec, "max") or _safe_get(spec, "Max")
                if lo is not None or hi is not None:
                    return _num(lo), _num(hi)
        except Exception:
            pass

        # Prioridad 3: Service.getResultsRange()
        try:
            service = _safe_get(a, "getService")
            if service:
                results_range = _safe_get(service, "getResultsRange")
                if results_range and isinstance(results_range, dict):
                    lo = results_range.get('min')
                    hi = results_range.get('max')
                    hide_min = results_range.get('hidemin', '') == 'on'
                    hide_max = results_range.get('hidemax', '') == 'on'
                    
                    if not hide_min and not hide_max and (lo is not None or hi is not None):
                        return _num(lo), _num(hi)
        except Exception:
            pass

        # Fallback: límites directos
        for name in ("getLowerLimit", "getMin", "getMinimum", "lower_limit"):
            lo = _safe_get(a, name)
            if lo is not None:
                break
        else:
            lo = None
            
        for name in ("getUpperLimit", "getMax", "getMaximum", "upper_limit"):
            hi = _safe_get(a, name)
            if hi is not None:
                break
        else:
            hi = None

        if lo is not None or hi is not None:
            return _num(lo), _num(hi)

        return None, None

    def _calculate_rcv(self, v_now, v_prev, ref_low, ref_high):
        """
        Calcula Reference Change Value (RCV) para determinar si el delta es significativo
        RCV = delta / ref_range_width
        """
        if v_now is None or v_prev is None:
            return None, u""
        
        if ref_low is None and ref_high is None:
            return None, u""
        
        delta = abs(v_now - v_prev)
        
        # Calcular ancho del rango
        if ref_low is not None and ref_high is not None:
            range_width = abs(ref_high - ref_low)
        elif ref_low is not None:
            range_width = abs(v_now - ref_low)  # Usar valor actual como referencia
        elif ref_high is not None:
            range_width = abs(ref_high - v_now)
        else:
            return None, u""
        
        if range_width == 0:
            return None, u""
        
        rcv = (delta / range_width) * 100.0
        
        # Clasificar
        if rcv > 50:
            return rcv, u"CRÍTICO"
        elif rcv > 25:
            return rcv, u"ALTO"
        elif rcv > 10:
            return rcv, u"MODERADO"
        else:
            return rcv, u"NORMAL"

    # ---------- API principal ----------
    def rows(self, debug=None):
        ar = self.context
        items = (_safe_get(ar, "getAnalyses", None) or
                 _safe_get(ar, "analyses", None) or [])
        rows = []

        now = self._now()
        days = self._pick_period_days(ar)
        period_label = u"6 meses" if days <= 180 else u"12 meses"

        pc = getattr(ar, "portal_catalog", None)
        patient_uid, mrn = self._patient_keys(ar)
        current_ar_uid = getattr(ar, "UID", None)
        if callable(current_ar_uid):
            current_ar_uid = current_ar_uid()

        if debug is not None:
            debug.append(u"Paciente UID: {}".format(patient_uid or u"—"))
            debug.append(u"MRN fallback: {}".format(mrn or u"—"))
            debug.append(u"Nº de análisis en AR: {}".format(len(items)))

        for a in items:
            try:
                name = (_safe_get(a, "Title", None) or
                        _safe_get(a, "getKeyword", None) or u"")
                keyword = _safe_get(a, "getKeyword", None) or name
                unit = (_safe_get(a, "getUnit", None) or
                        _safe_get(a, "Unit", None) or u"")
                val_now = (_safe_get(a, "getResult", None) or
                           _safe_get(a, "Result", None) or
                           _safe_get(a, "getFormattedResult", None))
                v_now = _num(val_now)

                # OBTENER RANGOS DE REFERENCIA
                ref_low, ref_high = self._get_ref_range_for_analysis(a)

                prev_id, prev_date_txt, v_prev = u"—", u"—", None
                delta_txt, delta_dir = u"N/A", u"•"
                rcv_value, rcv_flag = None, u""

                if pc and keyword:
                    ar_prev, pid, prev_val = self._fetch_prev_for_keyword(
                        pc, keyword, until_date=now,
                        current_ar_uid=current_ar_uid,
                        patient_uid=patient_uid, mrn=mrn, debug=debug
                    )
                    if ar_prev:
                        prev_id = pid or u"—"
                        prev_dt = (_safe_get(ar_prev, "getDatePublished", None) or
                                   _safe_get(ar_prev, "created", None) or
                                   _safe_get(ar_prev, "getDateReceived", None))
                        prev_date_txt = _fmt_date(prev_dt) or u"—"
                        v_prev = _num(prev_val)

                if v_now is not None and v_prev is not None and v_prev != 0:
                    dpct = ((v_now - v_prev) / abs(v_prev)) * 100.0
                    delta_dir = u"▲" if dpct > 0 else (u"▼" if dpct < 0 else u"=")
                    delta_txt = u"{:+.1f}%".format(dpct)
                    
                    # Calcular RCV si hay rangos
                    rcv_value, rcv_flag = self._calculate_rcv(v_now, v_prev, ref_low, ref_high)

                rows.append({
                    "name": _u(name),
                    "unit": _u(unit),
                    "value_now": _u(val_now) if val_now is not None else u"—",
                    "prev_sample_id": _u(prev_id),
                    "prev_date": _u(prev_date_txt),
                    "delta_pct": _u(delta_txt),
                    "delta_dir": _u(delta_dir),
                    "period_label": period_label,
                    # NUEVOS: Rangos de referencia
                    "ref_low": ref_low,
                    "ref_high": ref_high,
                    "ref_range_text": self._format_ref_range(ref_low, ref_high),
                    # NUEVOS: RCV
                    "rcv_value": rcv_value,
                    "rcv_flag": rcv_flag,
                    "rcv_text": u"{:.1f}% RCV".format(rcv_value) if rcv_value else u"",
                    # campos opcionales para futuros sparklines
                    "series": [],
                    "uid": None,
                })
            except Exception as e:
                if debug is not None:
                    debug.append(u"Fila '{}' falló: {}".format(_u(name), _u(e)))
                continue

        return rows, period_label

    def _format_ref_range(self, low, high):
        """Formatea el rango de referencia como texto"""
        if low is None and high is None:
            return u""
        
        parts = []
        if low is not None:
            parts.append(u"{:.2f}".format(low))
        if high is not None:
            if low is not None:
                parts.append(u" - ")
            parts.append(u"{:.2f}".format(high))
        
        return u"".join(parts)

    def __call__(self):
        debug_msgs = []
        try:
            # Activa diagnóstico con ?debug=1
            enable_debug = bool(self.request.get("debug"))
            rows, period_label = self.rows(debug=debug_msgs if enable_debug else None)
            data = {"rows": rows, "period_label": period_label}
            if enable_debug:
                data["debug"] = debug_msgs
            return data
        except Exception as e:
            try:
                logger.exception("DeltaCheck failed: %s", e)
            except Exception:
                pass
            return {"rows": [], "period_label": u"12 meses", "debug": debug_msgs + [u"ERROR: {}".format(_u(e))]}
