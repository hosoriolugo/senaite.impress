# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from zope.component.hooks import getSite
from ZODB.POSException import POSKeyError
from StringIO import StringIO
import math
import datetime

try:
    # Pillow (normalmente ya viene en la build de Plone/SENAITE)
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:
    PIL_OK = False


def _to_int(s, default):
    try:
        return int(s)
    except Exception:
        return default


def _parse_ms(x):
    # x puede venir como int ms, int sec, ISO string
    try:
        # número
        n = long(x)
        # si parece segundos (10 dígitos): a ms
        if len(str(abs(n))) <= 10:
            return n * 1000
        return n
    except Exception:
        # cadena
        try:
            # reemplazo para "YYYY-MM-DD HH:MM" sin "T"
            if isinstance(x, basestring) and (' ' in x and 'T' not in x):
                x = x.replace(' ', 'T')
            # Date.parse equivalente simple:
            from DateTime import DateTime
            # DateTime parsea muchos formatos y da epoch en s
            return long(DateTime(x).timeTime() * 1000.0)
        except Exception:
            return None


def _safe_text(val):
    try:
        if isinstance(val, unicode):
            return val
        if isinstance(val, str):
            return val.decode('utf-8', 'ignore')
        return unicode(val)
    except Exception:
        return u''


def _load_font(size):
    """Intenta TTF legible; si no, usa la default (mantiene compatibilidad)."""
    try:
        return ImageFont.truetype('DejaVuSans.ttf', size)
    except Exception:
        try:
            return ImageFont.truetype('arial.ttf', size)
        except Exception:
            return ImageFont.load_default()


def _truncate(draw, text, font, max_w, ellipsis=u'…'):
    """Recorta texto para que quepa en max_w píxeles (no rompe layout)."""
    w, _ = draw.textsize(text, font=font)
    if w <= max_w:
        return text
    lo, hi = 0, max(0, len(text)-1)
    while lo < hi:
        mid = (lo + hi) // 2
        t2 = text[:max(1, mid)] + ellipsis
        w2, _ = draw.textsize(t2, font=font)
        if w2 <= max_w:
            lo = mid + 1
        else:
            hi = mid
    res = text[:max(1, lo-1)] + ellipsis
    if draw.textsize(res, font=font)[0] > max_w and len(res) > 1:
        res = text[:max(1, lo-2)] + ellipsis
    return res


