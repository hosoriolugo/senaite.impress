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
        # python2 types
        try:
            num_types = (int, long, float)
        except Exception:
            num_types = (int, float)
        if isinstance(x, num_types):
            return float(x)
        s = _u(x).replace(",", ".")
        return float(s)
    except Exception:
        return None


def _norm(s):
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check por PACIENTE (PatientUID/MRN) + PERFIL de análisis (mismo panel)."""

    PERIOD_DAYS = 365  # 12 meses hacia atrás
    STATES_OK = set(("verified", "to_be_published", "published", "verified_duplicate"))

    # ----------------- utilidades básicas -----------------
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

    def _cat(self):
        portal = self.context.portal_url.getPortalObject()
        return getToolByName(portal, "portal_catalog")

    def _wftool(self):
        try:
            portal = self.context.portal_url.getPortalObject()
            return getToolByName(portal, "portal_workflow")
        except Exception:
            return None

    def _catalog_has_index(self, name):
        try:
            portal = self.context.portal_url.getPortalObject()
            cat = getToolByName(portal, "portal_catalog")
            return name in cat.indexes()
        except Exception:
            return False

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

    # ----------------- fechas ISO -----------------
    def _iso(self, dt):
        if not dt:
            return u""
        if api:
            try:
                zdt = api.to_datetime(dt)
                return zdt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
        try:
            return dt.ISO8601()
        except Exception:
            return _u(dt)

    def _date_of_ar(self, ar):
        # Usamos “verificado” como principal (como acordamos), si no, publicado -> recibido -> creado
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    # ----------------- contexto paciente -----------------
    def _patient_of(self, ar):
        for pa in ("getPatient", "Patient", "getRelatedPatient"):
            if hasattr(ar, pa):
                try:
                    p = getattr(ar, pa)()
                    if p:
                        return p
                except Exception:
                    pass
        return None

    def _patient_uid(self, patient):
        try:
            return patient and patient.UID() or None
        except Exception:
            return None

    def _mrn_set(self, ar, patient):
        vals = set()
        for obj in (ar, patient):
            if not obj:
                continue
            for name in (
                "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
                "getClientPatientID", "getPatientID", "getIdentifier",
            ):
                v = self._get(obj, name)
                if v:
                    vals.add(_u(v).strip())
        return vals

    # ----------------- perfil de análisis (misma batería de analitos) -----------------
    def _profile_key(self, ar):
        """Devuelve un 'perfil' identificable:
        - Primero: UID de AnalysisProfile si existe (getProfile / getAnalysisProfile / getProfiles)
        - Si no: huella del conjunto de keywords de los análisis (estable y ordenada).
        """
        # 1) UID de perfil si está disponible
        for g in ("getAnalysisProfile", "getProfile", "getProfiles"):
            try:
                val = getattr(ar, g, None)
                val = val() if callable(val) else val
                if not val:
                    continue
                # puede ser objeto, lista de objetos o cadena
                if isinstance(val, (list, tuple)):
                    for p in val:
                        if hasattr(p, "UID"):
                            return u"prof:" + _u(p.UID())
                        # algunos setups guardan el id/uid en string
                        if isinstance(p, basestring):
                            return u"prof:" + _u(p)
                else:
                    if hasattr(val, "UID"):
                        return u"prof:" + _u(val.UID())
                    if isinstance(val, basestring):
                        return u"prof:" + _u(val)
            except Exception:
                continue

        # 2) Huella por conjunto de keywords de los análisis
        kws = set()
        for a in self._analyses_of(ar):
            kw = None
            svc = self._service_of(a)
            kw = (self._get(svc, "getKeyword") if svc else None) or self._get(a, "getKeyword")
            if kw:
                kws.add(_norm(kw))
        if kws:
            return u"kws:" + u",".join(sorted(kws))
        return None

    # ----------------- análisis y servicios -----------------
    def _analyses_of(self, ar):
        for g in ("getAnalyses", "analyses", "getAnalysis"):
            v = self._get(ar, g)
            if v:
                try:
                    return list(v)
                except Exception:
                    return v
        return []

    def _service_of(self, a):
        try:
            return getattr(a, "getService", lambda: None)()
        except Exception:
            return None

    def _unit_of(self, a):
        for g in ("getUnit", "Unit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v:
                return _u(v)
        return u""

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

    def _result_value(self, a):
        for g in ("getFormattedResult", "getResult", "Result", "result", "getValue"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                return v, _to_num(v)
        return u"—", None

    # ----------------- búsqueda de AR previos: por patient + perfil -----------------
    def _candidate_ars(self, current_ar, patient_uid, mrn_values, profile_key):
        """Busca AR del MISMO PACIENTE (PatientUID o MRN) y MISMO PERFIL, estados >= verified."""
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        brains = []

        # 1) Preferir PatientUID si existe y está indexado
        if patient_uid and self._catalog_has_index("getPatientUID"):
            try:
                brains = cat.searchResults(
                    portal_type="AnalysisRequest",
                    getPatientUID=_u(patient_uid),
                    sort_on="created",
                    sort_order="descending",
                )
            except Exception:
                brains = []

        # 2) Si no hay UID o no devolvió nada, buscar por MRN en índices comunes
        if (not brains) and mrn_values:
            mrn_indexes = [idx for idx in (
                "getClientPatientID", "getMedicalRecordNumber", "getMedicalRecordNumberValue",
                "getMRN", "getPatientID", "getIdentifier"
            ) if self._catalog_has_index(idx)]
            for idx in mrn_indexes:
                try:
                    subset = []
                    for v in mrn_values:
                        q = {
                            "portal_type": "AnalysisRequest",
                            idx: _u(v),
                            "sort_on": "created",
                            "sort_order": "descending",
                        }
                        subset.extend(cat.searchResults(**q))
                    if subset:
                        brains = subset
                        break
                except Exception:
                    continue

        # 3) Último recurso: barrido amplio (evita perder trazabilidad si no hay índices)
        if not brains:
            brains = cat.searchResults(
                portal_type="AnalysisRequest",
                sort_on="created",
                sort_order="descending",
            )[:1000]

        out = []
        seen = set([cur_uid])

        # Límite por periodo
        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS
        except Exception:
            cutoff = None

        # Perfil del actual (para filtrar)
        cur_profile = profile_key

        for b in brains:
            buid = getattr(b, "UID", None)
            if buid in seen:
                continue

            # Periodo
            if cutoff and getattr(b, "created", None):
                try:
                    if b.created < cutoff:
                        continue
                except Exception:
                    pass

            # Estado
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

            # Estado por objeto si el brain no traía
            if not getattr(b, "review_state", None):
                st = self._state_of(obj)
                if st and st not in self.STATES_OK:
                    continue

            # si no pudimos filtrar por índice de paciente (uid/mrn), asegúrate por string:
            if not (patient_uid or mrn_values):
                # fallback por nombre/MRN en objeto (menos confiable)
                if not self._same_patient(obj, current_ar):
                    continue

            # Filtra por PERFIL: requiere que el otro AR comparta el mismo profile_key
            if cur_profile:
                other_profile = self._profile_key(obj)
                if other_profile and other_profile != cur_profile:
                    continue

            out.append(obj)
            seen.add(buid)

        # Orden descendente por fecha (previo inmediato = primero distinto del actual)
        out.sort(key=lambda x: self._date_of_ar(x) or "", reverse=True)
        return out

    def _same_patient(self, other_ar, current_ar):
        """Fallback blando si no hay índice: compara MRN/nombre."""
        patient = self._patient_of(current_ar)
        mrns_cur = self._mrn_set(current_ar, patient)
        mrns_other = self._mrn_set(other_ar, self._patient_of(other_ar))
        if mrns_cur and mrns_other and (mrns_cur & mrns_other):
            return True
        # Nombre como última opción
        full_cur = None
        for k in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(current_ar, k)
            if v:
                full_cur = _norm(v)
                break
        full_oth = None
        for k in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, k)
            if v:
                full_oth = _norm(v)
                break
        return bool(full_cur and full_oth and full_cur == full_oth)

    # ----------------- series por analito (de AR previos del mismo perfil) -----------------
    def _series_for_uid(self, ars, analito_uid, keyword, title):
        pts = []
        for ar in ars:
            st = self._state_of(ar)
            if st and st not in self.STATES_OK:
                continue
            dt = self._date_of_ar(ar)
            for a in self._analyses_of(ar):
                keys = self._analysis_keys(a)
                if analito_uid and keys["uid"] == analito_uid:
                    ok = True
                elif keyword and keys["keyword"] and keys["keyword"].lower() == (keyword or u"").lower():
                    ok = True
                elif title and keys["title"] and keys["title"].lower() == (title or u"").lower():
                    ok = True
                else:
                    ok = False
                if not ok:
                    continue
                _, f = self._result_value(a)
                if f is None:
                    continue
                pts.append({'date': self._iso(dt), 'value': f, 'ar': ar})
                break
        pts.sort(key=lambda p: p['date'])
        return pts

    # ----------------- principal -----------------
    def __call__(self):
        ar = self.context

        # Paciente actual
        patient = self._patient_of(ar)
        patient_uid = self._patient_uid(patient)
        mrn_values = self._mrn_set(ar, patient)

        # Perfil del AR actual
        profile_key = self._profile_key(ar)

        # Candidatos: MISMO PACIENTE + MISMO PERFIL (independiente del cliente)
        prev_ars = self._candidate_ars(ar, patient_uid, mrn_values, profile_key)
        ars_for_series = list(prev_ars) + [ar]

        rows = []
        for a in self._analyses_of(ar):
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
            prev_date_fmt = u'—'

            if prev and val_now is not None:
                pv = prev['value']
                if pv is not None and pv != 0:
                    delta = ((val_now - pv) / abs(pv)) * 100.0
                    try:
                        delta_pct = u"%.1f%%" % (delta)
                    except Exception:
                        delta_pct = u"%s%%" % (delta)
                    delta_dir = u'▲' if val_now > pv else (u'▼' if val_now < pv else u'Δ')
                if prev['ar']:
                    prev_id = (self._get(prev['ar'], "getRequestID") or
                               self._get(prev['ar'], "getId") or u'—')
                    dt = self._date_of_ar(prev['ar'])
                    prev_date = self._iso(dt) if dt else u'—'
                    try:
                        prev_date_fmt = (dt and api and api.to_localized_time(dt)) or u'—'
                    except Exception:
                        prev_date_fmt = u'—'

            rows.append({
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': (raw if raw not in (None, u"", "") else u'—'),
                'delta_pct': delta_pct,
                'delta_dir': delta_dir,
                'delta_note': u'',
                'prev_sample_id': prev_id,
                'prev_date': prev_date,         # ISO (para depuración / datos crudos)
                'prev_date_fmt': prev_date_fmt, # Localizado (para mostrar en el PT)
                'rcv_pct': None,                # espacio para futuro (RCV)
                'series': [{'date': p['date'], 'value': p['value']} for p in series if p.get('value') is not None],
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        return {'period_label': label, 'rows': rows}
