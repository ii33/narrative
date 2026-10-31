import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 人物关系图：人是点，关系是有方向的边。
// 一条关系就是一条属性记录——属性名说的是**对方是这一方的什么人**——所以
// 「A —儿子→ B」读作「B 是 A 的儿子」，反过来那条是「A 是 B 的父亲」。
// 同一个词在两侧各出现一次说明方向被写没了：那种一对合成一条、两头都画箭头。
Item {
    id: page
    objectName: "relationPage"

    property var app
    property var persons: []
    property var edges: []
    property string notice: ""

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
        bridge.loadRelations()
    }

    function apply(payload) {
        persons = payload.persons || []
        edges = payload.edges || []
        canvas.requestPaint()
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
    // `both` 是「两侧写了同一个词」——那不是两条关系，是同一件事被说了一遍，
    // 画成两条只会让人以为方向没了。`slot` 用来把同一对人的多条边错开。
    function links() {
        var grouped = ({})
        for (var i = 0; i < page.edges.length; ++i) {
            var edge = page.edges[i]
            var key = edge.from < edge.to ? edge.from + "|" + edge.to
                                          : edge.to + "|" + edge.from
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
                        && rows[s].kind === row.kind) {
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
                    both: twin >= 0, pair: k, slot: 0
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

    // 一个节点的关系越多，圈越大：中心的枢纽本来就该比边角的人显眼。
    function degreeOf(id) {
        var count = 0
        for (var i = 0; i < page.edges.length; ++i) {
            if (page.edges[i].from === id) count += 1
            if (page.edges[i].to === id) count += 1
        }
        return count
    }

    function radiusOf(person, textWidth) {
        var byDegree = 18 + page.degreeOf(person.id) * 2.6
        return Math.max(20, Math.min(46, byDegree), textWidth / 2 + 9)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: "人物关系图"
                color: page.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Label {
                text: page.persons.length + " 人 · " + page.edges.length + " 条关系"
                color: page.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            Label {
                visible: page.notice.length > 0
                text: page.notice
                color: page.muted
                font.pixelSize: 11
            }
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
                text: "刷新"
                flat: true
                leftPadding: 12
                rightPadding: 12
                onClicked: page.load()
            }
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

            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()

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

                // 世界坐标以 (0,0) 为中心；缩放平移只改这一层，布局不动。
                ctx.translate(width / 2 + page.panX, height / 2 + page.panY)
                ctx.scale(page.zoom, page.zoom)

                // 谁的关系最多谁站中间：主角在圆心，边从中心发散，不会挤成一团。
                var hub = null
                for (var h = 0; h < people.length; ++h) {
                    if (hub === null
                        || page.degreeOf(people[h].id) > page.degreeOf(hub.id))
                        hub = people[h]
                }
                var ring = people
                if (hub !== null && page.degreeOf(hub.id) >= 3) {
                    ring = []
                    for (var k = 0; k < people.length; ++k)
                        if (people[k].id !== hub.id) ring.push(people[k])
                } else {
                    hub = null
                }

                // 圆环半径跟着人数走，但不超过画布：默认视图必须一屏放得下，
                // 想看细节靠缩放，不靠开局就被裁掉一圈人。
                var fit = Math.min(width, height) / 2 - 70
                var radius = Math.max(120, Math.min(fit, 90 + ring.length * 34))

                var pos = ({})
                var place = function (person, x, y) {
                    ctx.font = "12px sans-serif"
                    var tw = ctx.measureText(person.name).width
                    pos[person.id] = { x: x, y: y, r: page.radiusOf(person, tw) }
                }
                if (hub !== null) place(hub, 0, 0)
                for (var i = 0; i < ring.length; ++i) {
                    var angle = -Math.PI / 2 + i * 2 * Math.PI / ring.length
                    place(ring[i], radius * Math.cos(angle), radius * Math.sin(angle))
                }

                // 先画线，再把标签压在线上面：标签带白底，压在线上也读得清。
                for (var e = 0; e < links.length; ++e) {
                    var edge = links[e]
                    var a = pos[edge.from]
                    var b = pos[edge.to]
                    if (!a || !b) continue
                    var dx = b.x - a.x
                    var dy = b.y - a.y
                    var len = Math.sqrt(dx * dx + dy * dy) || 1
                    var ux = dx / len
                    var uy = dy / len
                    // 法线方向：弧往这一侧鼓。同一对人的几条边各占一个位置。
                    var nx = -uy
                    var ny = ux
                    // 法线要按「这一对人」的固定方向算：跟着边自己的方向算，两条
                    // 反向的边会得到相反的法线，正负一抵，还是叠在一条线上。
                    if (edge.from > edge.to) {
                        nx = -nx
                        ny = -ny
                    }
                    // 同一对人的两条边（儿子 / 父亲）要分开到标签不会互相盖住：
                    // 差得不够远时，两条曲线叠在一起，看起来还是「一条边一个词」。
                    var spread = edge.both ? 0.10 : (edge.slot === 0 ? 0.14 : 0.30 * edge.slot)
                    var bow = len * spread
                    var cx = (a.x + b.x) / 2 + nx * bow
                    var cy = (a.y + b.y) / 2 + ny * bow

                    // 端点落在圆周上，不是圆心：箭头要贴着圈。
                    var start = circleToward(a.x, a.y, cx, cy, a.r + 2)
                    var end = circleToward(b.x, b.y, cx, cy, b.r + 6)

                    ctx.beginPath()
                    ctx.lineWidth = 1.2
                    ctx.strokeStyle = page.accent
                    ctx.moveTo(start.x, start.y)
                    ctx.quadraticCurveTo(cx, cy, end.x, end.y)
                    ctx.stroke()

                    arrowHead(ctx, end.x, end.y, end.x - cx, end.y - cy)
                    // 两侧同一个词：一条线两个箭头，方向本来就是对等的。
                    if (edge.both) arrowHead(ctx, start.x, start.y, start.x - cx, start.y - cy)

                    var label = edge.kind + (edge.state ? "（" + edge.state + "）" : "")
                    ctx.font = "11px sans-serif"
                    var lw = ctx.measureText(label).width
                    // 弧线中点：二次贝塞尔 t=0.5
                    var mx = 0.25 * start.x + 0.5 * cx + 0.25 * end.x
                    var my = 0.25 * start.y + 0.5 * cy + 0.25 * end.y
                    ctx.fillStyle = "#ffffff"
                    ctx.fillRect(mx - lw / 2 - 4, my - 8, lw + 8, 16)
                    ctx.fillStyle = page.muted
                    ctx.textAlign = "center"
                    ctx.fillText(label, mx, my + 4)
                    ctx.textAlign = "left"
                }

                for (var p = 0; p < people.length; ++p) {
                    var person = people[p]
                    var spot = pos[person.id]
                    ctx.beginPath()
                    ctx.arc(spot.x, spot.y, spot.r, 0, 2 * Math.PI)
                    ctx.fillStyle = "#ffffff"
                    ctx.fill()
                    ctx.strokeStyle = page.accent
                    ctx.lineWidth = 1
                    ctx.stroke()
                    ctx.fillStyle = page.ink
                    ctx.font = "12px sans-serif"
                    ctx.textAlign = "center"
                    ctx.fillText(person.name, spot.x, spot.y + 4)
                    ctx.textAlign = "left"
                }
            }

            // 拖动平移；双击回到原样。
            MouseArea {
                anchors.fill: parent
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                property real lastX: 0
                property real lastY: 0
                onPressed: function (mouse) {
                    lastX = mouse.x
                    lastY = mouse.y
                }
                onPositionChanged: function (mouse) {
                    if (!pressed) return
                    page.panBy(mouse.x - lastX, mouse.y - lastY)
                    lastX = mouse.x
                    lastY = mouse.y
                }
                onDoubleClicked: page.resetView()
            }

            // 鼠标滚轮缩放；触控板两指滑动平移，捏合（Ctrl+滚轮）缩放。
            WheelHandler {
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: function (event) {
                    var pinch = (event.modifiers & Qt.ControlModifier) !== 0
                    var px = event.pixelDelta
                    var panned = px && (Math.abs(px.x) > 0 || Math.abs(px.y) > 0)
                    if (!pinch && panned) {
                        page.panBy(px.x, px.y)
                    } else {
                        var steps = event.angleDelta.y / 120
                        if (steps !== 0) {
                            var at = page.wheelPoint(event)
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
                + "同一个词在两侧各出现一次时合成一条、两头带箭头。"
                + "滚轮或双指捏合缩放，拖动平移，双击复位。括号里是关系当前的状态。"
            color: page.muted
            font.pixelSize: 11
        }
    }

    // 滚轮事件在哪一点上。Qt 6.6 起是 `point.position`，更早是 `position`；
    // 拿不到就退回画布中心——缩放照样能用，只是不以光标为锚。
    function wheelPoint(event) {
        if (event.position !== undefined && event.position !== null)
            return event.position
        if (event.point !== undefined && event.point !== null
            && event.point.position !== undefined)
            return event.point.position
        if (event.x !== undefined)
            return { x: event.x, y: event.y }
        return { x: canvas.width / 2, y: canvas.height / 2 }
    }

    // 从圆心朝 (qx,qy) 走 r 的那一点：边要落在圆周上。
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