class TrendChartPNG(BrowserView):
    """Devuelve un PNG con líneas de tendencia a partir de @@infolabsa-delta-check"""

    PALETTE = [
        (52, 58, 64),    # gris oscuro
        (13, 110, 253),  # azul
        (253, 126, 20),  # naranja
        (25, 135, 84),   # verde
        (220, 53, 69),   # rojo
        (111, 66, 193),  # púrpura
    ]

    def _get_ar(self, uid):
        # Si llega con context=AR ya sirve; si no, intenta buscar por catálogo
        ar = getattr(self, 'context', None)
        if uid:
            try:
                site = getSite()
                catalog = site.portal_catalog
                brains = catalog(UID=uid)
                if brains:
                    ar = brains[0].getObject()
            except Exception:
                pass
        return ar

    def _get_chartdata(self, ar):
        # Usa el flow ya existente
        trv = getattr(ar, 'restrictedTraverse', None)
        if not trv:
            return {}
        try:
            delta = ar.restrictedTraverse('@@infolabsa-delta-check')() or {}
        except (POSKeyError, Exception):
            return {}
        chart = delta.get('chart_v2') or delta.get('chart') or {}
        # Normaliza a: [{'name':u'...', 'unit':u'..', 'data':[[ms, y], ...]}, ...]
        series = []
        for s in (chart.get('series') or []):
            name = _safe_text(s.get('name') or u'Serie')
            unit = _safe_text(s.get('unit') or u'')
            data = []
            for p in (s.get('data') or []):
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    ms, y = p[0], p[1]
                elif isinstance(p, dict) and 'x' in p and 'y' in p:
                    ms, y = p.get('x'), p.get('y')
                else:
                    continue
                ms = _parse_ms(ms)
                try:
                    y = float(y)
                except Exception:
                    continue
                if ms is not None:
                    data.append([ms, y])
            if data:
                # ordena por X por si acaso
                data.sort(key=lambda r: r[0])
                series.append({'name': name, 'unit': unit, 'data': data})
        return {'series': series}

    def _nice_range(self, vmin, vmax):
        """Redondea mín/máx a números 'bonitos' y devuelve ticks"""
        if vmin == vmax:
            vmin -= 1.0
            vmax += 1.0
        rng = vmax - vmin
        if rng <= 0:
            rng = 1.0
        raw_step = rng / 5.0
        mag = 10 ** int(math.floor(math.log10(raw_step)))
        norm = raw_step / mag
        if   norm < 1.5: step = 1 * mag
        elif norm < 3:   step = 2 * mag
        elif norm < 7:   step = 5 * mag
        else:            step = 10 * mag
        ymin = math.floor(vmin / step) * step
        ymax = math.ceil(vmax / step) * step
        ticks = []
        t = ymin
        i = 0
        while t <= ymax and i < 15:
            ticks.append(t)
            t += step
            i += 1
        return ymin, ymax, ticks

    def __call__(self):
        # --- Parámetros ---
        request = self.request
        uid = request.get('uid') or request.form.get('uid')
        W = max(600, _to_int(request.get('w', 1000), 1000))
        H = max(240, _to_int(request.get('h', 360), 360))
        dpi = max(96, _to_int(request.get('dpi', 144), 144))
        # Tus paddings base (se respetan)
        pad_l = 60
        pad_r = 20
        pad_t = 30
        pad_b = 40
        bg = (255, 255, 255)
        grid = (230, 236, 240)
        axis = (173, 181, 189)
        txt_m = (108, 117, 125)
        txt_b = (0, 0, 0)

        if not PIL_OK:
            # Respuesta de texto clara si Pillow no está
            self.request.response.setHeader('Content-Type', 'text/plain; charset=utf-8')
            return u'Pillow no disponible: no se puede generar el PNG en el servidor.'

        # --- Datos ---
        ar = self._get_ar(uid)
        chart = self._get_chartdata(ar)
        series = chart.get('series') or []

        # Canvas
        im = Image.new('RGB', (W, H), bg)
        dr = ImageDraw.Draw(im)

        # Fuentes (mejor legibilidad si hay TTF; si no, igual que antes)
        font       = _load_font(11)
        font_small = _load_font(10)
        font_title = _load_font(12)

        # Mensaje si no hay datos
        if not series:
            msg = u'Sin datos para gráfico'
            tw, th = dr.textsize(msg, font=font)
            dr.text(((W - tw) / 2, (H - th) / 2), msg, fill=(100, 100, 100), font=font)
            out = StringIO()
            im.save(out, format='PNG', dpi=(dpi, dpi))
            self.request.response.setHeader('Content-Type', 'image/png')
            return out.getvalue()

        # Extremos globales
        xs = []
        ys = []
        for s in series:
            for ms, y in s['data']:
                xs.append(ms)
                ys.append(y)
        xmin = min(xs)
        xmax = max(xs)
        ymin = min(ys)
        ymax = max(ys)
        ymin, ymax, yticks = self._nice_range(ymin, ymax)
        if xmin == xmax:
            xmin -= 1000
            xmax += 1000

        # ========= ENCABEZADO INTELIGENTE (título + leyenda) =========
        title = u'Tendencias de Resultados'
        ttw, tth = dr.textsize(title, font=font_title)

        # Simulación de leyenda para calcular alto total y ajustar pad_t
        leg_start_x = pad_l
        leg_y0 = 6 + tth + 6     # debajo del título
        space_x = 24
        box_w, box_h = 14, 6
        gap = 6

        sim_x = leg_start_x
        sim_y = leg_y0
        last_lh = 0
        # ancho máximo “útil” para una entrada de leyenda
        max_line_w = W - pad_l - pad_r - 120

        for idx, s in enumerate(series):
            name = s['name']
            unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')
            ltw, lth = dr.textsize(label, font=font)
            last_lh = lth
            need_w = box_w + gap + ltw + space_x
            if sim_x + need_w > (W - pad_r - 120):
                sim_x = leg_start_x
                sim_y += lth + 6
            sim_x += need_w

        legend_total_h = (sim_y - leg_y0) + (last_lh or 0)
        # pad_t efectivo: respeta tu pad_t mínimo pero añade lo que realmente ocupa encabezado
        pad_t_eff = max(pad_t, 6 + tth + 6 + legend_total_h + 6)

        # Funciones de escala con pad_t efectivo
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t_eff - pad_b
        # Si por encabezado quedó chico, garantizamos mínimo de 100 px de área
        if plot_h < 100:
            pad_t_eff = max(14, H - pad_b - 100)
            plot_h = H - pad_t_eff - pad_b

        def sx(ms):
            return pad_l + int((ms - xmin) * 1.0 * plot_w / (xmax - xmin))

        def sy(y):
            return pad_t_eff + int((ymax - y) * 1.0 * plot_h / (ymax - ymin))

        # ======== DIBUJO ENCABEZADO (real) ========
        # Título
        dr.text((pad_l, 6), title, fill=txt_b, font=font_title)

        # Leyenda
        leg_x = leg_start_x
        leg_y = leg_y0
        for idx, s in enumerate(series):
            color = self.PALETTE[idx % len(self.PALETTE)]
            name = s['name']
            unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')

            # Truncado defensivo por si el nombre es larguísimo
            label_fit = _truncate(dr, label, font, max(60, max_line_w))

            ltw, lth = dr.textsize(label_fit, font=font)
            need_w = box_w + gap + ltw + space_x
            if leg_x + need_w > (W - pad_r - 120):
                leg_x = leg_start_x
                leg_y += lth + 6

            # Caja de color
            dr.rectangle([leg_x, leg_y + 3, leg_x + box_w, leg_y + 3 + box_h],
                         fill=color, outline=color)
            # Texto
            dr.text((leg_x + box_w + gap, leg_y), label_fit, fill=txt_b, font=font)
            leg_x += need_w

        # ======== GRID + Ejes ========
        for t in yticks:
            ypix = sy(t)
            dr.line([(pad_l, ypix), (W - pad_r, ypix)], fill=grid)
            lab = (u'%0.2f' % t).rstrip('0').rstrip('.')
            tw, th = dr.textsize(lab, font=font_small)
            dr.text((pad_l - 8 - tw, ypix - th / 2), lab, fill=txt_m, font=font_small)

        # Ejes
        dr.line([(pad_l, pad_t_eff), (pad_l, H - pad_b)], fill=axis)
        dr.line([(pad_l, H - pad_b), (W - pad_r, H - pad_b)], fill=axis)
        # Marco tenue del área de plot (mejora lectura)
        dr.rectangle([(pad_l, pad_t_eff), (W - pad_r, H - pad_b)], outline=(222, 226, 230))

        # Etiquetas X (máx 6 o 7, como tenías)
        slots = 6
        if slots > len(xs):
            slots = len(xs)
        if slots > 0:
            step_ms = (xmax - xmin) / float(slots)
            for i in range(slots + 1):
                ms = xmin + int(i * step_ms)
                xpix = sx(ms)
                try:
                    dt = datetime.datetime.utcfromtimestamp(ms / 1000.0)
                    lab = dt.strftime('%d/%m %H:%M')
                except Exception:
                    lab = unicode(ms)
                tw, th = dr.textsize(lab, font=font_small)
                # Evita salirte del área visible
                xtext = max(pad_l, min(xpix - tw / 2, W - pad_r - tw))
                dr.text((xtext, H - pad_b + 4), lab, fill=txt_m, font=font_small)

        # Dibuja series (exactamente como ya lo hacías)
        for idx, s in enumerate(series):
            color = self.PALETTE[idx % len(self.PALETTE)]
            pts = s['data']
            # línea
            last = None
            for (ms, y) in pts:
                xpix = sx(ms)
                ypix = sy(y)
                if last is not None:
                    dr.line([last, (xpix, ypix)], fill=color, width=2)
                last = (xpix, ypix)
            # último punto
            if last:
                r = 3
                dr.ellipse([last[0] - r, last[1] - r, last[0] + r, last[1] + r], fill=color, outline=color)

        # (Conservamos tu título final: ya se dibujó arriba; NO lo duplicamos)

        # Output
        out = StringIO()
        im.save(out, format='PNG', dpi=(dpi, dpi))
        self.request.response.setHeader('Content-Type', 'image/png')
        return out.getvalue()
