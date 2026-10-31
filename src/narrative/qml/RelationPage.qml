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
        page.syncCutIndex()
        canvas.requestPaint()
    }

    // 时间点下拉：最新 + 每一场（按阅读顺序）。选一场就是「读到那一场」。
    readonly property var cutOptions: {
        var rows = [{ id: "", label: "最新（读到末尾）" }]
        var nodes = (page.moments.nodes || [])
        for (var i = 0; i < nodes.length; ++i) {
            var node = nodes[i]
            if (node.level !== "scene") continue
            rows.push({
                id: node.id,
                label: (node.label_id ? node.label_id + "  " : "") + node.name
            })
        }
        return rows
    }

    property int cutIndex: 0
    function syncCutIndex() {
        for (var i = 0; i < page.cutOptions.length; ++i) {
            if (page.cutOptions[i].id === page.cutId) { page.cutIndex = i; return }
        }
        page.cutIndex = 0
    }

    function pickCut(index) {
        var rows = page.cutOptions
        page.cutId = (index >= 0 && index < rows.length) ? rows[index].id : ""
        page.load()
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

    // 半径**只看关系条数**：按面积算，所以取平方根。
    // 之前把「名字要多宽」也算进半径，结果名字长的零关系节点比名字短的枢纽还大——
    // 大小成了名字长度的函数，关系多少反而看不出来。名字放不下就写到圈外，不撑大圈。
    function radiusOf(person) {
        var degree = page.degreeOf(person.id)
        return Math.max(13, Math.min(44, 13 + 5.2 * Math.sqrt(degree)))
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
                    + (page.cutId ? "（读到 " + (page.cutName || page.cutId) + "）" : "")
                color: page.muted
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.maximumWidth: 320
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "读到"
                color: page.muted
                font.pixelSize: 11
            }
            Button {
                id: cutButton
                objectName: "relationCut"
                Layout.preferredWidth: 240
                font.pixelSize: 11
                text: page.cutName || "最新（读到末尾）"
                onClicked: cutPopup.open()
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
                    pos[person.id] = { x: x, y: y, r: page.radiusOf(person) }
                }
                if (hub !== null) place(hub, 0, 0)
                for (var i = 0; i < ring.length; ++i) {
                    var angle = -Math.PI / 2 + i * 2 * Math.PI / ring.length
                    place(ring[i], radius * Math.cos(angle), radius * Math.sin(angle))
                }

                // 先画线，再把标签压在线上面：标签带白底，压在线上也读得清。
                // `placed` 记住已经放下的标签，后面挑位置时避开它们。
                var placed = []
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
                    // 弧别从别的圈里穿过去：穿过去就被节点盖住，读者以为这条线断了。
                    // 往同一侧加大弯曲，最多试六次；实在绕不开就让它从下面走。
                    var tries = 0
                    while (tries < 6 && !page.curveIsClear(a, b, cx, cy, pos, edge.from, edge.to)) {
                        bow *= 1.45
                        cx = (a.x + b.x) / 2 + nx * bow
                        cy = (a.y + b.y) / 2 + ny * bow
                        tries += 1
                    }

                    // 端点落在圆周上，不是圆心：箭头要贴着圈。
                    var start = circleToward(a.x, a.y, cx, cy, a.r + 2)
                    var end = circleToward(b.x, b.y, cx, cy, b.r + 6)

                    // 先描一条白边：两条线交叉时能看出谁在上面，不会糊成一团。
                    ctx.beginPath()
                    ctx.lineWidth = 3.4
                    ctx.strokeStyle = "#ffffff"
                    ctx.moveTo(start.x, start.y)
                    ctx.quadraticCurveTo(cx, cy, end.x, end.y)
                    ctx.stroke()

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
                    // 标签别压在圈上：中点被挡住就沿着弧往两头挪。
                    var spot = page.labelSpot(start, cx, cy, end, pos, edge.from, edge.to,
                                              placed, lw)
                    var mx = spot.x
                    var my = spot.y
                    placed.push({ x: mx - lw / 2 - 4, y: my - 8, w: lw + 8, h: 16 })
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
                    ctx.textAlign = "center"
                    ctx.font = "12px sans-serif"
                    var room = spot.r * 2 - 8
                    if (ctx.measureText(person.name).width <= room) {
                        ctx.fillText(person.name, spot.x, spot.y + 4)
                    } else {
                        // 圈里放不下就写在圈下面：圈的大小是关系数说了算，不是名字。
                        // 带白底，免得压在关系线上读不清。
                        ctx.font = "11px sans-serif"
                        var w = ctx.measureText(person.name).width
                        var ly = spot.y + spot.r + 13
                        ctx.fillStyle = "#ffffff"
                        ctx.fillRect(spot.x - w / 2 - 3, ly - 9, w + 6, 13)
                        ctx.fillStyle = page.ink
                        ctx.fillText(person.name, spot.x, ly)
                    }
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

    // 标签放在弧上哪一点：优先中点，被圈挡住就依次往两头挪。
    function labelSpot(start, cx, cy, end, pos, skipA, skipB, placed, width) {
        var tries = [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.2, 0.8]
        for (var i = 0; i < tries.length; ++i) {
            var t = tries[i]
            var u = 1 - t
            var px = u * u * start.x + 2 * u * t * cx + t * t * end.x
            var py = u * u * start.y + 2 * u * t * cy + t * t * end.y
            var blocked = false
            for (var id in pos) {
                if (id === skipA || id === skipB) continue
                var other = pos[id]
                var dx = px - other.x
                var dy = py - other.y
                if (dx * dx + dy * dy < (other.r + 12) * (other.r + 12)) {
                    blocked = true
                    break
                }
            }
            if (!blocked && !page.labelHits(placed, px, py, width)) return { x: px, y: py }
        }
        return { x: 0.25 * start.x + 0.5 * cx + 0.25 * end.x,
                 y: 0.25 * start.y + 0.5 * cy + 0.25 * end.y }
    }

    // 这个位置会不会压到已经放下的标签。
    function labelHits(placed, x, y, width) {
        var left = x - width / 2 - 4
        var top = y - 8
        for (var i = 0; i < (placed || []).length; ++i) {
            var box = placed[i]
            if (left < box.x + box.w && box.x < left + width + 8
                && top < box.y + box.h && box.y < top + 16) return true
        }
        return false
    }

    // 这条弧有没有从别的圈里穿过去。只采样中间段：两端本来就贴着端点。
    function curveIsClear(a, b, cx, cy, pos, skipA, skipB) {
        for (var t = 0.18; t <= 0.83; t += 0.1) {
            var u = 1 - t
            var px = u * u * a.x + 2 * u * t * cx + t * t * b.x
            var py = u * u * a.y + 2 * u * t * cy + t * t * b.y
            for (var id in pos) {
                if (id === skipA || id === skipB) continue
                var other = pos[id]
                var dx = px - other.x
                var dy = py - other.y
                if (dx * dx + dy * dy < (other.r + 8) * (other.r + 8)) return false
            }
        }
        return true
    }

    // 时间点：与「数据浏览」同一套三层——大事件 → 场景 → 事件。
    // 列里放哪些行按 `level` 判断，不按后端算的序号，免得层级错位时串列。
    function momentRows(rank, parentId) {
        var levels = page.moments.levels || []
        var key = rank < levels.length ? levels[rank].key : ""
        var nodes = page.moments.nodes || []
        var out = []
        for (var i = 0; i < nodes.length; ++i) {
            var node = nodes[i]
            if (node.level !== key) continue
            if ((node.parent_id || "") === (parentId || "")) out.push(node)
        }
        return out
    }

    function chooseCut(id, name) {
        page.cutId = id || ""
        page.cutName = name || ""
        page.relatedOnly = page.relatedOnly && page.cutId !== ""
        page.load()
        cutPopup.close()
    }

    Popup {
        id: cutPopup
        objectName: "relationCutPopup"
        modal: true
        padding: 0
        width: Math.min(780, page.width - 80)
        height: Math.min(380, page.height - 120)
        anchors.centerIn: parent
        property string firstId: ""
        property string secondId: ""

        onOpened: {
            var here = page.momentPath(page.cutId)
            firstId = here.storyline
            secondId = here.scene
            if (firstId === "") {
                var tops = page.momentRows(0, "")
                firstId = tops.length > 0 ? tops[0].id : ""
            }
            if (secondId === "") {
                var kids = page.momentRows(1, firstId)
                secondId = kids.length > 0 ? kids[0].id : ""
            }
        }

        background: Rectangle {
            color: page.t.panel
            border.color: page.t.line
            radius: page.t.radius
        }

        contentItem: ColumnLayout {
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 32
                spacing: 8
                Button {
                    text: "最新（读到末尾）"
                    flat: true
                    font.pixelSize: 11
                    onClicked: page.chooseCut("", "")
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: "关闭"
                    flat: true
                    font.pixelSize: 11
                    onClicked: cutPopup.close()
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: page.t.line }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Repeater {
                    model: page.moments.levels || []
                    delegate: Item {
                        required property var modelData
                        required property int index
                        Layout.preferredWidth: index === 0 ? 280 : (index === 1 ? 260 : -1)
                        Layout.fillWidth: index === 2
                        Layout.fillHeight: true

                        ListView {
                            id: cutList
                            objectName: "relationCutLevel" + index
                            anchors.fill: parent
                            anchors.margins: 4
                            clip: true
                            spacing: 2
                            model: index === 0
                                ? page.momentRows(0, "")
                                : page.momentRows(index,
                                                  index === 1 ? cutPopup.firstId : cutPopup.secondId)
                            delegate: ItemDelegate {
                                id: cutRow
                                required property var modelData
                                width: ListView.view.width
                                readonly property bool picked: page.cutId === modelData.id
                                onClicked: {
                                    if (index === 0) cutPopup.firstId = modelData.id
                                    if (index === 1) cutPopup.secondId = modelData.id
                                    page.chooseCut(modelData.id, modelData.name)
                                }
                                background: Rectangle {
                                    radius: 4
                                    color: cutRow.picked ? page.t.accentSoft
                                        : (cutRow.hovered ? page.t.panelAlt : "transparent")
                                }
                                contentItem: Label {
                                    leftPadding: 8
                                    text: (modelData.label_id ? modelData.label_id + "　" : "")
                                        + modelData.name
                                        + (modelData.provisional === true ? "（暂称）" : "")
                                    elide: Text.ElideRight
                                    font.pixelSize: 11
                                    font.italic: modelData.synthetic === true
                                    font.weight: cutRow.picked ? Font.DemiBold : Font.Normal
                                    color: cutRow.picked ? page.t.accent
                                        : (modelData.synthetic === true ? page.t.muted : page.t.body)
                                }
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            width: parent.width - 16
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            visible: cutList.count === 0
                            text: index === 0 ? "还没有大事件"
                                : (index === 1 ? "先选一个大事件" : "先选一个场景")
                            color: page.t.muted
                            font.pixelSize: 10
                        }

                        Rectangle {
                            visible: index < 2
                            anchors.right: parent.right
                            width: 1
                            height: parent.height
                            color: page.t.line
                        }
                    }
                }
            }
        }
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
