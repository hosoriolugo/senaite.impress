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


def _norm(s):
    # normaliza para comparar nombres: minúsculas y colapsa espacios
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check robusto por paciente y analito con fechas ISO-8601."""

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

    # ------------ fechas ISO-8601 (para sparkline JS) ------------
    def _iso(self, dt):
        if not dt:
            return u""
        # api helper si existe
        if api:
            try:
                # api.to_iso_date suele devolver YYYY-MM-DD HH:MM, pero mejor ISO completo
                zdt = api.to_datetime(dt)
                return zdt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
        # Zope DateTime tiene ISO8601()
        try:
            return dt.ISO8601()
        except Exception:
            return _u(dt)

    # ------------ AR / Paciente ------------
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
        """Llaves para identificar paciente incluso sin Patient UID."""
        keys = {}
        # MRN-like de AR y Patient (varios nombres)
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

        # Nombre completo (fallback)
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
        svc = self._service_of(a)
        svc_uid = self._get(svc, "UID")
        kw = self._get(svc, "getKeyword") if svc else None
        if not kw:
            kw = self._get(a, "getKeyword")
        title = self._title_of(svc) if svc else (self._get(a, "Title") or u"")

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

    # ------------ Búsqueda de previos (robusta) ------------
    def _same_patient(self, other_ar, pkeys):
        """Evalúa si other_ar pertenece al mismo paciente, sin depender de índices."""
        # MRN-like en el AR other
        found = set()
        for name in (
            "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
            "getClientPatientID", "getPatientID", "getIdentifier",
        ):
            v = self._get(other_ar, name)
            if v:
                found.add(_u(v).strip())
        if found and pkeys.get("mrn_like"):
            if any(x in pkeys["mrn_like"] for x in found):
                return True

        # Nombre completo como último recurso
        full = None
        for name in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, name)
            if v:
                full = _norm(v)
                break
        if full and pkeys.get("fullname") and full == pkeys["fullname"]:
            return True

        return False

    def _candidate_ars(self, current_ar, patient, pkeys):
        """Amplía el radio de búsqueda: toma últimos N AR y filtra programáticamente."""
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")
        # Trae un conjunto amplio (p.ej. últimos 500 AR) y filtra por periodo y paciente
        brains = cat.searchResults(
            portal_type="AnalysisRequest",
            sort_on="created",
            sort_order="descending",
            # Si tu catálogo tiene 'created' como DateIndex puedes filtrar por rango;
            # si no, el corte por periodo lo hacemos después.
        )[:500]

        out = []
        seen = set([cur_uid])
        # Límite por periodo
        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS  # días atrás
        except Exception:
            cutoff = None

        for b in brains:
            if b.UID in seen:
                continue
            # Periodo (si podemos)
            if cutoff and getattr(b, "created", None):
                try:
                    if b.created < cutoff:
                        continue
                except Exception:
                    pass
            try:
                obj = b.getObject()
            except Exception:
                continue
            if self._same_patient(obj, pkeys):
                out.append(obj)
                seen.add(b.UID)
        return out

    def _series_for_uid(self, ars, analito_uid, keyword, title):
        """Construye serie (date,value) para el analito identificado por uid/keyword/title."""
        pts = []
        for ar in ars:
            dt = self._date_of_ar(ar)
            for a in self._analyses_of(ar):
                keys = self._analysis_keys(a)
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
                iso = self._iso(dt)
                pts.append({'date': iso, 'value': f, 'ar': ar})
                break
        pts.sort(key=lambda p: p['date'])
        return pts

    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        prev_ars = self._candidate_ars(ar, patient, pkeys)
        ars_for_series = list(prev_ars) + [ar]

        rows = []
        now_analyses = self._analyses_of(ar)
        for a in now_analyses:
            keys = self._analysis_keys(a)
            raw, val_now = self._result_value(a)

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
                    prev_id = (self._get(prev['ar'], "getRequestID") or
                               self._get(prev['ar'], "getId") or u'—')
                    dt = self._date_of_ar(prev['ar'])
                    prev_date = self._iso(dt) if dt else u'—'

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
