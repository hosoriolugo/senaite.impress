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
    """Convierte v a Zope DateTime de forma segura."""
    try:
        if isinstance(v, DateTime):
            return v
        if isinstance(v, (tuple, list)) and v:
            return _as_dt(v[0])
        return DateTime(v)
    except Exception:
        return DateTime()  # HOY como fallback


def _fmt_date(dt):
    try:
        return _as_dt(dt).strftime("%d/%m/%Y")
    except Exception:
        return u""


class InfolabsaDeltaCheck(BrowserView):
    """Devuelve {'period_label': '6/12 meses', 'rows': [...]} calculado respecto a HOY."""

    def _pick_period_days(self, ar):
        """Si el paciente tiene ≥2 AR en últimos 180 días -> 6m; si no -> 12m."""
        try:
            pc = getattr(ar, "portal_catalog", None)
            patient = _safe_get(ar, "getPatient", None)
            if not pc or not patient:
                return 365
            try:
                patient_uid = patient.UID()
            except Exception:
                return 365

            today = DateTime()
            six_months_ago = today - 180.0  # DateTime - float(días) => DateTime

            brains = pc.searchResults(
                portal_type="AnalysisRequest",
                getPatientUID=patient_uid,
                created={"query": [six_months_ago, today], "range": "min:max"},
            )
            return 180 if len(brains or []) >= 2 else 365
        except Exception:
            return 365

    def _fetch_prev_for_keyword(self, pc, patient_uid, keyword, ar_created, days):
        """Busca AR previo con el mismo analito dentro de [HOY-días, HOY] y anterior al AR actual."""
        try:
            today = DateTime()
            window_start = today - float(days)
            brains = pc.searchResults(
                portal_type="AnalysisRequest",
                getPatientUID=patient_uid,
                sort_on="created",
                sort_order="descending",
                created={"query": [window_start, today], "range": "min:max"},
            )
        except Exception:
            return None, None, None

        ar_ts = _as_dt(ar_created).timeTime() if ar_created else None

        for b in brains:
            try:
                ar_prev = b.getObject()
                if ar_prev == self.context:
                    continue

                prev_created = (_safe_get(ar_prev, "created", None) or
                                _safe_get(ar_prev, "getDateReceived", None) or
                                _safe_get(ar_prev, "getDatePublished", None))
                if ar_ts is not None and prev_created:
                    prev_ts = _as_dt(prev_created).timeTime()
                    if prev_ts >= ar_ts:
                        continue  # Debe ser anterior al actual

                analyses = (_safe_get(ar_prev, "getAnalyses", None) or
                            _safe_get(ar_prev, "analyses", None) or [])
                for a in analyses:
                    kw = _safe_get(a, "getKeyword", None) or _safe_get(a, "Title", None)
                    if not kw:
                        continue
                    if _u(kw).strip().lower() == _u(keyword).strip().lower():
                        val = (_safe_get(a, "getResult", None) or
                               _safe_get(a, "Result", None) or
                               _safe_get(a, "getFormattedResult", None))
                        return (ar_prev,
                                _safe_get(ar_prev, "getId", None) or _safe_get(ar_prev, "id", None),
                                val)
            except Exception:
                continue
        return None, None, None

    def rows(self):
        ar = self.context
        items = (_safe_get(ar, "getAnalyses", None) or
                 _safe_get(ar, "analyses", None) or [])
        rows = []

        days = self._pick_period_days(ar)
        period_label = u"6 meses" if days <= 180 else u"12 meses"

        patient = _safe_get(ar, "getPatient", None)
        pc = getattr(ar, "portal_catalog", None)
        ar_created = (_safe_get(ar, "created", None) or
                      _safe_get(ar, "getDateReceived", None) or
                      _safe_get(ar, "getDatePublished", None))

        patient_uid = None
        if patient:
            try:
                patient_uid = patient.UID()
            except Exception:
                patient_uid = None

        for a in items:
            try:
                name = _safe_get(a, "Title", None) or _safe_get(a, "getKeyword", None) or u""
                keyword = _safe_get(a, "getKeyword", None) or name
                unit = _safe_get(a, "getUnit", None) or _safe_get(a, "Unit", None) or u""
                val_now = (_safe_get(a, "getResult", None) or
                           _safe_get(a, "Result", None) or
                           _safe_get(a, "getFormattedResult", None))
                v_now = _num(val_now)

                prev_id, prev_date_txt, v_prev = u"—", u"—", None
                delta_pct_txt, delta_dir = u"—", u""

                if pc and patient_uid and keyword and ar_created:
                    ar_prev, pid, prev_val = self._fetch_prev_for_keyword(
                        pc, patient_uid, keyword, ar_created, days
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
                    delta_pct_txt = u"{:+.1f}%".format(dpct)
                elif v_now is not None and v_prev is None:
                    delta_dir = u"•"
                    delta_pct_txt = u"N/A"

                rows.append({
                    "name": _u(name),
                    "unit": _u(unit),
                    "value_now": _u(val_now) if val_now is not None else u"—",
                    "prev_sample_id": _u(prev_id),
                    "prev_date": _u(prev_date_txt),
                    "delta_pct": _u(delta_pct_txt),
                    "delta_dir": _u(delta_dir),
                    "period_label": period_label,
                })
            except Exception:
                continue

        return rows, period_label

    def __call__(self):
        try:
            rows, period_label = self.rows()
            return {"rows": rows, "period_label": period_label}
        except Exception as e:
            try:
                logger.exception("DeltaCheck failed: %s", e)
            except Exception:
                pass
            return {"rows": [], "period_label": u"12 meses"}
