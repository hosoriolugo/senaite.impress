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
            if ' ' in x and 'T' not in x:
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
        pad_l = 60
        pad_r = 20
        pad_t = 30
        pad_b = 40
        bg = (255, 255, 255)
        grid = (230, 236, 240)

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

        # Fuentes (usa la por defecto para compatibilidad)
        font = ImageFont.load_default()

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
        # X en ms → también sacamos etiquetas legibles
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

        # Funciones de escala
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b

        def sx(ms):
            return pad_l + int((ms - xmin) * 1.0 * plot_w / (xmax - xmin))

        def sy(y):
            return pad_t + int((ymax - y) * 1.0 * plot_h / (ymax - ymin))

        # Grid Y + eje Y
        for t in yticks:
            ypix = sy(t)
            dr.line([(pad_l, ypix), (W - pad_r, ypix)], fill=grid)
            lab = (u'%0.2f' % t).rstrip('0').rstrip('.')
            tw, th = dr.textsize(lab, font=font)
            dr.text((pad_l - 8 - tw, ypix - th / 2), lab, fill=(108, 117, 125), font=font)
        # Ejes
        dr.line([(pad_l, pad_t), (pad_l, H - pad_b)], fill=(173, 181, 189))
        dr.line([(pad_l, H - pad_b), (W - pad_r, H - pad_b)], fill=(173, 181, 189))

        # Etiquetas X (máx 6)
        # Seleccionamos algunos puntos equiespaciados por ms
        slots = 6
        if slots > len(xs):
            slots = len(xs)
        if slots > 0:
            step_ms = (xmax - xmin) / float(slots)
            labels = []
            for i in range(slots + 1):
                ms = xmin + int(i * step_ms)
                xpix = sx(ms)
                # formatea fecha corta
                try:
                    dt = datetime.datetime.utcfromtimestamp(ms / 1000.0)
                    lab = dt.strftime('%d/%m %H:%M')
                except Exception:
                    lab = unicode(ms)
                tw, th = dr.textsize(lab, font=font)
                dr.text((xpix - tw / 2, H - pad_b + 4), lab, fill=(108, 117, 125), font=font)

        # Dibuja series
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

        # Leyenda
        leg_x = pad_l
        leg_y = 6
        for idx, s in enumerate(series):
            color = self.PALETTE[idx % len(self.PALETTE)]
            name = s['name']
            unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')
            # caja color
            dr.rectangle([leg_x, leg_y + 3, leg_x + 14, leg_y + 9], fill=color, outline=color)
            dr.text((leg_x + 18, leg_y), label, fill=(0, 0, 0), font=font)
            tw, th = dr.textsize(label, font=font)
            leg_x += 18 + tw + 24
            # salto si se acaba el ancho
            if leg_x > W - pad_r - 120:
                leg_x = pad_l
                leg_y += th + 6

        # Título
        title = u'Tendencias de Resultados'
        tw, th = dr.textsize(title, font=font)
        dr.text((pad_l, max(0, pad_t - th - 8)), title, fill=(0, 0, 0), font=font)

        # Output
        out = StringIO()
        im.save(out, format='PNG', dpi=(dpi, dpi))
        self.request.response.setHeader('Content-Type', 'image/png')
        return out.getvalue()
