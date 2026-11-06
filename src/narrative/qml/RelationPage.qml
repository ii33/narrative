import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "RelationLayout.js" as RelationLayout

// 人物关系图：人是点，关系是有方向的边。
// 一条关系就是一条属性记录——属性名说的是**对方是这一方的什么人**——所以
// 「A —儿子→ B」读作「B 是 A 的儿子」，反过来那条是「A 是 B 的父亲」。
// 同一个词在两侧各出现一次说明方向被写没了：那种一对合成一条、两头都画箭头。
Item {
    id: page
    objectName: "relationPage"
    clip: true

    property var app
    property var persons: []
    property var edges: []
    property string notice: ""
    property var graphLayout: ({positions: {}, fitScale: 1})
    property var graphLabelLayout: ({edges: [], fitScale: 1})
    property bool labelGeometryReady: false
    property var displayLabelLayout: ({edges: []})
    property real measuredLabelScale: -1
    property string hoveredEdgeKey: ""
    readonly property real labelScreenFontSize: Math.max(10, Math.min(14, 11 * Math.pow(page.zoom, 0.18)))
    readonly property real labelWorldFontSize: page.labelScreenFontSize / Math.max(.01, page.zoom * page.graphLabelLayout.fitScale)
    onZoomChanged: { page.hoveredEdgeKey = ""; canvas.requestPaint() }

    function labelAt(x, y) {
        var scale = page.zoom * page.graphLabelLayout.fitScale
        var wx = (x-canvas.width/2-page.panX)/scale, wy = (y-canvas.height/2-page.panY)/scale
        var routes = page.displayLabelLayout.edges || []
        for (var i=routes.length-1; i>=0; --i) {
            var box = routes[i].label
            if (wx>=box.x && wx<=box.x+box.w && wy>=box.y && wy<=box.y+box.h) return routes[i].edge_key
        }
        return ""
    }
    function highlightedNode(id) {
        var routes=page.displayLabelLayout.edges || []
        for (var i=0; i<routes.length; ++i)
            if (routes[i].edge_key===page.hoveredEdgeKey) return routes[i].edge.from===id || routes[i].edge.to===id
        return false
    }

    function rebuildLayout() {
        var radii = Object.create(null)
        for (var i = 0; i < page.persons.length; ++i) radii[page.persons[i].id] = page.radiusOf(page.persons[i])
        page.labelGeometryReady = false
        page.measuredLabelScale = -1
        page.hoveredEdgeKey = ""
        page.graphLayout = RelationLayout.layout(page.persons, page.links(), radii, canvas.width, canvas.height)
        canvas.requestPaint()
    }

    // 阅读位置与筛选：和「数据浏览」同一个概念——停在哪儿，就只画读者已经知道的关系。
    property string cutId: ""
    property bool relatedOnly: false
    property var moments: ({ levels: [], nodes: [] })
    property string cutName: ""

    // 画布是自由的：缩放、平移都是界面状态，不改任何数据。
    property real zoom: 1
    property real panX: 0
    property real panY: 0
    property real pinchStart: 1

    readonly property real minZoom: 0.35
    readonly property real maxZoom: 3.0

    readonly property var t: app ? app.theme : ({})
    readonly property string ink: t && t.ink ? t.ink : "#16233a"
    readonly property string muted: t && t.muted ? t.muted : "#6b7a90"
    readonly property string line: t && t.line ? t.line : "#dfe6ef"
    readonly property string accent: t && t.accent ? t.accent : "#2b6cb0"

    function load() {
        page.notice = ""
        bridge.loadRelations(page.cutId, page.relatedOnly)
    }

    function apply(payload) {
        persons = payload.persons || []
        edges = payload.edges || []
        moments = payload.moments || ({ levels: [], nodes: [] })
        cutName = payload.cut_name || ""
        if (!page.cutId && payload.cut) page.cutId = payload.cut
        page.rebuildLayout()
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind !== "relations") return
            page.apply(payload)
        }
        function onErrorOccurred(kind, message) {
            if (kind === "relations") page.notice = message
        }
    }

    // 切到这一页才去问：启动时还没有打开工程，问了只会报错。
    onVisibleChanged: if (visible) load()

    // -- 画布状态 -------------------------------------------------------

    function clampZoom(value) {
        return Math.max(page.minZoom, Math.min(page.maxZoom, value))
    }

    // 以某一点为中心缩放：那一点在屏幕上不动，其余的散开或聚拢。
    function zoomAt(x, y, factor) {
        var before = page.zoom
        var after = page.clampZoom(before * factor)
        if (after === before) return
        var cx = canvas.width / 2
        var cy = canvas.height / 2
        page.panX = x - cx - (x - cx - page.panX) * (after / before)
        page.panY = y - cy - (y - cy - page.panY) * (after / before)
        page.zoom = after
        canvas.requestPaint()
    }

    function panBy(dx, dy) {
        page.panX += dx
        page.panY += dy
        canvas.requestPaint()
    }

    function resetView() {
        page.zoom = 1
        page.panX = 0
        page.panY = 0
        canvas.requestPaint()
    }

    // -- 数据：把两条同名的反向记录读成一条双向边 ------------------------

    // 返回 [{from, to, kind, state, both, slot}]。
    // 显式对称定义或同性质反向记录显示双箭头；普通单条关系保留方向。
    // `slot` 用来把同一对人的多条边错开。
    function links() {
        var grouped = Object.create(null)
        for (var i = 0; i < page.edges.length; ++i) {
            var edge = page.edges[i]
            var key = JSON.stringify(edge.from < edge.to ? [edge.from, edge.to] : [edge.to, edge.from])
            if (!grouped[key]) grouped[key] = []
            grouped[key].push(edge)
        }
        var out = []
        for (var k in grouped) {
            var rows = grouped[k]
            var used = []
            for (var r = 0; r < rows.length; ++r) {
                if (used.indexOf(r) >= 0) continue
                var row = rows[r]
                var twin = -1
                for (var s = r + 1; s < rows.length; ++s) {
                    if (used.indexOf(s) >= 0) continue
                    if (rows[s].from === row.to && rows[s].to === row.from
                        && rows[s].kind === row.kind
                        && (rows[s].state || "") === (row.state || "")
                        && (rows[s].scope || "world") === (row.scope || "world")
                        && (rows[s].holder_id || "") === (row.holder_id || "")
                        && (rows[s].context_id || "canon") === (row.context_id || "canon")) {
                        twin = s
                        break
                    }
                }
                used.push(r)
                if (twin >= 0) used.push(twin)
                out.push({
                    from: row.from, to: row.to,
                    fromName: row.from_name, toName: row.to_name,
                    kind: row.kind, state: row.state || "",
                    scope: row.scope || "world", holder_id: row.holder_id || "", context_id: row.context_id || "canon",
                    both: row.symmetric === true || twin >= 0, pair: k, slot: 0
                })
            }
        }
        // 同一对人的第 2、3 条边要往两边弯，不然叠成一条。
        for (var q = 0; q < out.length; ++q) {
            var mates = out.filter(function (item) { return item.pair === out[q].pair })
            out[q].slot = mates.indexOf(out[q]) - (mates.length - 1) / 2
        }
        return out
    }

    // Radius counts distinct neighbours, never parallel labels or directions.
    function degreeOf(id) {
        var neighbors = Object.create(null)
        for (var i = 0; i < page.edges.length; ++i) {
            var edge = page.edges[i]
            if (edge.from === id && edge.to !== id) neighbors[edge.to] = true
            if (edge.to === id && edge.from !== id) neighbors[edge.from] = true
        }
        return Object.keys(neighbors).length
    }

    // Label lengths and repeated relations cannot change a node's radius.
    function radiusOf(person) {
        var degree = page.degreeOf(person.id)
        return Math.max(13, Math.min(44, 13 + 5.2 * Math.sqrt(degree)))
    }

    FontMetrics {
        id: nodeFontMetrics
        font.family: "sans-serif"
        font.pixelSize: 12
    }

    // 在圆的内接正方形中排完整名称，四角也不会越出圆边。
    function nodeLabel(name, radius) {
        var room = Math.max(1, (radius - 3) * Math.SQRT2)
        var characters = Array.from(String(name || ""))
        var size = 12
        var rows, widths, row, rowWidth
        while (true) {
            rows = []
            widths = []
            row = ""
            rowWidth = 0
            for (var i = 0; i < characters.length; ++i) {
                var character = characters[i]
                if (character === "\n") {
                    rows.push(row)
                    widths.push(rowWidth)
                    row = ""
                    rowWidth = 0
                    continue
                }
                var trial = row + character
                var trialWidth = nodeFontMetrics.advanceWidth(trial) * size / 12
                if (row.length > 0 && trialWidth > room) {
                    rows.push(row)
                    widths.push(rowWidth)
                    row = character
                    rowWidth = nodeFontMetrics.advanceWidth(character) * size / 12
                } else {
                    row = trial
                    rowWidth = trialWidth
                }
            }
            rows.push(row)
            widths.push(rowWidth)
            var lineHeight = Math.max(nodeFontMetrics.height, 12) * size / 12
            if (rows.length * lineHeight <= room
                && Math.max.apply(null, widths) <= room)
                return { lines: rows, widths: widths, fontSize: size,
                         lineHeight: lineHeight, room: room }
            size *= 0.9
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 8

            Label {
                text: "人物关系图"
                color: page.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Label {
                text: page.persons.length + " 人 · " + page.links().length + " 条关系"
                    + (page.cutId ? "（读到 " + (page.cutName || page.cutId) + "）" : "")
                color: page.muted
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.minimumWidth: 0
                Layout.maximumWidth: 320
            }
            Item { Layout.fillWidth: true }
        }

        Flow {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 8
            RowLayout {
                width: implicitWidth
                height: implicitHeight
                spacing: 8
                Label {
                    text: "读到"
                    color: page.muted
                    font.pixelSize: 11
                }
                Button {
                    id: cutButton
                    objectName: "relationCut"
                    Layout.minimumWidth: 0
                    Layout.preferredWidth: 240
                    Layout.maximumWidth: 240
                    font.pixelSize: 11
                    text: page.cutName || "最新（读到末尾）"
                    onClicked: cutPopup.open()
                }
                Button {
                    text: "最新"
                    enabled: page.cutId !== ""
                    onClicked: page.chooseCut("", "")
                }

                CheckBox {
                    id: relatedBox
                    objectName: "relationRelatedOnly"
                    text: "只看相关"
                    enabled: page.cutId !== ""
                    checked: page.relatedOnly && page.cutId !== ""
                    font.pixelSize: 11
                    onToggled: {
                        page.relatedOnly = checked
                        page.load()
                    }
                }
            }
            RowLayout {
                width: implicitWidth
                height: implicitHeight
                spacing: 8
                Label {
                    objectName: "relationZoomLabel"
                    text: Math.round(page.zoom * 100) + "%"
                    color: page.muted
                    font.pixelSize: 11
                }
                Button {
                    objectName: "relationZoomOut"
                    text: "−"
                    flat: true
                    leftPadding: 12
                    rightPadding: 12
                    onClicked: page.zoomAt(canvas.width / 2, canvas.height / 2, 1 / 1.2)
                }
                Button {
                    objectName: "relationZoomIn"
                    text: "+"
                    flat: true
                    leftPadding: 12
                    rightPadding: 12
                    onClicked: page.zoomAt(canvas.width / 2, canvas.height / 2, 1.2)
                }
                Button {
                    objectName: "relationReset"
                    text: "复位"
                    flat: true
                    leftPadding: 12
                    rightPadding: 12
                    onClicked: page.resetView()
                }
                Button {
                    objectName: "relationRefresh"
                    text: "刷新"
                    flat: true
                    leftPadding: 12
                    rightPadding: 12
                    onClicked: page.load()
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: page.notice.length > 0
            text: page.notice
            color: page.muted
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: page.line
        }

        // 一张图画完：圆环排开人物，边上写关系性质，括号里是状态。
        Canvas {
            id: canvas
            objectName: "relationCanvas"
            Layout.fillWidth: true
            Layout.fillHeight: true

            onWidthChanged: page.rebuildLayout()
            onHeightChanged: page.rebuildLayout()

            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var people = page.persons
                var links = page.links()
                if (people.length === 0) {
                    ctx.fillStyle = page.muted
                    ctx.font = "13px sans-serif"
                    ctx.fillText("还没有人物节点", 20, 30)
                    return
                }

                if (!page.labelGeometryReady) {
                    ctx.font = "11px sans-serif"
                    var sizes = links.map(function(edge) {
                        var text = edge.kind + (edge.state ? "（" + edge.state + "）" : "")
                        return {width: ctx.measureText(text).width, height: 18}
                    })
                    page.graphLabelLayout = RelationLayout.routeEdges(page.graphLayout.positions, links, sizes, width, height)
                    page.labelGeometryReady = true
                }
                var viewScale = page.zoom * page.graphLabelLayout.fitScale
                if (page.measuredLabelScale !== viewScale) {
                    ctx.font = "12px sans-serif"
                    var textRatio = page.labelWorldFontSize / 12
                    var displaySizes = links.map(function(edge) {
                        var text=edge.kind+(edge.state?"（"+edge.state+"）":"")
                        return {width:ctx.measureText(text).width*textRatio, height:page.labelWorldFontSize*1.5}
                    })
                    page.displayLabelLayout = {edges: RelationLayout.placeLabels(page.graphLayout.positions, page.graphLabelLayout.edges, displaySizes)}
                    page.measuredLabelScale = viewScale
                }
                // Camera, nodes and curves stay fixed while readable labels remeasure.
                ctx.translate(width / 2 + page.panX, height / 2 + page.panY)
                ctx.scale(viewScale, viewScale)
                var pos = page.graphLayout.positions
                var routes = page.displayLabelLayout.edges
                // All strokes precede all labels, so a later edge cannot run
                // through text that was already painted.
                for (var e = 0; e < routes.length; ++e) {
                    var route = routes[e]
                    ctx.beginPath()
                    ctx.lineWidth = 3.4
                    ctx.strokeStyle = "#ffffff"
                    ctx.moveTo(route.start.x, route.start.y)
                    ctx.quadraticCurveTo(route.cx, route.cy, route.end.x, route.end.y)
                    ctx.stroke()
                    ctx.beginPath()
                    ctx.lineWidth = page.hoveredEdgeKey === route.edge_key ? 2.8 : 1.2
                    ctx.strokeStyle = page.hoveredEdgeKey && page.hoveredEdgeKey !== route.edge_key ? page.line : page.accent
                    ctx.moveTo(route.start.x, route.start.y)
                    ctx.quadraticCurveTo(route.cx, route.cy, route.end.x, route.end.y)
                    ctx.stroke()
                    arrowHead(ctx, route.end.x, route.end.y, route.end.x-route.cx, route.end.y-route.cy)
                    if (route.edge.both) arrowHead(ctx, route.start.x, route.start.y, route.start.x-route.cx, route.start.y-route.cy)
                }
                for (e = 0; e < routes.length; ++e) {
                    route = routes[e]
                    var leader = route.leader
                    if (Math.abs(leader.from.x-leader.to.x) + Math.abs(leader.from.y-leader.to.y) > 2) {
                        ctx.beginPath()
                        ctx.lineWidth = page.hoveredEdgeKey === route.edge_key ? 1.6 : .85
                        ctx.strokeStyle = page.accent
                        // Manual dashes work on Qt Canvas versions without setLineDash.
                        var leaderDx = leader.to.x-leader.from.x, leaderDy = leader.to.y-leader.from.y
                        var leaderLength = Math.sqrt(leaderDx*leaderDx+leaderDy*leaderDy)
                        var dash = 5/viewScale, gap = 3/viewScale
                        for (var offset=0; offset<leaderLength; offset+=dash+gap) {
                            var fromRatio=offset/leaderLength, toRatio=Math.min(offset+dash,leaderLength)/leaderLength
                            ctx.moveTo(leader.from.x+leaderDx*fromRatio,leader.from.y+leaderDy*fromRatio)
                            ctx.lineTo(leader.from.x+leaderDx*toRatio,leader.from.y+leaderDy*toRatio)
                        }
                        ctx.stroke()
                        ctx.beginPath()
                        ctx.arc(leader.to.x, leader.to.y, 2.3/viewScale, 0, 2*Math.PI)
                        ctx.fillStyle = page.accent
                        ctx.fill()
                    }
                }
                ctx.font = "12px sans-serif"
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                for (e = 0; e < routes.length; ++e) {
                    var box = routes[e].label
                    ctx.fillStyle = "#ffffff"
                    ctx.fillRect(box.x, box.y, box.w, box.h)
                    ctx.strokeStyle = page.hoveredEdgeKey === routes[e].edge_key ? page.accent : page.line
                    ctx.lineWidth = (page.hoveredEdgeKey === routes[e].edge_key ? 1.2 : .5)/viewScale
                    ctx.strokeRect(box.x,box.y,box.w,box.h)
                    ctx.fillStyle = page.accent
                    ctx.save()
                    ctx.translate(box.x+box.w/2,box.y+box.h/2)
                    ctx.scale(page.labelWorldFontSize/12,page.labelWorldFontSize/12)
                    ctx.fillText(box.text,0,0)
                    ctx.restore()
                }
                ctx.textAlign = "left"

                for (var p = 0; p < people.length; ++p) {
                    var person = people[p]
                    var spot = pos[person.id]
                    ctx.beginPath()
                    ctx.arc(spot.x, spot.y, spot.r, 0, 2 * Math.PI)
                    ctx.fillStyle = "#ffffff"
                    ctx.fill()
                    ctx.strokeStyle = page.accent
                    ctx.lineWidth = page.highlightedNode(person.id) ? 2.8 : 1
                    ctx.stroke()
                    ctx.fillStyle = page.ink
                    ctx.textAlign = "center"
                    var label = page.nodeLabel(person.name, spot.r)
                    // 用画布缩放保留小数字号，避免字体解析时取整后越界。
                    ctx.save()
                    ctx.translate(spot.x, spot.y)
                    var textScale = label.fontSize / 12
                    ctx.scale(textScale, textScale)
                    ctx.font = "12px sans-serif"
                    ctx.textBaseline = "middle"
                    for (var row = 0; row < label.lines.length; ++row) {
                        var y = (row - (label.lines.length - 1) / 2) * label.lineHeight / textScale
                        ctx.fillText(label.lines[row], 0, y)
                    }
                    ctx.restore()
                    ctx.textAlign = "left"
                }
            }

            // 拖动平移；双击回到原样。
            MouseArea {
                objectName: "relationPointer"
                anchors.fill: parent
                hoverEnabled: true
                onExited: { page.hoveredEdgeKey = ""; canvas.requestPaint() }
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                property real lastX: 0
                property real lastY: 0
                onPressed: function (mouse) {
                    lastX = mouse.x
                    lastY = mouse.y
                }
                onPositionChanged: function (mouse) {
                    if (!pressed) {
                        page.hoveredEdgeKey = page.labelAt(mouse.x, mouse.y)
                        canvas.requestPaint()
                        return
                    }
                    page.panBy(mouse.x - lastX, mouse.y - lastY)
                    lastX = mouse.x
                    lastY = mouse.y
                }
                onDoubleClicked: page.resetView()
            }

            // 鼠标滚轮缩放；触控板两指滑动平移，捏合（Ctrl+滚轮）缩放。
            WheelHandler {
                target: null
                blocking: false
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: function (event) {
                    var at = page.wheelPoint(event)
                    if (!at || !canvas.contains(at)
                        || !page.contains(canvas.mapToItem(page, at.x, at.y))) {
                        event.accepted = false
                        return
                    }
                    var pinch = (event.modifiers & Qt.ControlModifier) !== 0
                    var px = event.pixelDelta
                    var panned = px && (Math.abs(px.x) > 0 || Math.abs(px.y) > 0)
                    if (!pinch && panned) {
                        page.panBy(px.x, px.y)
                    } else {
                        var steps = event.angleDelta.y / 120
                        if (steps !== 0) {
                            page.zoomAt(at.x, at.y, Math.pow(1.18, steps))
                        }
                    }
                    event.accepted = true
                }
            }

            // 触摸屏与触控板的捏合。
            PinchArea {
                anchors.fill: parent
                pinch.minimumScale: 0.2
                pinch.maximumScale: 6
                onPinchStarted: page.pinchStart = page.zoom
                onPinchUpdated: function (pinch) {
                    var wanted = page.clampZoom(page.pinchStart * pinch.scale)
                    page.zoomAt(pinch.center.x, pinch.center.y, wanted / page.zoom)
                }
            }
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "箭头从这一方指向对方：A —儿子→ B 读作「B 是 A 的儿子」，反过来写「父亲」。"
                + "对称关系或同性质反向记录合成双箭头。"
                + "滚轮或双指捏合缩放，拖动平移，双击复位。括号里是关系当前的状态。"
                + "悬停标签高亮关系；虚线为标签引线。"
            color: page.muted
            font.pixelSize: 11
        }
    }

    // 滚轮事件在哪一点上。Qt 6.6 起是 `point.position`，更早是 `position`；
    // 拿不到就退回画布中心——缩放照样能用，只是不以光标为锚。
    function wheelPoint(event) {
        if (event.point && event.point.scenePosition)
            return canvas.mapFromItem(null, event.point.scenePosition.x, event.point.scenePosition.y)
        if (event.position !== undefined && event.position !== null) return event.position
        if (event.x !== undefined) return Qt.point(event.x, event.y)
        return null
    }

    // 标签放在弧上哪一点：优先中点，被圈挡住就依次往两头挪。
    // 时间点：与「数据浏览」同一套三层——大事件 → 场景 → 事件。
    // 列里放哪些行按 `level` 判断，不按后端算的序号，免得层级错位时串列。
    function chooseCut(id, name) {
        page.cutId = id || ""
        page.cutName = name || ""
        page.relatedOnly = page.relatedOnly && page.cutId !== ""
        page.load()
    }

    ReadingPositionPicker {
        id: cutPopup
        namePrefix: "relationCut"
        tree: page.moments
        t: page.t
        currentId: page.cutId
        viewportWidth: page.width
        viewportHeight: page.height
        onPicked: function(id, name) { page.chooseCut(id, name) }
    }

    function circleToward(px, py, qx, qy, r) {
        var vx = qx - px
        var vy = qy - py
        var l = Math.sqrt(vx * vx + vy * vy) || 1
        return { x: px + vx / l * r, y: py + vy / l * r }
    }

    function arrowHead(ctx, x, y, tx, ty) {
        var l = Math.sqrt(tx * tx + ty * ty) || 1
        var hx = tx / l
        var hy = ty / l
        var size = 8
        ctx.beginPath()
        ctx.fillStyle = page.accent
        ctx.moveTo(x, y)
        ctx.lineTo(x - hx * size - hy * size * 0.55, y - hy * size + hx * size * 0.55)
        ctx.lineTo(x - hx * size + hy * size * 0.55, y - hy * size - hx * size * 0.55)
        ctx.closePath()
        ctx.fill()
    }
}
