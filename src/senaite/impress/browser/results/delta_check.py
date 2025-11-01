# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName

try:
    from bika.lims import api, logger
except Exception:
    api = None
    import logging
    logger = logging.getLogger("senaite.impress")

import json
import re
import unicodedata
import datetime
import time


def _u(v):
    try:
        return unicode(v)
    except Exception:
        try:
            return u"%s" % v
        except Exception:
            return u""


def _strip_accents(s):
    try:
        s = _u(s)
        return u"".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )
    except Exception:
        return _u(s)


def _to_num(x):
    """Convierte x a float si es posible. Acepta '1,23', '<1', '> 3.5'."""
    try:
        if x in (None, u"", ""):
            return None
        try:
            num_types = (int, long, float)  # noqa
        except NameError:
            num_types = (int, float)
        if isinstance(x, num_types):
            return float(x)
        s = _u(x)
        s = s.replace("<", "").replace(">", "")
        s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _norm(s):
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check robusto por paciente y analito con fechas ISO-8601 + JSON crudo."""

    PERIOD_DAYS = 365
    MAX_POINTS  = 6  # Ajustado a 6 como solicitaste
    STATES_OK = set(("verified", "to_be_published", "published", "verified_duplicate"))

    # ------------------ utils base ------------------
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

    def _title_of(self, obj):
        for g in ("Title", "title_or_id"):
            if hasattr(obj, g):
                try:
                    t = getattr(obj, g)()
                    if t:
                        return _u(t)
                except Exception:
                    pass
        return _u(getattr(obj, "id", ""))

    def _cat(self):
        portal = self.context.portal_url.getPortalObject()
        cat = getToolByName(portal, "senaite_catalog_sample", None)
        if cat:
            return cat
        return getToolByName(portal, "portal_catalog")

    def _acat(self):
        portal = self.context.portal_url.getPortalObject()
        acat = getToolByName(portal, "senaite_catalog_analysis", None)
        if acat:
            return acat
        return getToolByName(portal, "portal_catalog")

    def _catalog_has_index(self, name, analyses=False):
        try:
            cat = self._acat() if analyses else self._cat()
            return name in cat.indexes()
        except Exception:
            return False

    def _list_indexes(self, analyses=False):
        try:
            cat = self._acat() if analyses else self._cat()
            return sorted(list(cat.indexes()))
        except Exception:
            return []

    def _wftool(self):
        try:
            portal = self.context.portal_url.getPortalObject()
            return getToolByName(portal, "portal_workflow")
        except Exception:
            return None

    # ------------------ estado ------------------
    def _state_of(self, obj, brain=None):
        try:
            if brain is not None:
                rs = getattr(brain, "review_state", None)
                if rs:
                    return _u(rs)
        except Exception:
            pass
        for g in ("getReviewState", "review_state", "state"):
            try:
                v = getattr(obj, g, None)
                v = v() if callable(v) else v
                if v:
                    return _u(v)
            except Exception:
                continue
        try:
            wftool = self._wftool()
            if wftool:
                v = wftool.getInfoFor(obj, "review_state", default=None)
                if v:
                    return _u(v)
        except Exception:
            pass
        return None

    # ------------------ fecha / formato ------------------
    def _iso(self, dt):
        if not dt:
            return u""
        if api:
            try:
                zdt = api.to_datetime(dt)
                return zdt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass
        try:
            return dt.ISO8601()  # puede traer Z; la quitamos abajo si hace falta
        except Exception:
            return _u(dt)

    def _fmt_local(self, dt):
        if not dt:
            return u"—"
        if api:
            try:
                return api.to_localized_time(dt)
            except Exception:
                pass
        try:
            return self.context.toLocalizedTime(dt)
        except Exception:
            pass
        try:
            return _u(dt)
        except Exception:
            return u"—"

    def _fmt_ddmmyy(self, iso):
        """Devuelve DDMMYY sin hora, a partir de ISO-8601."""
        try:
            s = _u(iso).replace("Z", "")
            fmt = "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d"
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%d%m%y")
        except Exception:
            return re.sub(r"\D+", "", _u(iso))[:6] or _u(iso)

    def _to_epoch_ms(self, iso):
        """Convierte ISO-8601 a epoch en milisegundos (para charts)."""
        try:
            # CORRECCIÓN: Limpiar correctamente el string ISO
            clean_iso = _u(iso).replace("Z", "").split('+')[0]  # Solo quitar timezone, no dividir por guiones
            
            if 'T' in clean_iso:
                # Formato con tiempo: "2025-10-04T04:04:47"
                fmt = "%Y-%m-%dT%H:%M:%S"
                dt = datetime.datetime.strptime(clean_iso, fmt)
            else:
                # Solo fecha: "2025-10-04"
                fmt = "%Y-%m-%d"
                dt = datetime.datetime.strptime(clean_iso, fmt)
            
            # Convertir a timestamp en milisegundos
            timestamp = time.mktime(dt.timetuple()) * 1000
            return int(timestamp)
            
        except Exception as e:
            logger.error("Error convirtiendo fecha %s a epoch: %s" % (iso, str(e)))
            return None

    def _date_of_ar(self, ar):
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    def _received_date_of_ar(self, ar):
        for g in ("getDateReceived", "getReceptionDate", "getSamplingDate", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    # ------------------ AR / Paciente ------------------
    def _patient_obj(self, ar):
        for pa in ("getPatient", "Patient", "getRelatedPatient"):
            if hasattr(ar, pa):
                try:
                    p = getattr(ar, pa)()
                    if p:
                        return p
                except Exception:
                    pass
        return None

    def _mrn_of_ar(self, ar, patient):
        for obj in (ar, patient):
            if not obj:
                continue
            for name in (
                "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
                "getClientPatientID", "getPatientID", "getIdentifier",
            ):
                v = self._get(obj, name)
                if v:
                    return _u(v).strip()
        return None

    def _patient_uid_of(self, patient):
        for name in ("UID", "getUID", "getId"):
            v = getattr(patient, name, None)
            try:
                v = v() if callable(v) else v
            except Exception:
                pass
            if v:
                return _u(v)
        return None

    def _patient_keys(self, ar, patient):
        keys = {}
        mrn = self._mrn_of_ar(ar, patient)
        if mrn:
            keys["mrn"] = mrn
        if patient:
            puid = self._patient_uid_of(patient)
            if puid:
                keys["patient_uid"] = puid
        full = None
        for obj in (ar, patient):
            if not obj:
                continue
            for name in ("getPatientFullName", "getFullname", "Title"):
                v = self._get(obj, name)
                if v:
                    full = _u(v).strip()
                    break
            if full:
                break
        if full:
            keys["fullname"] = _norm(full)
        return keys

    def _analyses_of(self, ar):
        for g in ("getAnalyses", "analyses", "getAnalysis"):
            v = self._get(ar, g)
            if v:
                try:
                    return list(v)
                except Exception:
                    return v
        return []

    # ------------------ Analito keys ------------------
    def _service_of(self, a):
        try:
            # Usar getAnalysisService si está disponible
            return getattr(a, "getAnalysisService", lambda: None)()
        except Exception:
            return None

    def _service_uid_of(self, a):
        for g in ("getServiceUID", "ServiceUID"):
            v = self._get(a, g)
            if v:
                return _u(v)
        svc = self._service_of(a)
        return self._get(svc, "UID")

    def _service_code(self, svc):
        for g in ("getAnalysisCode", "getCode", "getServiceID", "getId", "id"):
            v = self._get(svc, g)
            if v:
                return _u(v)
        return None

    def _unit_of(self, a):
        for g in ("getUnit", "Unit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v:
                return _u(v)
        return u""

    def _analysis_keys(self, a):
        svc = self._service_of(a)
        svc_uid = self._get(svc, "UID") or self._service_uid_of(a)
        kw = self._get(svc, "getKeyword") if svc else None
        if not kw:
            kw = self._get(a, "getKeyword")
        title = self._title_of(svc) if svc else (self._get(a, "Title") or u"")
        code = self._service_code(svc) if svc else None

        uid = None
        if svc_uid:
            uid = _u(svc_uid)
        elif kw:
            uid = u"kw:" + _u(kw).strip().lower()
        elif title:
            uid = u"title:" + _u(title).strip().lower()

        return {
            "svc_uid": _u(svc_uid) if svc_uid else None,
            "keyword": _u(kw).strip() if kw else None,
            "title": _u(title).strip() if title else None,
            "code": _u(code).strip() if code else None,
            "uid": uid,
            "unit": self._unit_of(a),
            "name": _u(title or kw or self._get(a, "Title") or u""),
        }

    # ===== Extraer numérico robusto =====
    def _result_value(self, a):
        # 1) Intento directo
        for g in ("getResult", "Result", "result", "getValue"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                num = _to_num(v)
                if num is not None:
                    fr = self._get(a, "getFormattedResult")
                    return (fr if fr not in (None, u"", "") else _u(v)), float(num)
        # 2) FormattedResult con número
        fr = self._get(a, "getFormattedResult")
        if fr not in (None, u"", ""):
            s = _u(fr)
            m = re.search(r'[-<>]?\s*\d+(?:[.,]\d+)?', s)
            if m:
                num = _to_num(m.group(0))
                if num is not None:
                    return (s, float(num))
            # 3) Cualitativos
            sn = _norm(_strip_accents(s))
            neg = (u"ausente", u"no detectado", u"no-detectado", u"nd",
                   u"negativo", u"sin crecimiento", u"no growth",
                   u"absent", u"none detected", u"not detected", u"no se detecta")
            pos = (u"presente", u"detectado", u"positivo", u"con crecimiento",
                   u"present", u"detected", u"growth")
            if any(k in sn for k in neg):
                return (s, 0.0)
            if any(k in sn for k in pos):
                return (s, 1.0)
        return u"—", None

    # ------------------ Búsqueda de AR previos ------------------
    def _same_patient(self, other_ar, pkeys):
        found = set()
        for name in (
            "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
            "getClientPatientID", "getPatientID", "getIdentifier",
        ):
            v = self._get(other_ar, name)
            if v:
                found.add(_u(v).strip())
        if found and pkeys.get("mrn"):
            if pkeys["mrn"] in found:
                return True

        full = None
        for name in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, name)
            if v:
                full = _norm(v)
                break
        if full and pkeys.get("fullname") and full == pkeys["fullname"]:
            return True
        return False

    def _best_patient_query_for_catalog(self, cat, pkeys):
        try:
            indexes = set(cat.indexes())
        except Exception:
            indexes = set()

        puid = pkeys.get("patient_uid")
        for idx in ("getPatientUIDExact", "getPatientUID"):
            if puid and idx in indexes:
                return {idx: puid}

        mrn = pkeys.get("mrn")
        for idx in ("getMedicalRecordNumber", "medical_record_number",
                    "getClientPatientID", "getPatientID", "getIdentifier"):
            if mrn and idx in indexes:
                return {idx: mrn}
        return {}

    def _candidate_ars(self, current_ar, patient, pkeys):
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS
        except Exception:
            cutoff = None

        query = {
            "portal_type": "AnalysisRequest",
            "sort_on": "created",
            "sort_order": "descending",
        }
        patient_filter = self._best_patient_query_for_catalog(cat, pkeys)
        query.update(patient_filter)

        try:
            idxs = set(cat.indexes())
        except Exception:
            idxs = set()
        if "getDateReceived" in idxs:
            if cutoff:
                query["getDateReceived"] = {"query": cutoff, "range": "min"}
        elif "created" in idxs:
            if cutoff:
                query["created"] = {"query": cutoff, "range": "min"}

        try:
            if patient_filter:
                brains = cat.searchResults(**query)
            else:
                brains = cat.searchResults(
                    portal_type="AnalysisRequest",
                    sort_on="created",
                    sort_order="descending",
                )[:500]
        except Exception:
            brains = []

        out = []
        seen = set([cur_uid])

        for b in brains:
            if getattr(b, "UID", None) in seen:
                continue
            try:
                b_state = getattr(b, "review_state", None)
                if b_state and _u(b_state) not in self.STATES_OK:
                    continue
            except Exception:
                pass
            try:
                obj = b.getObject()
            except Exception:
                continue
            if not getattr(b, "review_state", None):
                st = self._state_of(obj)
                if st and st not in self.STATES_OK:
                    continue
            if not patient_filter:
                if not self._same_patient(obj, pkeys):
                    continue
            out.append(obj)
            seen.add(b.UID)
        return out

    # ------------------ previo global ------------------
    def _choose_prev_ar_global(self, current_ar, candidate_ars):
        cur_recv = self._received_date_of_ar(current_ar)
        if not cur_recv:
            return None
        dated = []
        for ar in candidate_ars:
            dt = self._received_date_of_ar(ar)
            if not dt:
                continue
            try:
                if dt >= cur_recv:
                    continue
            except Exception:
                continue
            st = self._state_of(ar)
            if st and _u(st) not in self.STATES_OK:
                continue
            dated.append((dt, ar))
        if not dated:
            return None
        dated.sort(key=lambda x: x[0])
        return dated[-1][1]

    def _find_prev_value_in_ar(self, prev_ar, target_keys):
        if not prev_ar:
            return (u"—", None)

        tgt_svc_uid = (target_keys or {}).get("svc_uid")
        tgt_kw      = (target_keys or {}).get("keyword")
        tgt_code    = (target_keys or {}).get("code")
        tgt_title_n = _norm((target_keys or {}).get("title"))
        tgt_name_n  = _norm((target_keys or {}).get("name"))
        tgt_unit_n  = _norm((target_keys or {}).get("unit"))

        analyses = self._analyses_of(prev_ar)

        def _service_code(svc):
            for g in ("getAnalysisCode", "getCode", "getServiceID", "getId", "id"):
                v = self._get(svc, g)
                if v:
                    return _u(v)
            return None

        def _kw_of(a):
            svc = self._service_of(a)
            return (self._get(svc, "getKeyword") or self._get(a, "getKeyword"))

        def _match_and_return(a):
            raw, num = self._result_value(a)
            if (num is not None) or (raw not in (None, u"", "")):
                return (raw if raw not in (None, u"", "") else u"—", num)
            return None

        if tgt_svc_uid:
            for a in analyses:
                svc = self._service_of(a)
                svuid = self._get(svc, "UID") or self._service_uid_of(a)
                if svuid and _u(svuid) == tgt_svc_uid:
                    r = _match_and_return(a);  return r if r else (u"—", None)

        if tgt_kw:
            for a in analyses:
                kw_other = _kw_of(a)
                if kw_other and _u(kw_other).strip().lower() == _u(tgt_kw).strip().lower():
                    r = _match_and_return(a);  return r if r else (u"—", None)

        if tgt_code:
            for a in analyses:
                code_other = _service_code(self._service_of(a))
                if code_other and _norm(code_other) == _norm(tgt_code):
                    r = _match_and_return(a);  return r if r else (u"—", None)

        if tgt_title_n:
            for a in analyses:
                t_other = self._title_of(self._service_of(a)) if self._service_of(a) else (self._get(a, "Title") or u"")
                if _norm(t_other) == tgt_title_n:
                    r = _match_and_return(a);  return r if r else (u"—", None)

        for a in analyses:
            unit_o = self._unit_of(a)
            name_o = self._title_of(self._service_of(a)) if self._service_of(a) else (self._get(a, "Title") or u"")
            if _norm(name_o) == tgt_name_n and _norm(unit_o) == tgt_unit_n:
                r = _match_and_return(a);  return r if r else (u"—", None)

        return (u"—", None)

    # ------------------ Serie por analito ------------------
    def _series_for_uid(self, ars, analito_uid, keyword, title):
        acat = self._acat()

        # AR -> (obj, fecha)
        ar_by_id = {}
        ar_ids = []
        for ar in ars:
            rid = self._get(ar, "getRequestID") or self._get(ar, "getId")
            if not rid:
                continue
            ar_ids.append(rid)
            ar_by_id[rid] = (ar, self._date_of_ar(ar))
        if not ar_ids:
            return []

        query = {
            "portal_type": "Analysis",
            "getRequestID": ar_ids,
        }
        sort_on = "getResultCaptureDate" if self._catalog_has_index("getResultCaptureDate", analyses=True) else "created"

        try:
            abrains = acat.searchResults(sort_on=sort_on, sort_order="ascending", **query)
        except Exception:
            abrains = []

        pts = []
        ok_states = self.STATES_OK
        tgt_uid = analito_uid or u""
        tgt_kw  = (keyword or u"").strip().lower()
        tgt_title_n = _norm(title)

        for ab in abrains:
            try:
                astate = getattr(ab, "review_state", None)
                if astate and _u(astate) not in ok_states:
                    continue
            except Exception:
                pass

            try:
                aobj = ab.getObject()
            except Exception:
                continue

            svc = self._service_of(aobj)
            svuid = self._get(svc, "UID") or self._service_uid_of(aobj)
            kw_other = (self._get(svc, "getKeyword") or self._get(aobj, "getKeyword") or u"").strip().lower()
            title_other = self._title_of(svc) if svc else (self._get(aobj, "Title") or u"")
            title_other_n = _norm(title_other)

            match = False
            if tgt_uid and svuid and _u(svuid) == tgt_uid:
                match = True
            elif tgt_kw and kw_other and kw_other == tgt_kw:
                match = True
            elif tgt_title_n and title_other_n and title_other_n == tgt_title_n:
                match = True
            if not match:
                continue

            raw_val, fval = self._result_value(aobj)
            if fval is None:
                continue

            rid = getattr(ab, "getRequestID", None)
            rid = rid() if callable(rid) else rid
            ar_ref, ar_dt = ar_by_id.get(rid, (None, None))
            # Prioridad: usar fecha/hora del análisis; luego created; al final fecha del AR
            dt = getattr(ab, "getResultCaptureDate", None) or getattr(ab, "created", None) or ar_dt
            if not dt:
                continue

            iso = self._iso(dt).replace("Z", "")
            pts.append({
                "date": iso,
                "value": float(fval),
                "raw": _u(raw_val),
                "ar": ar_ref or self.context,
                "rid": rid
            })

        # Dedup por AR, último punto
        by_rid = {}
        for p in pts:
            rid = p.get("rid")
            if not rid:
                continue
            prev = by_rid.get(rid)
            if prev is None or p["date"] > prev["date"]:
                by_rid[rid] = p

        pts = list(by_rid.values())
        pts.sort(key=lambda p: p["date"])
        if len(pts) > self.MAX_POINTS:
            pts = pts[-self.MAX_POINTS:]
        return pts

    # ------------------ Vista (SIEMPRE JSON) ------------------
    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        prev_ars = self._candidate_ars(ar, patient, pkeys)
        ars_for_series = list(prev_ars) + [ar]

        rows = []
        multi_series = []
        now_analyses = self._analyses_of(ar)

        prev_ar_global = self._choose_prev_ar_global(ar, prev_ars)

        for a in now_analyses:
            keys = self._analysis_keys(a)
            raw_now, val_now = self._result_value(a)

            series_pts = self._series_for_uid(ars_for_series, keys["uid"], keys["keyword"], keys["title"])

            # puntos compatibles
            points = []
            for p in series_pts:
                iso = p.get('date')
                val = p.get('value')
                if iso is None or val is None:
                    continue
                ddmmyy = self._fmt_ddmmyy(iso)
                ms = self._to_epoch_ms(iso)
                points.append({
                    'date': iso,
                    'value': float(val),
                    'x': iso,
                    'y': float(val),
                    'ddmmyy': ddmmyy,
                    'ms': ms,
                    'sid': p.get('rid') or p.get('sid') or u''  # Propagar SID/RID
                })

            # serie para el gráfico (solo si hay ≥ 2 puntos)
            if len(points) >= 2:
                multi_series.append({
                    "name": keys["name"],
                    "unit": keys["unit"] or u"",
                    "series": points,  # objetos completos
                    "xy": [{'x': pt['x'], 'y': pt['y']} for pt in points],
                    "data": [[pt['ms'], pt['value']] for pt in points if pt.get('ms') is not None],  # <- NUMÉRICO
                    "categories_ddmmyy": [pt['ddmmyy'] for pt in points],
                })

            if len(points) < 2 or val_now is None:
                continue

            prev_raw, prev_num = self._find_prev_value_in_ar(prev_ar_global, keys)

            delta_abs = None
            delta_pct = u"N/A"
            delta_dir = u"∙"

            if prev_num is not None:
                try:
                    delta_abs = float(val_now) - float(prev_num)
                    if prev_num != 0:
                        pct = ((float(val_now) - float(prev_num)) / abs(float(prev_num))) * 100.0
                        delta_pct = u"%.1f%%" % pct
                    if float(val_now) > float(prev_num):
                        delta_dir = u"▲"
                    elif float(val_now) < float(prev_num):
                        delta_dir = u"▼"
                    else:
                        delta_dir = u"Δ"
                except Exception:
                    pass

            prev_id = u"—"
            prev_date = u"—"
            prev_date_fmt = u"—"
            if prev_ar_global:
                prev_id = (self._get(prev_ar_global, "getRequestID") or
                           self._get(prev_ar_global, "getId") or u"—")
                pdt = self._date_of_ar(prev_ar_global)
                prev_date = self._iso(pdt).replace("Z","") if pdt else u"—"
                prev_date_fmt = self._fmt_local(pdt) if pdt else u"—"

            delta_abs_fmt = u"—"
            try:
                if delta_abs is not None:
                    delta_abs_fmt = u"{:+.2f}".format(delta_abs)
            except Exception:
                pass
            delta_combo_fmt = (u"%s (%s)" % (delta_abs_fmt, delta_pct)
                               if delta_abs is not None
                               else u"—")

            # Dirección de tendencia simplificada (opcional)
            def _trend_dir(series_points):
                try:
                    n = len(series_points or [])
                    if n < 2:
                        return u"∙"
                    import datetime as _dt
                    def _to_ts_days(iso_):
                        try:
                            dt_ = _dt.datetime.strptime(iso_.replace("Z",""), "%Y-%m-%dT%H:%M:%S")
                        except Exception:
                            try:
                                dt_ = _dt.datetime.strptime(iso_[:10], "%Y-%m-%d")
                            except Exception:
                                return None
                        return (dt_ - _dt.datetime(1970,1,1)).total_seconds() / 86400.0
                    pts2 = [( _to_ts_days(p["date"]), float(p["value"]) ) for p in series_points if p.get("date") and p.get("value") is not None]
                    pts2 = [p for p in pts2 if p[0] is not None]
                    if len(pts2) < 2:
                        return u"∙"
                    if len(pts2) == 2:
                        return u"▲" if pts2[-1][1] > pts2[0][1] else (u"▼" if pts2[-1][1] < pts2[0][1] else u"∙")
                    xs = [p[0] for p in pts2]
                    ys = [p[1] for p in pts2]
                    xbar = sum(xs) / float(len(xs))
                    ybar = sum(ys) / float(len(ys))
                    sxy = sum((x - xbar)*(y - ybar) for x,y in pts2)
                    sxx = sum((x - xbar)*(x - xbar) for x in xs) or 1.0
                    slope = sxy / sxx
                    t_span = max(xs) - min(xs) or 1.0
                    y_span = max(ys) - min(ys) or 1.0
                    rel = abs(slope) * t_span / y_span
                    if rel < 0.005:
                        return u"∙"
                    return u"▲" if slope > 0 else u"▼"
                except Exception:
                    return u"∙"

            trend_dir = _trend_dir(points)

            prev_value_raw = prev_raw if prev_raw not in (None, u"", "") else (u"%s" % prev_num if prev_num is not None else u"—")

            trend_from_fmt = self._fmt_ddmmyy(points[0]['date']) if points else u"—"
            trend_to_fmt   = self._fmt_ddmmyy(points[-1]['date']) if points else u"—"

            rows.append({
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': (raw_now if raw_now not in (None, u"", "") else u'—'),

                'delta_pct': delta_pct,
                'delta_dir': trend_dir,
                'delta_abs_fmt': delta_abs_fmt,
                'delta_combo_fmt': delta_combo_fmt,
                'delta_note': u'',

                'prev_sample_id': prev_id,
                'prev_date': prev_date,
                'prev_date_fmt': prev_date_fmt,
                'prev_value': prev_value_raw,

                'rcv_pct': None,

                'series': [{'date': pt['date'], 'value': pt['value']} for pt in points],

                'trend_from_fmt': trend_from_fmt,
                'trend_to_fmt': trend_to_fmt,
            })

        # chart_v2 series numéricas
        chart_v2_series = []
        for s in multi_series:
            chart_v2_series.append({
                "name": s.get("name"),
                "unit": s.get("unit") or u"",
                "data": s.get("data") or [],  # [[ms, val], ...] NUMÉRICO
                "categories_ddmmyy": s.get("categories_ddmmyy") or [],
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        has_chart = bool([s for s in multi_series if len(s.get("series") or []) >= 2])

        # --- RETORNAR DICCIONARIO, NO JSON ---
        payload = {
            'period_label': label,
            'rows': rows,
            'chart': {
                'series': multi_series,           # objetos completos
                'max_points': self.MAX_POINTS,
                'window_days': self.PERIOD_DAYS,
            },
            'chart_v2': {
                'x_mode': 'ms',
                'series': chart_v2_series,        # datos numéricos
                'max_points': self.MAX_POINTS,
                'window_days': self.PERIOD_DAYS,
            },
            'has_chart': bool(has_chart),
        }

        # Si piden debug, adjuntamos metadatos dentro del mismo JSON
        if self.request.form.get("debug") or self.request.get("debug"):
            cat = self._cat()
            pf = self._best_patient_query_for_catalog(cat, pkeys)
            payload["_debug"] = {
                "patient_keys": pkeys,
                "sample_catalog_indexes": self._list_indexes(analyses=False),
                "analysis_catalog_indexes": self._list_indexes(analyses=True),
                "patient_filter_used_in_catalog_query": pf,
                "period_days": self.PERIOD_DAYS,
                "max_points_per_analyte": self.MAX_POINTS,
                "candidate_ARs_found": len(prev_ars),
                "current_AR_id": self._get(ar, "getRequestID") or self._get(ar, "getId"),
            }

        # RETORNAR DICCIONARIO DIRECTAMENTE - NO CONVERTIR A JSON
        return payload
