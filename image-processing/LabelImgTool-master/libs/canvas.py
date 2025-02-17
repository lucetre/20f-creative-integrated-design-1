from PyQt4.QtGui import *
from PyQt4.QtCore import *
#from PyQt4.QtOpenGL import *

from shape import Shape
from lib import distance

CURSOR_DEFAULT = Qt.ArrowCursor
CURSOR_POINT = Qt.PointingHandCursor
CURSOR_DRAW = Qt.CrossCursor
CURSOR_MOVE = Qt.ClosedHandCursor
CURSOR_GRAB = Qt.OpenHandCursor

# class Canvas(QGLWidget):


class Canvas(QWidget):
    zoomRequest = pyqtSignal(int)
    scrollRequest = pyqtSignal(int, int)
    newShape = pyqtSignal()
    selectionChanged = pyqtSignal(bool)
    shapeMoved = pyqtSignal()
    drawingPolygon = pyqtSignal(bool)

    CREATE, EDIT = range(2)
    RECT_SHAPE, POLYGON_SHAPE = range(2)

    MAX_MASK_LAYER = 2

    epsilon = 11.0

    def __init__(self, *args, **kwargs):
        super(Canvas, self).__init__(*args, **kwargs)
        # Initialise local state.
        self.shape_type = self.POLYGON_SHAPE
        self.brush_point = None
        self.task_mode = 3
        self.current_brush_path = None
        self.mask_Image = None
        self.baseBrushCol  =QColor(255,0,0,255)
        self.eraseBrushCol =QColor(255,255,255,0)
        self.brush_color =self.baseBrushCol
        self.brush_size = 10
        self.brush = QPainter();
        self.mode = self.EDIT
        self.shapes = []
        self.current = None
        self.selectedShape = None  # save the selected shape here
        self.selectedShapeCopy = None
        self.lineColor = QColor(0, 0, 255)
        self.line = Shape(line_color=self.lineColor)
        self.prevPoint = QPointF()
        self.offsets = QPointF(), QPointF()
        self.scale = 1.0
        self.bg_image = QImage()
        self.curMaskIdx = 0
        self.mask_pixmap = []
        for i in range(self.MAX_MASK_LAYER):
            self.mask_pixmap.append(None)
        self.visible = {}
        self._hideBackround = False
        self.hideBackround = False
        self.hShape = None
        self.hVertex = None
        self._painter = QPainter(self)
        self.font_size = 50
        self._cursor = CURSOR_DEFAULT
        self._preMousePos = [0, 0]
        # Menus:
        self.menus = (QMenu(), QMenu())
        # Set widget options.
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.WheelFocus)

    def set_shape_type(self, type):
        if type == 0:
            self.shape_type = self.RECT_SHAPE
            self.line.set_shape_type(type)
            return True
        elif type == 1:
            self.shape_type = self.POLYGON_SHAPE
            self.line.set_shape_type(type)
            return True
        else:
            print "not support the shape type: " + str(type)
            return False

    def enterEvent(self, ev):
        self.overrideCursor(self._cursor)
    def get_mask_image(self):
        return self.mask_pixmap[self.curMaskIdx]

    def leaveEvent(self, ev):
        self.restoreCursor()

    def focusOutEvent(self, ev):
        self.restoreCursor()

    def isVisible(self, shape):
        return self.visible.get(shape, True)

    def drawing(self):
        return self.mode == self.CREATE

    def editing(self):
        return self.mode == self.EDIT

    def setEditing(self, value=True):
        self.mode = self.EDIT if value else self.CREATE
        if not value:  # Create
            self.unHighlight()
            self.deSelectShape()

    def unHighlight(self):
        if self.hShape:
            self.hShape.highlightClear()
        self.hVertex = self.hShape = None

    def selectedVertex(self):
        return self.hVertex is not None

    def mouseMoveEvent(self, ev):
        """Update line with last point and current coordinates."""
        pos = self.transformPos(ev.posF())
        delta = [self._preMousePos[0]-ev.globalX(), self._preMousePos[1]-ev.globalY()]
        self._preMousePos[0] = ev.globalX()
        self._preMousePos[1] = ev.globalY()

        if Qt.MidButton & ev.buttons():
            delta[0] *= -1
            delta[1] *= -1
            if delta[0]!=0 :
                self.scrollRequest.emit(delta[0], Qt.Horizontal)
            if delta[1]!=0 :
                self.scrollRequest.emit(delta[1], Qt.Vertical)
            ev.accept()
            return

        if self.task_mode == 3 and self.curMaskIdx>=0:
            self.brush_point = pos

            if Qt.LeftButton & ev.buttons():
                if self.outOfPixmap(pos):
                    return
                if not self.current_brush_path:
                    self.current_brush_path = QPainterPath()
                    self.current_brush_path.moveTo(pos)
                else:
                    self.current_brush_path.lineTo(pos)
            self.repaint()
            return


        # Polygon drawing.
        if self.drawing():
            self.overrideCursor(CURSOR_DRAW)
            if self.current:
                color = self.lineColor
                if self.outOfPixmap(pos):
                    # Don't allow the user to draw outside the pixmap.
                    # Project the point to the pixmap's edges.
                    pos = self.intersectionPoint(self.current[-1], pos)
                elif len(self.current) > 1 and self.closeEnough(pos, self.current[0]):
                    # Attract line to starting point and colorise to alert the
                    # user:
                    pos = self.current[0]
                    color = self.current.line_color
                    self.overrideCursor(CURSOR_POINT)
                    self.current.highlightVertex(0, Shape.NEAR_VERTEX)
                self.line[1] = pos
                self.line.line_color = color
                self.repaint()
                self.current.highlightClear()
            return

        # Polygon copy moving.
        if Qt.RightButton & ev.buttons():
            if self.selectedShapeCopy and self.prevPoint:
                self.overrideCursor(CURSOR_MOVE)
                self.boundedMoveShape(self.selectedShapeCopy, pos)
                self.repaint()
            elif self.selectedShape:
                self.selectedShapeCopy = self.selectedShape.copy()
                self.repaint()
            return

        # Polygon/Vertex moving.
        if Qt.LeftButton & ev.buttons():
            if self.selectedVertex():
                self.boundedMoveVertex(pos)
                self.shapeMoved.emit()
                self.repaint()
            elif self.selectedShape and self.prevPoint:
                self.overrideCursor(CURSOR_MOVE)
                self.boundedMoveShape(self.selectedShape, pos)
                self.shapeMoved.emit()
                self.repaint()
            return

        # Just hovering over the canvas, 2 posibilities:
        # - Highlight shapes
        # - Highlight vertex
        # Update shape/vertex fill and tooltip value accordingly.
        self.setToolTip("Image")
        for shape in reversed([s for s in self.shapes if self.isVisible(s)]):
            # Look for a nearby vertex to highlight. If that fails,
            # check if we happen to be inside a shape.
            index = shape.nearestVertex(pos, self.epsilon)
            if index is not None:
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex, self.hShape = index, shape
                shape.highlightVertex(index, shape.MOVE_VERTEX)
                self.overrideCursor(CURSOR_POINT)
                self.setToolTip("Click & drag to move point")
                self.setStatusTip(self.toolTip())
                self.update()
                break
            elif shape.containsPoint(pos):
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex, self.hShape = None, shape
                self.setToolTip(
                    "Click & drag to move shape '%s'" %
                    shape.label)
                self.setStatusTip(self.toolTip())
                self.overrideCursor(CURSOR_GRAB)
                self.update()
                break
        else:  # Nothing found, clear highlights, reset state.
            if self.hShape:
                self.hShape.highlightClear()
                self.update()
            self.hVertex, self.hShape = None, None

        ev.accept()

    def mousePressEvent(self, ev):
        pos = self.transformPos(ev.posF())
        self._preMousePos = [ev.globalX(), ev.globalY()]
        if ev.button() == Qt.LeftButton:
            if self.task_mode == 3 and self.curMaskIdx>=0:
                if not self.current_brush_path :
                    self.current_brush_path = QPainterPath()
                self.current_brush_path.moveTo(pos)
                self.brush_point = pos
                self.repaint()

            if self.drawing():
                if self.shape_type == self.POLYGON_SHAPE and self.current:
                    self.current.addPoint(self.line[1])
                    self.line[0] = self.current[-1]
                    if self.current.isClosed():
                        self.finalise()
                elif self.shape_type == self.RECT_SHAPE and self.current and self.current.reachMaxPoints() is False:
                    initPos = self.current[0]
                    minX = initPos.x()
                    minY = initPos.y()
                    targetPos = self.line[1]
                    maxX = targetPos.x()
                    maxY = targetPos.y()
                    self.current.addPoint(QPointF(minX, maxY))
                    self.current.addPoint(targetPos)
                    self.current.addPoint(QPointF(maxX, minY))
                    self.current.addPoint(initPos)
                    self.line[0] = self.current[-1]
                    if self.current.isClosed():
                        self.finalise()
                elif not self.outOfPixmap(pos):
                    self.current = Shape(shape_type=self.shape_type)
                    self.current.addPoint(pos)
                    self.line.points = [pos, pos]
                    self.setHiding()
                    self.drawingPolygon.emit(True)
                    self.update()
            else:
                self.selectShapePoint(pos)
                self.prevPoint = pos
                self.repaint()
        elif ev.button() == Qt.RightButton and self.editing():
            self.selectShapePoint(pos)
            self.prevPoint = pos
            self.repaint()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.RightButton:
            menu = self.menus[bool(self.selectedShapeCopy)]
            self.restoreCursor()
            if not menu.exec_(self.mapToGlobal(ev.pos()))\
               and self.selectedShapeCopy:
                # Cancel the move by deleting the shadow copy.
                self.selectedShapeCopy = None
                self.repaint()
        elif ev.button() == Qt.LeftButton and self.selectedShape:
            self.overrideCursor(CURSOR_GRAB)
        elif ev.button() == Qt.LeftButton and self.task_mode == 3 and self.current_brush_path:
            self.current_brush_path = None

    def endMove(self, copy=False):
        assert self.selectedShape and self.selectedShapeCopy
        shape = self.selectedShapeCopy
        #del shape.fill_color
        #del shape.line_color
        if copy:
            self.shapes.append(shape)
            self.selectedShape.selected = False
            self.selectedShape = shape
            self.repaint()
        else:
            shape.label = self.selectedShape.label
            self.deleteSelected()
            self.shapes.append(shape)
        self.selectedShapeCopy = None

    def hideBackroundShapes(self, value):
        self.hideBackround = value
        if self.selectedShape:
            # Only hide other shapes if there is a current selection.
            # Otherwise the user will not be able to select a shape.
            self.setHiding(True)
            self.repaint()

    def setHiding(self, enable=True):
        self._hideBackround = self.hideBackround if enable else False

    def canCloseShape(self):
        return self.drawing() and self.current and len(self.current) > 2

    def mouseDoubleClickEvent(self, ev):
        # We need at least 4 points here, since the mousePress handler
        # adds an extra one before this handler is called.
        if self.canCloseShape() and len(self.current) > 3:
            self.current.popPoint()
            self.finalise()

    def selectShape(self, shape):
        self.deSelectShape()
        shape.selected = True
        self.selectedShape = shape
        self.setHiding()
        self.selectionChanged.emit(True)
        self.update()

    def selectShapePoint(self, point):
        """Select the first shape created which contains this point."""
        self.deSelectShape()
        if self.selectedVertex():  # A vertex is marked for selection.
            index, shape = self.hVertex, self.hShape
            shape.highlightVertex(index, shape.MOVE_VERTEX)
            return
        for shape in reversed(self.shapes):
            if self.isVisible(shape) and shape.containsPoint(point):
                shape.selected = True
                self.selectedShape = shape
                self.calculateOffsets(shape, point)
                self.setHiding()
                self.selectionChanged.emit(True)
                return

    def calculateOffsets(self, shape, point):
        rect = shape.boundingRect()
        x1 = rect.x() - point.x()
        y1 = rect.y() - point.y()
        x2 = (rect.x() + rect.width()) - point.x()
        y2 = (rect.y() + rect.height()) - point.y()
        self.offsets = QPointF(x1, y1), QPointF(x2, y2)

    def boundedMoveVertex(self, pos):
        index, shape = self.hVertex, self.hShape
        point = shape[index]
        if self.outOfPixmap(pos):
            pos = self.intersectionPoint(point, pos)

        shiftPos = pos - point
        shape.moveVertexBy(index, shiftPos)

        if self.shape_type == self.RECT_SHAPE:
            lindex = (index + 1) % 4
            rindex = (index + 3) % 4
            lshift = None
            rshift = None
            if index % 2 == 0:
                lshift = QPointF(shiftPos.x(), 0)
                rshift = QPointF(0, shiftPos.y())
            else:
                rshift = QPointF(shiftPos.x(), 0)
                lshift = QPointF(0, shiftPos.y())
            shape.moveVertexBy(rindex, rshift)
            shape.moveVertexBy(lindex, lshift)

    def boundedMoveShape(self, shape, pos):
        if self.outOfPixmap(pos):
            return False  # No need to move
        o1 = pos + self.offsets[0]
        if self.outOfPixmap(o1):
            pos -= QPointF(min(0, o1.x()), min(0, o1.y()))
        o2 = pos + self.offsets[1]
        if self.outOfPixmap(o2):
            pos += QPointF(min(0, self.bg_image.width() - o2.x()),
                           min(0, self.bg_image.height() - o2.y()))
        # The next line tracks the new position of the cursor
        # relative to the shape, but also results in making it
        # a bit "shaky" when nearing the border and allows it to
        # go outside of the shape's area for some reason. XXX
        #self.calculateOffsets(self.selectedShape, pos)
        dp = pos - self.prevPoint
        if dp:
            shape.moveBy(dp)
            self.prevPoint = pos
            return True
        return False

    def deSelectShape(self):
        if self.selectedShape:
            self.selectedShape.selected = False
            self.selectedShape = None
            self.setHiding(False)
            self.selectionChanged.emit(False)
            self.update()

    def deleteSelected(self):
        if self.selectedShape:
            shape = self.selectedShape
            self.shapes.remove(self.selectedShape)
            self.selectedShape = None
            self.update()
            return shape

    def copySelectedShape(self):
        if self.selectedShape:
            shape = self.selectedShape.copy()
            self.deSelectShape()
            self.shapes.append(shape)
            shape.selected = True
            self.selectedShape = shape
            self.boundedShiftShape(shape)
            return shape

    def boundedShiftShape(self, shape):
        # Try to move in one direction, and if it fails in another.
        # Give up if both fail.
        point = shape[0]
        offset = QPointF(2.0, 2.0)
        self.calculateOffsets(shape, point)
        self.prevPoint = point
        if not self.boundedMoveShape(shape, point - offset):
            self.boundedMoveShape(shape, point + offset)

    def paintEvent(self, event):
        if not self.bg_image:
            return super(Canvas, self).paintEvent(event)

        p = self._painter
        p.begin(self)
        p.setFont(QFont('Times', self.font_size, QFont.Bold))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        p.scale(self.scale, self.scale)
        p.translate(self.offsetToCenter())

        p.drawImage(0, 0, self.bg_image)
        #print self.brush_point.x(),self.brush_point.y()
        if self.task_mode == 3 and self.curMaskIdx>=0:
            opacityVal = 0.2
            if self.curMaskIdx==1 :
                opacityVal = 0.4
            p.setOpacity(opacityVal)

            if self.brush_point:
                p.drawEllipse(self.brush_point,self.brush_size/2,self.brush_size/2)
            if self.current_brush_path:
                if self.mask_pixmap[self.curMaskIdx].isNull():
                    for i in range(self.MAX_MASK_LAYER) :
                        self.mask_pixmap[i] = QImage(self.bg_image.size(), QImage.Format_ARGB32)
                        self.mask_pixmap[i].fill(QColor(255,255,255,0))
                self.brush.begin(self.mask_pixmap[self.curMaskIdx])
                brush_pen = QPen()
                self.brush.setCompositionMode(QPainter.CompositionMode_Source)
                brush_pen.setColor(self.brush_color)
                brush_pen.setWidth(self.brush_size)
                brush_pen.setCapStyle(Qt.RoundCap)
                brush_pen.setJoinStyle(Qt.RoundJoin)
                self.brush.setPen(brush_pen)
                self.brush.drawPath(self.current_brush_path)
                self.brush.end()
            p.drawImage(0,0,self.mask_pixmap[self.curMaskIdx])

        Shape.scale = self.scale
        for shape in self.shapes:
            if shape.fill_color:
                shape.fill = True
                shape.paint(p)
            elif (shape.selected or not self._hideBackround) and self.isVisible(shape):
                shape.fill = shape.selected or shape == self.hShape
                shape.paint(p)
        if self.current:
            self.current.paint(p)
            self.line.paint(p)
        if self.selectedShapeCopy:
            self.selectedShapeCopy.paint(p)
        # Paint rect
        if self.current is not None and len(self.line) == 2:
            leftTop = self.line[0]
            rightBottom = self.line[1]
            rectWidth = rightBottom.x() - leftTop.x()
            rectHeight = rightBottom.y() - leftTop.y()
            color = QColor(0, 220, 0)
            p.setPen(color)
            brush = QBrush(Qt.BDiagPattern)
            p.setBrush(brush)
            if self.shape_type == self.RECT_SHAPE:
                p.drawRect(leftTop.x(), leftTop.y(), rectWidth, rectHeight)

        p.end()

    def transformPos(self, point):
        """Convert from widget-logical coordinates to painter-logical coordinates."""
        return point / self.scale - self.offsetToCenter()

    def offsetToCenter(self):
        s = self.scale
        area = super(Canvas, self).size()
        if self.bg_image:
            w, h = self.bg_image.width() * s, self.bg_image.height() * s
        else:
            w,h = 100,100
        aw, ah = area.width(), area.height()
        x = (aw - w) / (2 * s) if aw > w else 0
        y = (ah - h) / (2 * s) if ah > h else 0
        return QPointF(x, y)

    def outOfPixmap(self, p):
        w, h = self.bg_image.width(), self.bg_image.height()
        return not (0 <= p.x() <= w and 0 <= p.y() <= h)

    def finalise(self):
        assert self.current
        self.current.close()
        self.shapes.append(self.current)
        self.current = None
        self.setHiding(False)
        self.newShape.emit()
        self.update()

    def closeEnough(self, p1, p2):
        #d = distance(p1 - p2)
        #m = (p1-p2).manhattanLength()
        # print "d %.2f, m %d, %.2f" % (d, m, d - m)
        return distance(p1 - p2) < self.epsilon

    def intersectionPoint(self, p1, p2):
        # Cycle through each image edge in clockwise fashion,
        # and find the one intersecting the current line segment.
        # http://paulbourke.net/geometry/lineline2d/
        size = self.bg_image.size()
        points = [(0, 0),
                  (size.width(), 0),
                  (size.width(), size.height()),
                  (0, size.height())]
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        d, i, (x, y) = min(self.intersectingEdges((x1, y1), (x2, y2), points))
        x3, y3 = points[i]
        x4, y4 = points[(i + 1) % 4]
        if (x, y) == (x1, y1):
            # Handle cases where previous point is on one of the edges.
            if x3 == x4:
                return QPointF(x3, min(max(0, y2), max(y3, y4)))
            else:  # y3 == y4
                return QPointF(min(max(0, x2), max(x3, x4)), y3)
        return QPointF(x, y)

    def intersectingEdges(self, xxx_todo_changeme, xxx_todo_changeme1, points):
        """For each edge formed by `points', yield the intersection
        with the line segment `(x1,y1) - (x2,y2)`, if it exists.
        Also return the distance of `(x2,y2)' to the middle of the
        edge along with its index, so that the one closest can be chosen."""
        (x1, y1) = xxx_todo_changeme
        (x2, y2) = xxx_todo_changeme1
        for i in xrange(4):
            x3, y3 = points[i]
            x4, y4 = points[(i + 1) % 4]
            denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
            nua = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)
            nub = (x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)
            if denom == 0:
                # This covers two cases:
                #   nua == nub == 0: Coincident
                #   otherwise: Parallel
                continue
            ua, ub = nua / denom, nub / denom
            if 0 <= ua <= 1 and 0 <= ub <= 1:
                x = x1 + ua * (x2 - x1)
                y = y1 + ua * (y2 - y1)
                m = QPointF((x3 + x4) / 2, (y3 + y4) / 2)
                d = distance(m - QPointF(x2, y2))
                yield d, i, (x, y)

    # These two, along with a call to adjustSize are required for the
    # scroll area.
    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        if self.bg_image:
            return self.scale * self.bg_image.size()
        return super(Canvas, self).minimumSizeHint()

    def wheelEvent(self, ev):
        if Qt.MidButton & ev.buttons():
            return
            
        if ev.orientation() == Qt.Vertical:
            mods = ev.modifiers()
            if Qt.ControlModifier == int(mods):
                self.zoomRequest.emit(ev.delta())
            else:
                self.scrollRequest.emit(
                    ev.delta() / (8 * 15) * 20, Qt.Horizontal if (
                        Qt.ShiftModifier == int(mods)) else Qt.Vertical)
        else:
            self.scrollRequest.emit(ev.delta() / (8 * 15) * 20, Qt.Horizontal)
        ev.accept()

    def setLastLabel(self, text):
        assert text
        self.shapes[-1].label = text
        return self.shapes[-1]

    def undoLastLine(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        self.line.points = [self.current[-1], self.current[0]]
        self.drawingPolygon.emit(True)

    def resetAllLines(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        self.line.points = [self.current[-1], self.current[0]]
        self.drawingPolygon.emit(True)
        self.current = None
        self.drawingPolygon.emit(False)
        self.update()

    def loadMaskmap(self,mask):
        self.mask_pixmap[self.curMaskIdx] = mask
        self.repaint()
    def loadPixmap(self, pixmap):
        self.bg_image = pixmap
        self.shapes = []
        for i in range(self.MAX_MASK_LAYER) :
            self.mask_pixmap[i] = QImage(self.bg_image.size(), QImage.Format_ARGB32)
            self.mask_pixmap[i].fill(QColor(255,255,255,0))
        self.repaint()

    def loadShapes(self, shapes):
        self.shapes = list(shapes)
        self.shape_type = shapes[0].get_shape_type()
        print self.shape_type
        self.current = None
        self.repaint()

    def setShapeVisible(self, shape, value):
        self.visible[shape] = value
        self.repaint()

    def overrideCursor(self, cursor):
        self.restoreCursor()
        self._cursor = cursor
        QApplication.setOverrideCursor(cursor)

    def restoreCursor(self):
        QApplication.restoreOverrideCursor()

    def resetState(self):
        self.restoreCursor()
        self.bg_image = None
        self.update()

    def setBrushCol(self, num):
        if (num):
            self.brush_color =self.baseBrushCol
        else :
            self.brush_color =self.eraseBrushCol