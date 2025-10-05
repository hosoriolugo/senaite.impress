# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName

try:
    from bika.lims import api, logger
except Exception:
    api = None
    import logging
    logger = logging.getLogger("senaite.impress")


def _u(v):
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
        s = _u(x).replace(",", ".")
        return float(s)
    except Exception:
        return None


class InfolabsaDeltaCheck(BrowserView):
    """Delta check robusto por paciente y analito (con múltiples llaves)."""

    PERIOD_DAYS = 365  # 12 meses

    # ------------ utils genéricos ------------
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
        return getToolByName(portal, "portal_catalog")

    # ------------ AR / Patient ------------
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

    def _patient_keys(self, ar, patient):
        """Conjunto de llaves para identificar paciente cuando no hay patient UID."""
        keys = {}
        # MRN y variantes comunes en AR/patient
        for obj in (ar, patient):
            if not obj:
                continue
            for name in (
                "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
                "getClientPatientID", "getPatientID", "getIdentifier",
            ):
                v = self._get(obj, name)
                if v:
                    keys.setdefault("mrn_like", set()).add(_u(v).strip())
        # nombre completo (para fallback “suave”)
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
            keys["fullname"] = full
        return keys

    def _date_of_ar(self, ar):
        for g in ("getDatePublished", "getDateVerified", "getDateReceived", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    def _analyses_of(self, ar):
        for g in ("getAnalyses", "analyses", "getAnalysis"):
            v = self._get(ar, g)
            if v:
                try:
                    return list(v)
                except Exception:
                    return v
        return []

    # ------------ Analito keys ------------
    def _service_of(self, a):
        try:
            return getattr(a, "getService", lambda: None)()
        except Exception:
            return None

    def _analysis_keys(self, a):
        """Llaves para identificar el analito de forma estable entre ARs."""
        svc = self._service_of(a)
        svc_uid = self._get(svc, "UID")
        kw = self._get(svc, "getKeyword") if svc else None
        if not kw:
            kw = self._get(a, "getKeyword")  # a veces el analysis lo trae
        title = self._title_of(svc) if svc else (self._get(a, "Title") or u"")
        # uid: preferir UID; si no hay, usar keyword; si no, Title normalizado
        uid = None
        if svc_uid:
            uid = _u(svc_uid)
        elif kw:
            uid = u"kw:" + _u(kw).strip().lower()
        elif title:
            uid = u"title:" + _u(title).strip().lower()
        return {
            "svc_uid": svc_uid,
            "keyword": _u(kw).strip() if kw else None,
            "title": _u(title).strip() if title else None,
            "uid": uid,
            "unit": self._unit_of(a),
            "name": _u(title or kw or self._get(a, "Title") or u""),
        }

    def _unit_of(self, a):
        for g in ("getUnit", "Unit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v:
                return _u(v)
        return u""

    def _result_value(self, a):
        for g in ("getFormattedResult", "getResult", "Result", "result", "getValue"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v, _to_num(v)
        return u"—", None

    # ------------ Búsqueda de previos ------------
    def _search_candidate_ars(self, current_ar, patient, pkeys):
        """Busca AR previos por:
           1) patient UID (si existe)
           2) MRN-like en índices comunes (si existen)
           3) fallback por fullname (menos estricto)
        """
        cat = self._cat()
        portal = self.context.portal_url.getPortalObject()
        cur_uid = self._get(current_ar, "UID")
        brains = []

        # 1) Por patient UID
        pid = self._get(patient, "UID") if patient else None
        if pid:
            brains += list(cat.searchResults(
                portal_type="AnalysisRequest",
                sort_on="created", sort_order="descending",
                getPatientUID=pid,
            ))

        # 2) Por MRN-like (agrega si el índice existe en tu catálogo)
        mrn_vals = pkeys.get("mrn_like", set())
        for q in mrn_vals:
            # probamos varios índices conocidos; si no existen, el catálogo ignora
            for idx in ("getMedicalRecordNumber", "getMedicalRecordNumberValue",
                        "getClientPatientID", "getPatientID"):
                brains += list(cat.searchResults(
                    portal_type="AnalysisRequest",
                    sort_on="created", sort_order="descending",
                    **{idx: q}
                ))

        # 3) Por Fullname (muy laxo; úsalo solo de backup)
        if pkeys.get("fullname"):
            brains += list(cat.searchResults(
                portal_type="AnalysisRequest",
                sort_on="created", sort_order="descending",
                Title=pkeys["fullname"],
            ))

        # unique y sin el actual
        seen = set()
        out = []
        for b in brains:
            if b.UID == cur_uid:
                continue
            if b.UID in seen:
                continue
            seen.add(b.UID)
            try:
                out.append(b.getObject())
            except Exception:
                pass
        return out

    def _series_for_uid(self, ars, analito_uid, keyword, title):
        """Construye serie (date,value) para analito identificado por uid/keyword/title."""
        pts = []
        for ar in ars:
            dt = self._date_of_ar(ar)
            for a in self._analyses_of(ar):
                keys = self._analysis_keys(a)
                # match por prioridad
                ok = False
                if analito_uid and keys["uid"] == analito_uid:
                    ok = True
                elif keyword and keys["keyword"] and keys["keyword"].lower() == keyword.lower():
                    ok = True
                elif title and keys["title"] and keys["title"].lower() == title.lower():
                    ok = True
                if not ok:
                    continue
                _, f = self._result_value(a)
                if f is None:
                    continue
                if api and dt:
                    try:
                        iso = api.to_iso_date(dt)
                    except Exception:
                        iso = _u(dt)
                else:
                    iso = _u(dt) if dt else u""
                pts.append({'date': iso, 'value': f, 'ar': ar})
                break
        pts.sort(key=lambda p: p['date'])
        return pts

    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        # candidatos: previos del mismo paciente (por UID/MRN/nombre)
        prev_ars = self._search_candidate_ars(ar, patient, pkeys)

        rows = []
        now_analyses = self._analyses_of(ar)
        for a in now_analyses:
            keys = self._analysis_keys(a)  # asegura uid/keyword/title
            raw, val_now = self._result_value(a)

            # serie histórica para este analito
            ars_for_series = list(prev_ars) + [ar]
            series = self._series_for_uid(ars_for_series, keys["uid"], keys["keyword"], keys["title"])

            # previo inmediato (antes del actual)
            prev = None
            for pt in reversed(series):
                if pt.get('ar') is not ar:
                    prev = pt
                    break

            delta_pct = u'N/A'
            delta_dir = u'∙'
            prev_id = u'—'
            prev_date = u'—'

            if prev and val_now is not None:
                pv = prev['value']
                if pv is not None and pv != 0:
                    delta = ((val_now - pv) / abs(pv)) * 100.0
                    delta_pct = u"%.1f%%" % (delta)
                    delta_dir = u'▲' if val_now > pv else (u'▼' if val_now < pv else u'Δ')
                if prev['ar']:
                    prev_id = self._get(prev['ar'], "getRequestID") or self._get(prev['ar'], "getId") or u'—'
                    dt = self._date_of_ar(prev['ar'])
                    if api and dt:
                        try:
                            prev_date = api.to_localized_time(dt)
                        except Exception:
                            prev_date = _u(dt) if dt else u'—'
                    else:
                        prev_date = _u(dt) if dt else u'—'

            rows.append({
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': (raw if raw not in (None, u"", "") else u'—'),
                'delta_pct': delta_pct,
                'delta_dir': delta_dir,
                'delta_note': u'',
                'prev_sample_id': prev_id,
                'prev_date': prev_date,
                'rcv_pct': None,
                'series': [{'date': p['date'], 'value': p['value']} for p in series if p.get('value') is not None],
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        return {'period_label': label, 'rows': rows}
