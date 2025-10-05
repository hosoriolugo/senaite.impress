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
        # Python 2 ints/longs/float (mantener compat)
        try:
            num_types = (int, long, float)  # noqa
        except NameError:
            num_types = (int, float)
        if isinstance(x, num_types):
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

    PERIOD_DAYS = 365   # 12 meses (ventana)
    MAX_POINTS  = 8     # tope de puntos por analito (más recientes)
    # Estados considerados "ok" para el delta (>= verified)
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
        """Catálogo de AnalysisRequest (samples)."""
        portal = self.context.portal_url.getPortalObject()
        # Senaite 2.x: cat de AR suele ser senaite_catalog_sample
        cat = getToolByName(portal, "senaite_catalog_sample", None)
        if cat:
            return cat
        return getToolByName(portal, "portal_catalog")

    def _acat(self):
        """Catálogo de Analysis."""
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

    def _wftool(self):
        try:
            portal = self.context.portal_url.getPortalObject()
            return getToolByName(portal, "portal_workflow")
        except Exception:
            return None

    # ------------------ estado (review_state) ------------------
    def _state_of(self, obj, brain=None):
        # 1) si viene del brain, úsalo
        try:
            if brain is not None:
                rs = getattr(brain, "review_state", None)
                if rs:
                    return _u(rs)
        except Exception:
            pass
        # 2) atributo simple o getter en el objeto
        for g in ("getReviewState", "review_state", "state"):
            try:
                v = getattr(obj, g, None)
                v = v() if callable(v) else v
                if v:
                    return _u(v)
            except Exception:
                continue
        # 3) workflow tool
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
        # api helper si existe
        if api:
            try:
                zdt = api.to_datetime(dt)
                # ISO con Z para JS Date.parse
                return zdt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
        # Zope DateTime tiene ISO8601()
        try:
            return dt.ISO8601()
        except Exception:
            return _u(dt)

    def _fmt_local(self, dt):
        if not dt:
            return u"—"
        # bika api
        if api:
            try:
                return api.to_localized_time(dt)
            except Exception:
                pass
        # Plone helper
        try:
            return self.context.toLocalizedTime(dt)
        except Exception:
            pass
        try:
            # último recurso
            return _u(dt)
        except Exception:
            return u"—"

    def _date_of_ar(self, ar):
        # PRIORIDAD: Verificado -> Publicado -> Recepción -> creado
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
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
        # Mismos nombres que el template INFOLABSA.pt
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

    def _patient_keys(self, ar, patient):
        """Llaves para identificar paciente (MRN preferente + nombre full)."""
        keys = {}
        mrn = self._mrn_of_ar(ar, patient)
        if mrn:
            keys["mrn"] = mrn

        # Nombre completo como fallback
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

    # ------------------ Búsqueda de AR previos ------------------
    def _same_patient(self, other_ar, pkeys):
        """Fallback programático si falta el índice MRN."""
        # MRN-like en el AR other
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
        """
        Saca los AR del mismo paciente dentro del período:
        - Si existe índice 'medical_record_number', se usa directamente.
        - Si no, se hace fallback programático (últimos N) + filtro por paciente.
        - Se quedan sólo AR en estados >= verified.
        """
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        # Límite por periodo
        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS  # días atrás
        except Exception:
            cutoff = None

        brains = []
        mrn = pkeys.get("mrn")
        has_mrn_index = self._catalog_has_index("medical_record_number", analyses=False)

        try:
            if mrn and has_mrn_index:
                query = {
                    "portal_type": "AnalysisRequest",
                    "medical_record_number": mrn,
                    "sort_on": "created",
                    "sort_order": "descending",
                }
                if cutoff and self._catalog_has_index("created", analyses=False):
                    query["created"] = {"query": cutoff, "range": "min"}
                brains = cat.searchResults(**query)
            else:
                # Fallback: trae un conjunto amplio y filtra a mano
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

            # Estado según brain
            try:
                b_state = getattr(b, "review_state", None)
                if b_state and _u(b_state) not in self.STATES_OK:
                    continue
            except Exception:
                pass

            # Periodo en fallback (si no pudimos filtrar arriba)
            if not (mrn and has_mrn_index) and cutoff and getattr(b, "created", None):
                try:
                    if b.created < cutoff:
                        continue
                except Exception:
                    pass

            try:
                obj = b.getObject()
            except Exception:
                continue

            # Estado por objeto si no venía en brain
            if not getattr(b, "review_state", None):
                st = self._state_of(obj)
                if st and st not in self.STATES_OK:
                    continue

            # Si no tenemos índice MRN, filtra por paciente programáticamente
            if not (mrn and has_mrn_index):
                if not self._same_patient(obj, pkeys):
                    continue

            out.append(obj)
            seen.add(b.UID)

        return out

    # ------------------ Serie por analito vía catálogo de analyses ------------------
    def _series_for_uid(self, ars, analito_uid, keyword, title):
        """
        Construye la serie (date,value,raw,ar) del analito:
        - Filtra analyses por getRequestID ∈ IDs de AR candidatos
        - y por getKeyword (o getServiceUID si quisieras).
        - Usa fecha del AR (verificada->publicada->recepción->creado).
        """
        acat = self._acat()

        # Mapa: RequestID -> (AR, fecha del AR)
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

        # Si hay índice de keyword, úsalo (más directo y robusto)
        if keyword and self._catalog_has_index("getKeyword", analyses=True):
            query["getKeyword"] = keyword
        # Alternativa opcional: por UID de servicio (descomentar si conviene)
        # elif analito_uid and self._catalog_has_index("getServiceUID", analyses=True) and not analito_uid.startswith("kw:"):
        #     query["getServiceUID"] = analito_uid

        sort_on = "getResultCaptureDate" if self._catalog_has_index("getResultCaptureDate", analyses=True) else "created"

        try:
            abrains = acat.searchResults(sort_on=sort_on, sort_order="ascending", **query)
        except Exception:
            abrains = []

        pts = []
        ok_states = self.STATES_OK

        for ab in abrains:
            # Estado por brain
            try:
                astate = getattr(ab, "review_state", None)
                if astate and _u(astate) not in ok_states:
                    continue
            except Exception:
                pass

            # Objeto y valor numérico
            try:
                aobj = ab.getObject()
            except Exception:
                continue

            raw_val, fval = self._result_value(aobj)
            if fval is None:
                continue

            rid = getattr(ab, "getRequestID", None)
            rid = rid() if callable(rid) else rid
            ar_ref, ar_dt = ar_by_id.get(rid, (None, None))

            # Fecha del punto: prioriza fecha del AR
            dt = ar_dt
            if not dt:
                # fallback: usa índice del analysis si no hay fecha AR
                dt = getattr(ab, "getResultCaptureDate", None) or getattr(ab, "created", None)

            if not dt:
                continue

            iso = self._iso(dt)
            pts.append({"date": iso, "value": fval, "raw": raw_val, "ar": ar_ref or self.context})

        # Asegura orden temporal
        pts.sort(key=lambda p: p["date"])

        # Último intento: si la query por catálogo no devolvió el punto actual (p.ej. falta de índice),
        # añade el del AR actual por inspección directa
        if not pts:
            cur_ar = self.context
            dt = self._date_of_ar(cur_ar)
            for a in self._analyses_of(cur_ar):
                keys = self._analysis_keys(a)
                ok = False
                if analito_uid and keys["uid"] == analito_uid:
                    ok = True
                elif keyword and keys["keyword"] and keys["keyword"].lower() == (keyword or "").lower():
                    ok = True
                elif title and keys["title"] and keys["title"].lower() == (title or "").lower():
                    ok = True
                if not ok:
                    continue
                raw, f = self._result_value(a)
                if f is None:
                    continue
                iso = self._iso(dt)
                pts.append({"date": iso, "value": f, "raw": raw, "ar": cur_ar})
                break

            pts.sort(key=lambda p: p["date"])

        # Recorte a los últimos MAX_POINTS
        if len(pts) > self.MAX_POINTS:
            pts = pts[-self.MAX_POINTS:]

        return pts

    # ------------------ Vista ------------------
    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        # AR previos del mismo paciente (por MRN si hay índice; si no, fallback)
        prev_ars = self._candidate_ars(ar, patient, pkeys)

        # Serie se arma con [previos + actual]
        ars_for_series = list(prev_ars) + [ar]

        rows = []
        now_analyses = self._analyses_of(ar)
        for a in now_analyses:
            keys = self._analysis_keys(a)
            raw_now, val_now = self._result_value(a)

            series = self._series_for_uid(ars_for_series, keys["uid"], keys["keyword"], keys["title"])

            # Debe haber al menos 2 puntos (actual + previo)
            if len(series) < 2 or val_now is None:
                continue

            # previo inmediato (último punto que NO sea del AR actual)
            prev = None
            for pt in reversed(series):
                if pt.get('ar') is not ar:
                    prev = pt
                    break

            if not prev:
                # sin previo válido no mostramos fila
                continue

            delta_pct = u'N/A'
            delta_dir = u'∙'
            prev_id = u'—'
            prev_date = u'—'
            prev_date_fmt = u'—'
            prev_value_raw = prev.get('raw') if prev.get('raw') not in (None, u"", "") else (
                u"%s" % prev.get('value') if prev.get('value') is not None else u"—"
            )

            pv = prev.get('value')
            if pv is not None and pv != 0:
                delta = ((val_now - pv) / abs(pv)) * 100.0
                delta_pct = u"%.1f%%" % (delta)
                delta_dir = u'▲' if val_now > pv else (u'▼' if val_now < pv else u'Δ')

            if prev.get('ar'):
                prev_id = (self._get(prev['ar'], "getRequestID") or
                           self._get(prev['ar'], "getId") or u'—')
                dt = self._date_of_ar(prev['ar'])
                prev_date = self._iso(dt) if dt else u'—'
                prev_date_fmt = self._fmt_local(dt) if dt else u'—'

            rows.append({
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': (raw_now if raw_now not in (None, u"", "") else u'—'),
                'delta_pct': delta_pct,
                'delta_dir': delta_dir,
                'delta_note': u'',
                'prev_sample_id': prev_id,
                'prev_date': prev_date,            # ISO (para debug/JSON)
                'prev_date_fmt': prev_date_fmt,    # Localizado (para el PDF)
                'prev_value': prev_value_raw,      # Valor previo (raw) para mostrar en la plantilla
                'rcv_pct': None,
                'series': [{'date': p['date'], 'value': p['value']} for p in series if p.get('value') is not None],
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        return {'period_label': label, 'rows': rows}
