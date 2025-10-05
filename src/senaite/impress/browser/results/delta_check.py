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
        try:
            numtypes = (int, long, float)  # Py2
        except NameError:
            numtypes = (int, float)        # Py3
        if isinstance(x, numtypes):
            return float(x)
        s = _u(x).replace(",", ".")
        return float(s)
    except Exception:
        return None


def _norm(s):
    # normaliza para comparar nombres: minúsculas y colapsa espacios
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check por paciente (MRN>Nombre), con filtro por Perfil de análisis.
       Fallback por Client+SampleType(+Contact) solo si NO hay datos de paciente.
    """

    PERIOD_DAYS = 365  # 12 meses
    STATES_OK = set(("verified", "to_be_published", "published", "verified_duplicate"))

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

    def _wftool(self):
        try:
            portal = self.context.portal_url.getPortalObject()
            return getToolByName(portal, "portal_workflow")
        except Exception:
            return None

    # ------------ estado (review_state) ------------
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

    # ------------ fechas ISO-8601 (para sparkline JS) ------------
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

    # ------------ AR / Paciente / Cliente / SampleType / Perfil ------------
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

    def _patient_uid(self, patient):
        if patient and hasattr(patient, "UID"):
            try:
                return _u(patient.UID())
            except Exception:
                pass
        return None

    def _client_uid(self, ar):
        c = self._get(ar, "getClient")
        if c:
            try:
                c = c()
            except Exception:
                pass
        if c and hasattr(c, "UID"):
            try:
                return _u(c.UID())
            except Exception:
                pass
        return None

    def _contact_uid(self, ar):
        c = self._get(ar, "getContact")
        if c:
            try:
                c = c()
            except Exception:
                pass
        if c and hasattr(c, "UID"):
            try:
                return _u(c.UID())
            except Exception:
                pass
        return None

    def _sampletype_key(self, ar):
        st = self._get(ar, "getSampleType")
        if st:
            try:
                st = st()
            except Exception:
                pass
        if st and hasattr(st, "UID"):
            try:
                return u"uid:" + _u(st.UID())
            except Exception:
                pass
        title = None
        try:
            title = (st and hasattr(st, "Title") and st.Title()) or None
        except Exception:
            title = None
        if not title:
            title = self._get(ar, "getSampleTypeTitle")
            try:
                title = title() if callable(title) else title
            except Exception:
                pass
        return u"title:" + _norm(title or u"")

    def _profile_key(self, ar):
        """Obtén una llave estable del Perfil de análisis si existe."""
        prof = None
        # intentos típicos en SENAITE/Bika
        for g in ("getAnalysisProfile", "getProfile"):
            if hasattr(ar, g):
                try:
                    prof = getattr(ar, g)()
                    if prof:
                        break
                except Exception:
                    pass
        # a veces viene en lista
        if not prof:
            for g in ("getProfiles", "getAnalysisProfiles"):
                if hasattr(ar, g):
                    try:
                        lst = getattr(ar, g)() or []
                        if lst:
                            prof = lst[0]
                            break
                    except Exception:
                        pass
        if not prof:
            return None
        # pref UID
        if hasattr(prof, "UID"):
            try:
                return u"uid:" + _u(prof.UID())
            except Exception:
                pass
        # fallback título
        try:
            t = (hasattr(prof, "Title") and prof.Title()) or None
            if t:
                return u"title:" + _norm(t)
        except Exception:
            pass
        return None

    def _patient_keys(self, ar, patient):
        """Llaves para identificar paciente si existe Patient."""
        keys = {}
        p_uid = self._patient_uid(patient)
        if p_uid:
            keys["patient_uid"] = p_uid

        # MRN-like AR + Patient
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

        # Identifiers del Patient
        try:
            idents = []
            if patient and hasattr(patient, "getIdentifiers"):
                idents = patient.getIdentifiers() or []
            for i in idents:
                val = (i.get("value", u"") or u"").strip()
                if val:
                    keys.setdefault("mrn_like", set()).add(_u(val))
        except Exception:
            pass

        # Nombre completo
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

    def _context_keys(self, ar, patient):
        """Si NO hay patient usable, definimos el 'sujeto' por contexto."""
        keys = {
            "client_uid": self._client_uid(ar),
            "contact_uid": self._contact_uid(ar) or None,
            "sampletype_key": self._sampletype_key(ar),
        }
        pkeys = self._patient_keys(ar, patient)
        has_patient_info = bool(
            pkeys.get("patient_uid") or pkeys.get("mrn_like") or pkeys.get("fullname")
        )
        # Perfil del AR actual (para filtrar candidatos por el mismo perfil si existe)
        profile_key = self._profile_key(ar)
        return pkeys, keys, has_patient_info, profile_key

    def _date_of_ar(self, ar):
        # PRIORIDAD: Verificado -> Publicado -> Recepción -> creado
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
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

    # ------------ Emparejamiento de "sujeto" ------------
    def _same_patient(self, other_ar, pkeys):
        """Paciente primero: MRN exacto, luego nombre (normalizado). UID si ambos lo tienen."""
        # Patient UID si existe en ambos
        try:
            cur_patient = self._patient_obj(self.context)
            other_patient = self._patient_obj(other_ar)
            cur_uid = self._patient_uid(cur_patient)
            other_uid = self._patient_uid(other_patient)
            if cur_uid and other_uid and (cur_uid == other_uid):
                return True
        except Exception:
            pass

        # MRN-like exacto
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

        # Identifiers del patient del AR other
        try:
            op = self._patient_obj(other_ar)
            idents = []
            if op and hasattr(op, "getIdentifiers"):
                idents = op.getIdentifiers() or []
            for i in idents:
                val = (i.get("value", u"") or u"").strip()
                if val and pkeys.get("mrn_like") and (val in pkeys["mrn_like"]):
                    return True
        except Exception:
            pass

        # Nombre completo normalizado
        full = None
        for name in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, name)
            if v:
                full = _norm(v)
                break
        if full and pkeys.get("fullname") and full == pkeys["fullname"]:
            return True

        return False

    def _same_context(self, other_ar, ctx_keys):
        """Fallback cuando NO hay patient usable: Client + SampleType (+Contact)."""
        if not ctx_keys:
            return False
        cur_client = ctx_keys.get("client_uid")
        cur_contact = ctx_keys.get("contact_uid")
        cur_st = ctx_keys.get("sampletype_key")
        oth_client = self._client_uid(other_ar)
        oth_contact = self._contact_uid(other_ar)
        oth_st = self._sampletype_key(other_ar)
        if cur_client and oth_client and (cur_client != oth_client):
            return False
        if cur_st and oth_st and (cur_st != oth_st):
            return False
        if cur_contact and oth_contact and (cur_contact != oth_contact):
            return False
        return True

    # ------------ Búsqueda de previos ------------
    def _candidate_ars(self, current_ar, patient, pkeys, ctx_keys, has_patient_info, profile_key):
        """Trae un conjunto amplio y filtra por periodo, estado, 'sujeto' y perfil."""
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        brains = cat.searchResults(
            portal_type="AnalysisRequest",
            sort_on="created",
            sort_order="descending",
        )[:1000]

        out = []
        seen = set([cur_uid])

        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS
        except Exception:
            cutoff = None

        for b in brains:
            if b.UID in seen:
                continue

            if cutoff and getattr(b, "created", None):
                try:
                    if b.created < cutoff:
                        continue
                except Exception:
                    pass

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

            # Mismo Perfil (si el actual tiene perfil)
            if profile_key:
                other_profile = self._profile_key(obj)
                if other_profile and (other_profile != profile_key):
                    continue
                # si el otro no tiene perfil, lo aceptamos por ahora

            # Emparejamiento por paciente (cliente NO se usa cuando hay patient info)
            ok = False
            if has_patient_info:
                ok = self._same_patient(obj, pkeys)
            else:
                ok = self._same_context(obj, ctx_keys)

            if ok:
                out.append(obj)
                seen.add(b.UID)

        return out

    # ------------ Serie por analito ------------
    def _series_for_uid(self, ars, analito_uid, keyword, title):
        pts = []
        for ar in ars:
            st = self._state_of(ar)
            if st and st not in self.STATES_OK:
                continue
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

    # ------------ Vista ------------
    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys, ctx_keys, has_patient_info, profile_key = self._context_keys(ar, patient)

        prev_ars = self._candidate_ars(ar, patient, pkeys, ctx_keys, has_patient_info, profile_key)
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
            prev_date_fmt = u'—'

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
                    try:
                        prev_date_fmt = (dt and self.context.toLocalizedTime(dt)) or u'—'
                    except Exception:
                        prev_date_fmt = prev_date

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
                'prev_date_fmt': prev_date_fmt,
                'rcv_pct': None,
                'series': [{'date': p['date'], 'value': p['value']} for p in series if p.get('value') is not None],
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        return {'period_label': label, 'rows': rows}
