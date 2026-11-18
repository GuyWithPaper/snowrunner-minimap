"""Map marker badges drawn with QPainter (garage, fuel, watchtower, tasks...)."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush, QPolygonF, QFont

# icon name in level data -> (category, ring colour)
def category(icon):
    i = icon.lower()
    if "garage" in i: return "garage", QColor(70, 150, 255)
    if "fuel" in i: return "fuel", QColor(80, 200, 120)
    if "watchtower" in i: return "tower", QColor(255, 255, 255)
    if "service" in i or "resupply" in i or "repair" in i: return "repair", QColor(80, 200, 120)
    if "trailer" in i: return "trailer", QColor(70, 150, 255)
    if "upgrade" in i: return "upgrade", QColor(255, 200, 40)
    if "gateway" in i: return "gate", QColor(200, 200, 200)
    if "contest" in i: return "contest", QColor(255, 140, 30)
    if "tasksub" in i: return "subtask", QColor(255, 220, 60)
    if "task" in i: return "task", QColor(255, 220, 60)
    if "storage" in i or "lumber" in i or "warehouse" in i: return "storage", QColor(230, 230, 230)
    return "delivery", QColor(230, 230, 230)


IMPORTANT = {"garage", "fuel", "tower", "repair", "trailer", "upgrade", "gate", "storage"}


def draw(p, x, y, icon, size=20, label=None):
    cat, ring = category(icon)
    r = size / 2
    if cat == "subtask":
        r *= 0.7
    p.setPen(QPen(QColor(0, 0, 0, 200), 3)); p.setBrush(QColor(28, 30, 32, 235))
    p.drawEllipse(QPointF(x, y), r, r)
    p.setPen(QPen(ring, max(1.5, size * 0.1))); p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(x, y), r - 1, r - 1)
    s = r * 0.55
    w = QColor(245, 245, 245)
    p.setPen(QPen(w, max(1.2, size * 0.08), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(w)
    P = lambda a, b: QPointF(x + a * s, y + b * s)
    if cat == "garage":
        p.drawPolygon(QPolygonF([P(-1, 0.9), P(-1, -0.2), P(0, -1), P(1, -0.2), P(1, 0.9)]))
        p.setBrush(QColor(28, 30, 32)); p.setPen(Qt.NoPen); p.drawRect(QRectF(x - 0.5 * s, y + 0.05 * s, 1.0 * s, 0.85 * s))
    elif cat == "fuel":
        p.drawRoundedRect(QRectF(x - 0.8 * s, y - 0.9 * s, 1.0 * s, 1.8 * s), 0.2 * s, 0.2 * s)
        p.setBrush(Qt.NoBrush); p.drawPolyline(QPolygonF([P(0.2, -0.4), P(0.8, -0.1), P(0.8, 0.6)]))
        p.setBrush(QColor(28, 30, 32)); p.setPen(Qt.NoPen); p.drawRect(QRectF(x - 0.6 * s, y - 0.7 * s, 0.6 * s, 0.5 * s))
    elif cat == "tower":
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([P(-0.7, 1), P(-0.35, -0.45)])); p.drawPolyline(QPolygonF([P(0.7, 1), P(0.35, -0.45)]))
        p.setBrush(w); p.drawPolygon(QPolygonF([P(-0.75, -0.45), P(0, -1.05), P(0.75, -0.45)]))
        p.drawLine(P(-0.5, 0.3), P(0.5, 0.3))
    elif cat == "repair":
        p.setBrush(Qt.NoBrush); p.drawLine(P(-0.7, 0.7), P(0.3, -0.3)); p.drawEllipse(P(0.45, -0.45), 0.4 * s, 0.4 * s)
    elif cat == "trailer":
        p.drawRect(QRectF(x - 0.9 * s, y - 0.6 * s, 1.5 * s, 0.9 * s)); p.drawLine(P(0.6, 0.1), P(1.0, 0.1))
        p.drawEllipse(P(-0.4, 0.55), 0.22 * s, 0.22 * s)
    elif cat == "upgrade":
        p.drawPolygon(QPolygonF([P(0, -1), P(0.8, 0), P(0.3, 0), P(0.3, 0.9), P(-0.3, 0.9), P(-0.3, 0), P(-0.8, 0)]))
    elif cat == "gate":
        p.drawPolygon(QPolygonF([P(-0.8, -0.35), P(0.1, -0.35), P(0.1, -0.85), P(0.9, 0), P(0.1, 0.85), P(0.1, 0.35), P(-0.8, 0.35)]))
    elif cat == "contest":
        p.setBrush(Qt.NoBrush); p.drawEllipse(P(0, 0.15), 0.75 * s, 0.75 * s); p.drawLine(P(0, 0.15), P(0, -0.3)); p.drawLine(P(-0.25, -0.95), P(0.25, -0.95))
    elif cat in ("task", "subtask"):
        f = QFont("Segoe UI", max(6, int(size * (0.55 if cat == "task" else 0.45))), QFont.Black)
        p.setFont(f); p.setPen(w); p.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignCenter, "!")
    elif cat == "storage":
        p.drawRect(QRectF(x - 0.8 * s, y - 0.5 * s, 1.6 * s, 1.3 * s))
        p.setBrush(QColor(28, 30, 32)); p.setPen(Qt.NoPen); p.drawRect(QRectF(x - 0.8 * s, y - 0.5 * s, 1.6 * s, 0.25 * s))
    else:  # delivery point: flag
        p.drawLine(P(-0.5, 1), P(-0.5, -1)); p.drawPolygon(QPolygonF([P(-0.5, -1), P(0.8, -0.6), P(-0.5, -0.15)]))
    if label:
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        rect = QRectF(x - 90, y + r + 1, 180, 14)
        p.setPen(QColor(0, 0, 0, 230))
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            p.drawText(rect.translated(dx, dy), Qt.AlignHCenter | Qt.AlignTop, label)
        p.setPen(QColor(255, 255, 255)); p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop, label)
